"""Generic RSS/Atom collector, fully configured from sources.yaml. Also handles Google Alerts feeds."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import feedparser

from .. import http
from ..models import IntelItem
from .base import Source, SourceError, register


def _unwrap_google(link: str) -> str:
    """Google Alerts wraps links in a google.com/url?...&url=REAL redirect."""
    parts = urlsplit(link)
    if parts.netloc.endswith("google.com") and parts.path == "/url":
        real = parse_qs(parts.query).get("url")
        if real:
            return real[0]
    return link


@register("rss", "google_alerts")
class RssSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        url = self.options.get("url")
        if not url:
            raise SourceError("options.url is required")
        resp = http.get(url)
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            raise SourceError(f"Could not parse feed: {feed.bozo_exception}")
        items = []
        for e in feed.entries[: int(self.options.get("max_entries", 50))]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            published = datetime(*t[:6], tzinfo=timezone.utc) if t else None
            title = e.get("title", "").strip()
            link = _unwrap_google(e.get("link", ""))
            if not title or not link:
                continue
            summary = e.get("summary") or e.get("description") or ""
            items.append(self.item(title=title, link=link, summary=summary, published=published))
        return items
