from datetime import timedelta

import pytest

from src.models import IntelItem
from src.util import utcnow


@pytest.fixture(autouse=True)
def _isolated_locations(tmp_path_factory, monkeypatch):
    """Never read the user's real ~/.config/cti-brief or CTI_* settings during tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    for name in ("CTI_CONFIG", "CTI_PROFILES_DIR", "CTI_DATA_DIR", "CTI_ENV_FILE", "CTI_PROFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_item():
    def _make(title, *, summary="", link=None, trust=70, category="news", hours_old=1, source="Src"):
        return IntelItem(source_id=source.lower(), source_name=source, category=category, trust=trust,
                         title=title, link=link or f"https://example.com/{abs(hash(title))}", summary=summary,
                         published=utcnow() - timedelta(hours=hours_old))
    return _make
