"""Generic HTML scraper for sites without a feed. Selectors come from sources.yaml.

options:
  url: page to fetch
  item_selector: CSS selector for one entry (required)
  title_selector: inside the entry (default: the link element)
  link_selector: inside the entry (default: "a")
  summary_selector: optional
  date_selector: optional; date_attr: attribute holding the date (default: text of the element)
  max_items: default 30
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..models import IntelItem
from ..util import parse_date
from .base import Source, SourceError, register


@register("scraper")
class ScraperSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        o = self.options
        if not o.get("url") or not o.get("item_selector"):
            raise SourceError("options.url and options.item_selector are required")
        resp = http.get(o["url"])
        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        for node in soup.select(o["item_selector"])[: int(o.get("max_items", 30))]:
            link_el = node.select_one(o.get("link_selector", "a")) or (node if node.name == "a" else None)
            if link_el is None or not link_el.get("href"):
                continue
            title_el = node.select_one(o["title_selector"]) if o.get("title_selector") else link_el
            title = (title_el or link_el).get_text(" ", strip=True)
            summary = ""
            if o.get("summary_selector"):
                s = node.select_one(o["summary_selector"])
                summary = s.get_text(" ", strip=True) if s else ""
            published = None
            if o.get("date_selector"):
                d = node.select_one(o["date_selector"])
                if d is not None:
                    raw = d.get(o["date_attr"]) if o.get("date_attr") else d.get_text(strip=True)
                    published = parse_date(raw)
            if title:
                items.append(self.item(title=title, link=urljoin(o["url"], link_el["href"]),
                                       summary=summary, published=published))
        return items
