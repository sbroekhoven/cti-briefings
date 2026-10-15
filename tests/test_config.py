import os

import pytest

from src.config import Config, Locations, available_profiles
from src.main import main


def _profile(profiles_dir, name, env_text=None):
    d = profiles_dir / name
    d.mkdir(parents=True)
    (d / "settings.yaml").write_text("paths:\n  database: intel.db\n  briefs_dir: /abs/briefs\n  prompt: prompt.md\n")
    (d / "sources.yaml").write_text("sources: []\n")
    (d / "gating.yaml").write_text("groups: {}\n")
    if env_text:
        (d / ".env").write_text(env_text)
    return d


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake XDG config home: <tmp>/cti-brief/{config.yaml,profiles/}."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    h = tmp_path / "cti-brief"
    (h / "profiles").mkdir(parents=True)
    return h


def test_defaults_live_in_the_xdg_config_home(home):
    loc = Locations.resolve()
    assert loc.config_file == home / "config.yaml" and loc.profiles_dir == home / "profiles"
    assert loc.data_dir is None and loc.env_file == home / ".env"
    assert loc.origin["profiles dir"] == "default" and "not created yet" in loc.origin["config file"]


def test_available_profiles_needs_settings(home):
    _profile(home / "profiles", "b")
    _profile(home / "profiles", "a")
    (home / "profiles" / "not-a-profile").mkdir()
    assert available_profiles(home / "profiles") == ["a", "b"]


@pytest.mark.parametrize("key,flag,env", [("profiles_dir", "profiles_dir", "CTI_PROFILES_DIR"),
                                          ("data_dir", "data_dir", "CTI_DATA_DIR")])
def test_precedence_flag_env_config_default(home, tmp_path, monkeypatch, key, flag, env):
    attr = key
    (home / "config.yaml").write_text(f"{key}: from-config\n")
    assert getattr(Locations.resolve(), attr) == home / "from-config"           # relative to config.yaml
    monkeypatch.setenv(env, str(tmp_path / "from-env"))
    assert getattr(Locations.resolve(), attr) == tmp_path / "from-env"
    loc = Locations.resolve(**{flag: tmp_path / "from-flag"})
    assert getattr(loc, attr) == tmp_path / "from-flag" and loc.origin[key.replace("_", " ")] == f"--{key.replace('_', '-')}"


def test_config_file_location_flag_and_env(tmp_path, monkeypatch):
    alt = tmp_path / "alt" / "my.yaml"
    alt.parent.mkdir()
    alt.write_text("profiles_dir: p\n")
    monkeypatch.setenv("CTI_CONFIG", str(alt))
    assert Locations.resolve().profiles_dir == tmp_path / "alt" / "p"
    other = tmp_path / "other.yaml"
    other.write_text("profiles_dir: q\n")
    assert Locations.resolve(config=other).profiles_dir == tmp_path / "q"


def test_unknown_config_key_is_an_error(home):
    (home / "config.yaml").write_text("profile_dir: typo\n")
    with pytest.raises(ValueError, match="unknown setting.*profile_dir"):
        Locations.resolve()


def test_profile_flag_then_env_then_default_profile(home, monkeypatch):
    _profile(home / "profiles", "private")
    _profile(home / "profiles", "business")
    (home / "config.yaml").write_text("default_profile: business\n")
    assert Config().name == "business"
    monkeypatch.setenv("CTI_PROFILE", "private")
    assert Config().name == "private"
    assert Config("business").name == "business"


def test_no_profile_is_an_error_listing_profiles(home):
    _profile(home / "profiles", "private")
    with pytest.raises(ValueError, match="available: private"):
        Config()


def test_no_profiles_at_all_points_to_init(home):
    with pytest.raises(ValueError, match="--init NAME"):
        Config()


def test_unknown_profile_lists_available(home):
    _profile(home / "profiles", "private")
    with pytest.raises(FileNotFoundError, match="Unknown profile 'nope'.*available: private"):
        Config("nope")


def test_data_paths_use_the_data_dir_config_paths_the_profile(home, tmp_path):
    d = _profile(home / "profiles", "private")
    cfg = Config("private")
    assert cfg.data == d / "data" and cfg.path("paths.database", "x") == d / "data" / "intel.db"
    assert cfg.path("paths.prompt", "prompt.md") == d / "prompt.md"
    assert str(cfg.path("paths.briefs_dir", "x")) == "/abs/briefs"
    cfg = Config("private", locations=Locations.resolve(data_dir=tmp_path / "store"))
    assert cfg.path("paths.database", "x") == tmp_path / "store" / "private" / "intel.db"
    assert cfg.path("paths.prompt", "prompt.md") == d / "prompt.md"


def test_env_precedence_real_then_profile_then_shared(home, monkeypatch):
    (home / ".env").write_text("CTI_TEST_SHARED=shared\nCTI_TEST_PROFILE=shared\nCTI_TEST_REAL=shared\n")
    _profile(home / "profiles", "private", "CTI_TEST_PROFILE=profile\nCTI_TEST_REAL=profile\n")
    monkeypatch.setenv("CTI_TEST_REAL", "real")
    Config("private")
    try:
        assert os.environ["CTI_TEST_SHARED"] == "shared"
        assert os.environ["CTI_TEST_PROFILE"] == "profile"
        assert os.environ["CTI_TEST_REAL"] == "real"
    finally:  # load_dotenv writes os.environ directly; monkeypatch only restores what it set itself
        os.environ.pop("CTI_TEST_SHARED", None)
        os.environ.pop("CTI_TEST_PROFILE", None)


def test_where_reports_locations_and_sources(home, tmp_path, monkeypatch, capsys):
    _profile(home / "profiles", "private")
    (home / "config.yaml").write_text(f"data_dir: {tmp_path / 'store'}\n")
    monkeypatch.setenv("CTI_PROFILE", "private")
    assert main(["--where"]) == 0
    out = capsys.readouterr().out
    assert f"data dir      {tmp_path / 'store'}   (config.yaml)" in out
    assert f"profiles dir  {home / 'profiles'}   (default)" in out
    assert f"database      {tmp_path / 'store' / 'private' / 'intel.db'}" in out
