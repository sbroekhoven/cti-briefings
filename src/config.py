from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "profile"   # copied by --init; also what the tests run against

# Paths in settings.yaml that hold data (resolved against the profile's data dir); all others are config
# (resolved against the profile folder).
DATA_KEYS = {"paths.database", "paths.briefs_dir", "paths.backups_dir", "markdown.dir", "hugo.content_dir"}
APP_KEYS = {"profiles_dir", "data_dir", "env_file", "default_profile"}
PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

APP_CONFIG_TEMPLATE = """\
# cti-brief app settings. Everything is optional; uncomment to change a default.
# Precedence for each setting: command-line flag > environment variable > this file > default.
# Relative paths are relative to this file's folder.

# profiles_dir: profiles                 # CTI_PROFILES_DIR, --profiles-dir. One folder per profile.
# data_dir: ~/.local/share/cti-brief     # CTI_DATA_DIR, --data-dir. Unset: <profile>/data. Set: <data_dir>/<profile>
# env_file: .env                         # CTI_ENV_FILE. Shared secrets; <profile>/.env overrides per profile
# default_profile: private               # CTI_PROFILE, --profile. Cron lines should still pass --profile
"""


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "cti-brief"


@dataclass
class Locations:
    """Where profiles, data and shared secrets live, and where each answer came from (for --where)."""
    config_file: Path
    profiles_dir: Path
    data_dir: Path | None            # None: each profile keeps its data in <profile>/data
    env_file: Path
    default_profile: str | None
    origin: dict[str, str] = field(default_factory=dict)

    @classmethod
    def resolve(cls, *, config: Path | None = None, profiles_dir: Path | None = None,
                data_dir: Path | None = None) -> "Locations":
        """First match wins: flag argument > environment variable > config.yaml > default."""
        origin = {}
        if config:
            cfile, origin["config file"] = Path(config).expanduser(), "--config"
        elif os.environ.get("CTI_CONFIG"):
            cfile, origin["config file"] = Path(os.environ["CTI_CONFIG"]).expanduser(), "CTI_CONFIG"
        else:
            cfile, origin["config file"] = config_home() / "config.yaml", "default"
        cfile = cfile.resolve()
        app = _load(cfile) if cfile.exists() else {}
        if not isinstance(app, dict):
            raise ValueError(f"{cfile}: expected key: value settings")
        unknown = set(app) - APP_KEYS
        if unknown:
            raise ValueError(f"{cfile}: unknown setting(s) {', '.join(sorted(unknown))} "
                             f"(allowed: {', '.join(sorted(APP_KEYS))})")
        if not cfile.exists():
            origin["config file"] += ", not created yet"

        def pick(label, flag_value, flag, env, key, default):
            for value, source in ((flag_value, flag), (os.environ.get(env) if env else None, env),
                                  (app.get(key), "config.yaml")):
                if value:
                    origin[label] = source
                    p = Path(value).expanduser()
                    return p if p.is_absolute() or source != "config.yaml" else cfile.parent / p
            origin[label] = "default"
            return default

        loc = cls(
            config_file=cfile,
            profiles_dir=pick("profiles dir", profiles_dir, "--profiles-dir", "CTI_PROFILES_DIR", "profiles_dir",
                              cfile.parent / "profiles").resolve(),
            data_dir=pick("data dir", data_dir, "--data-dir", "CTI_DATA_DIR", "data_dir", None),
            env_file=pick("env file", None, None, "CTI_ENV_FILE", "env_file", cfile.parent / ".env"),
            default_profile=app.get("default_profile"),
            origin=origin,
        )
        if loc.data_dir is not None:
            loc.data_dir = loc.data_dir.resolve()
        return loc

    def profile_data(self, profile_dir: Path) -> Path:
        return self.data_dir / profile_dir.name if self.data_dir else profile_dir / "data"


def available_profiles(profiles_dir: Path) -> list[str]:
    """Subfolders of the profiles dir that hold a settings.yaml."""
    if not profiles_dir.is_dir():
        return []
    return sorted(p.name for p in profiles_dir.iterdir() if (p / "settings.yaml").is_file())


class Config:
    """One profile: a folder with settings.yaml, sources.yaml, gating.yaml and prompt.md. Relative data paths
    (DATA_KEYS) resolve against the profile's data dir, other relative paths against the profile folder."""

    def __init__(self, profile: str | None = None, *, config_dir: Path | None = None,
                 locations: Locations | None = None):
        loc = locations or Locations.resolve()
        if config_dir is not None:
            base = Path(config_dir).expanduser().resolve()
        else:
            name = profile or os.environ.get("CTI_PROFILE") or loc.default_profile
            names = available_profiles(loc.profiles_dir)
            hint = (f"available: {', '.join(names)}" if names
                    else "no profiles yet, create one with --init NAME")
            if not name:
                raise ValueError(f"No profile given: use --profile NAME or set CTI_PROFILE ({hint})")
            base = loc.profiles_dir / name
            if name not in names:
                raise FileNotFoundError(f"Unknown profile '{name}' in {loc.profiles_dir} ({hint})")
        self.locations = loc
        self.base = base
        self.name = base.name
        self.data = loc.profile_data(base)
        # Precedence: real environment > profile .env > shared .env (load_dotenv never overrides what is set).
        load_dotenv(base / ".env")
        load_dotenv(loc.env_file)
        self.settings: dict = _load(base / "settings.yaml")
        self.sources: list[dict] = _load(base / "sources.yaml").get("sources", [])
        self.gating: dict = _load(base / "gating.yaml")

    def get(self, dotted: str, default=None):
        node = self.settings
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def path(self, dotted: str, default: str) -> Path:
        p = Path(self.get(dotted, default)).expanduser()
        if p.is_absolute():
            return p
        return (self.data if dotted in DATA_KEYS else self.base) / p


def init_profile(name: str, loc: Locations, *, template: Path = TEMPLATE, which=shutil.which) -> dict:
    """Create a new profile from the template, plus the shared .env and config.yaml when missing.
    Never overwrites anything. Returns what was created, for the CLI to report."""
    if not PROFILE_NAME.match(name):
        raise ValueError(f"Invalid profile name '{name}': use lowercase letters, digits, '-' and '_'")
    dest = loc.profiles_dir / name
    if dest.exists():
        raise FileExistsError(f"Profile '{name}' already exists: {dest}")
    loc.profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, dest, ignore=shutil.ignore_patterns("data", "__pycache__", ".env"))
    created = {"profile": dest, "claude": which("claude"), "uv": which("uv"), "env_file": None,
               "config_file": None}
    if created["claude"]:  # cron has a minimal PATH, so store the absolute path
        settings = dest / "settings.yaml"
        text = re.sub(r"^(\s*binary:\s*)\S+", lambda m: m.group(1) + created["claude"],
                      settings.read_text(encoding="utf-8"), count=1, flags=re.M)
        settings.write_text(text, encoding="utf-8")
    if not loc.env_file.exists():
        loc.env_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / ".env.example", loc.env_file)
        loc.env_file.chmod(0o600)
        created["env_file"] = loc.env_file
    if not loc.config_file.exists():
        loc.config_file.parent.mkdir(parents=True, exist_ok=True)
        loc.config_file.write_text(APP_CONFIG_TEMPLATE, encoding="utf-8")
        created["config_file"] = loc.config_file
    return created


def env(name: str | None) -> str | None:
    return os.environ.get(name) if name else None
