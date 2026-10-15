"""Brave Search API as a source (web or news endpoint). Key comes from .env, never from YAML.

options:
  api_key_env: BRAVE_API_KEY
  endpoint: news | web (default news)
  queries: list of search strings
  freshness: pd (24h) | pw | pm | py (default derived from lookback)
  count: results per query (default 20)
"""
from __future__ import annotations

import time
from datetime import datetime

from .. import http
from ..models import IntelItem
from ..util import parse_date
from .base import Source, SourceError, register

_URLS = {
    "news": "https://api.search.brave.com/res/v1/news/search",
    "web": "https://api.search.brave.com/res/v1/web/search",
}


@register("brave_search")
class BraveSearchSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        o = self.options
        key = self.secret(o.get("api_key_env", "BRAVE_API_KEY"))
        endpoint = o.get("endpoint", "news")
        if endpoint not in _URLS:
            raise SourceError("options.endpoint must be 'news' or 'web'")
        hours = float(self.general.get("lookback_hours", 24))
        freshness = o.get("freshness") or ("pd" if hours <= 24 else "pw" if hours <= 168 else "pm")
        items: list[IntelItem] = []
        for i, query in enumerate(o.get("queries", [])):
            if i:
                time.sleep(1.1)  # free tier allows about 1 request per second
            resp = http.get(
                _URLS[endpoint],
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
                params={"q": query, "count": int(o.get("count", 20)), "freshness": freshness},
            )
            data = resp.json()
            results = data.get("results", []) if endpoint == "news" else (data.get("web") or {}).get("results", [])
            for r in results:
                if r.get("title") and r.get("url"):
                    items.append(self.item(title=r["title"], link=r["url"], summary=r.get("description", ""),
                                           published=parse_date(r.get("page_age"))))
        return items
