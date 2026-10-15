from datetime import timedelta

from src import http
from src.sources import build_sources
from src.util import utcnow

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Bank hit by ransomware</title><link>https://example.com/a?utm_source=x</link>
<description>&lt;p&gt;Details here&lt;/p&gt;</description><pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


class FakeResp:
    def __init__(self, content=b"", data=None): self.content, self._data = content, data
    def json(self): return self._data


def test_rss_source_maps_to_common_item(monkeypatch):
    monkeypatch.setattr(http, "get", lambda *a, **k: FakeResp(RSS.encode()))
    src = build_sources([{"id": "t", "type": "rss", "name": "T", "category": "news", "trust": 70,
                          "options": {"url": "https://x/feed"}}], {})[0]
    item = src.fetch(utcnow() - timedelta(days=30))[0]
    assert item.title == "Bank hit by ransomware" and item.summary == "Details here" and item.trust == 70


def test_euvd_source_maps_records(monkeypatch):
    rec = {"id": "EUVD-2026-1", "description": "Remote code execution in Foo.", "baseScore": 9.8,
           "aliases": "CVE-2026-0001\n", "datePublished": "2026-10-05T10:00:00Z"}
    monkeypatch.setattr(http, "get", lambda *a, **k: FakeResp(data=[rec]))
    src = build_sources([{"id": "e", "type": "enisa_euvd", "options": {"feeds": ["exploited"]}}], {})[0]
    item = src.fetch(utcnow())[0]
    assert item.title.startswith("CVE-2026-0001") and item.source_bonus == 30  # 20 exploited + 10 for CVSS >= 9
    assert item.link.endswith("EUVD-2026-1")


def test_unknown_type_fails_loudly():
    import pytest
    with pytest.raises(ValueError):
        build_sources([{"id": "x", "type": "nope"}], {})


def test_cyberint_news_maps_and_authenticates(monkeypatch):
    calls = []
    news = [{"id": "n1", "title": "Bank customers phished", "content": "<p>Campaign details</p>",
             "tags": [{"value": "CVE-2026-0001", "type": "Vulnerability"}, {"value": "Germany", "type": "Country"},
                      {"value": "Finance", "type": "Sector"}], "labels": ["phishing"],
             "source": "https://example.com/story", "created": "2026-10-08T10:00:00Z"},
            {"id": "n2", "title": "No source", "content": "x", "tags": [], "created": "2026-10-08T11:00:00Z"}]

    def fake_post(url, **kw):
        calls.append((url, kw))
        return FakeResp(data={"data": {"news": news, "pagination": {"total_count": 2}}})

    monkeypatch.setenv("CYBERINT_API_TOKEN", "tok")
    monkeypatch.setattr(http, "post", fake_post)
    src = build_sources([{"id": "c", "type": "cyberint_news",
                          "options": {"base_url": "https://t.cyberint.io/", "filters": {"labels": ["leak"]}}}], {})[0]
    a, b = src.fetch(utcnow() - timedelta(days=1))
    url, kw = calls[0]
    assert len(calls) == 1 and url == "https://t.cyberint.io/knowledgebase/external/api/v1/news"
    assert kw["cookies"] == {"access_token": "tok"}
    assert kw["json"]["filters"]["fields"]["labels"] == ["leak"] and "from" in kw["json"]["filters"]["fields"]["date_range"]
    assert a.link == "https://example.com/story" and "CVE-2026-0001" in a.summary and "Campaign details" in a.summary
    assert "Germany" not in a.summary and "Finance" not in a.summary  # region/country/sector tags skipped
    assert b.link == "https://t.cyberint.io/knowledgebase/news/n2" and a.published is not None
