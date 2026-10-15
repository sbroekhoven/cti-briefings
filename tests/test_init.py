import stat

import pytest

from src.config import Config, Locations, init_profile
from src.main import main


@pytest.fixture
def loc(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return Locations.resolve()


def test_init_creates_profile_env_and_config(loc):
    made = init_profile("mine", loc, which=lambda name: f"/opt/bin/{name}")
    prof = loc.profiles_dir / "mine"
    assert made["profile"] == prof
    for f in ("settings.yaml", "sources.yaml", "gating.yaml", "prompt.md", ".env.example"):
        assert (prof / f).is_file(), f
    assert not (prof / "data").exists()
    assert "binary: /opt/bin/claude" in (prof / "settings.yaml").read_text()
    assert stat.S_IMODE(loc.env_file.stat().st_mode) == 0o600 and made["env_file"] == loc.env_file
    assert "# data_dir:" in loc.config_file.read_text()
    cfg = Config("mine", locations=Locations.resolve())
    assert cfg.path("paths.database", "x") == prof / "data" / "intel.db"
    assert cfg.get("delivery.email.enabled") is False and cfg.get("markdown.dir") is None


def test_init_second_profile_keeps_shared_files(loc):
    init_profile("one", loc, which=lambda n: None)
    loc.env_file.write_text("BRAVE_API_KEY=keep\n")
    made = init_profile("two", loc, which=lambda n: None)
    assert made["env_file"] is None and made["config_file"] is None
    assert loc.env_file.read_text() == "BRAVE_API_KEY=keep\n"
    assert "binary: claude" in (loc.profiles_dir / "two" / "settings.yaml").read_text()


def test_init_never_overwrites_and_checks_the_name(loc):
    init_profile("mine", loc, which=lambda n: None)
    with pytest.raises(FileExistsError):
        init_profile("mine", loc, which=lambda n: None)
    for bad in ("Mine", "../x", "a b", ""):
        with pytest.raises(ValueError):
            init_profile(bad, loc, which=lambda n: None)


def test_init_cli_prints_next_steps(loc, capsys):
    assert main(["--init", "mine"]) == 0
    out = capsys.readouterr().out
    assert "Created profile 'mine'" in out and "--profile mine --dry-run" in out and "crontab" in out
    assert main(["--init", "mine"]) == 1
    assert main(["--list-profiles"]) == 0
    assert "mine" in capsys.readouterr().out
