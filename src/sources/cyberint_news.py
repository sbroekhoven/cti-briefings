"""Cyberint (Check Point External Risk Management) knowledge base news. Authenticated JSON API.

Endpoint: POST <base_url>/knowledgebase/external/api/v1/news
  body: {pagination: {page_number, page_size<=100}, sort: [{field: created, sort_order: desc}],
         filters: {mode: or|and, fields: {labels, sectors, regions, date_range: {from, to}}}}
  response: {data: {news: [{id, title, content, tags: [{value, type}], source, labels, created, ...}],
                    pagination: {total_count}}}
Valid labels/sectors/regions: POST <base_url>/knowledgebase/external/api/v1/news/metadata

Auth: the API token from the Cyberint portal, sent as the `access_token` cookie (Cyberint's documented
scheme). Set auth: header to send it as a header instead.

options:
  base_url: https://<tenant>.cyberint.io          (required)
  token_env: CYBERINT_API_TOKEN
  auth: cookie | header                          (default cookie)
  auth_name: access_token                        (cookie or header name)
  auth_prefix: ""                                (e.g. "Bearer " with auth: header)
  filters: {labels: [...], sectors: [...], regions: [...]}   (optional; empty = all news)
  filter_mode: or                                (or | and, how the filter fields combine)
  max_pages: 5                                   (x100 items)
  skip_tag_types: [Region, Country, Sector]      tag types left out of the summary. Cyberint tags broadly,
                                                 so these would trigger region/sector keywords off-topic.
  link_template: "{base_url}/knowledgebase/news/{id}"   used when a news item has no source URL
"""
from __future__ import annotations

from datetime import datetime

from .. import http
from ..models import IntelItem
from ..util import parse_date
from .base import Source, SourceError, register

NEWS_PATH = "/knowledgebase/external/api/v1/news"
PAGE_SIZE = 100  # API maximum
DEFAULT_SKIP_TAG_TYPES = ["Region", "Country", "Sector"]


@register("cyberint_news")
class CyberintNewsSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        o = self.options
        base = (o.get("base_url") or "").rstrip("/")
        if not base:
            raise SourceError("options.base_url is required, e.g. https://<tenant>.cyberint.io")
        token = self.secret(o.get("token_env", "CYBERINT_API_TOKEN"))
        name, prefix = o.get("auth_name", "access_token"), o.get("auth_prefix", "")
        auth = {"headers": {name: f"{prefix}{token}"}} if o.get("auth") == "header" \
            else {"cookies": {name: token}}

        fields = {k: v for k, v in (o.get("filters") or {}).items() if v}
        fields["date_range"] = {"from": since.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        body = {"sort": [{"field": "created", "sort_order": "desc"}],
                "filters": {"mode": o.get("filter_mode", "or"), "fields": fields}}

        items = []
        for page in range(1, int(o.get("max_pages", 5)) + 1):
            body["pagination"] = {"page_number": page, "page_size": PAGE_SIZE}
            data = (http.post(base + NEWS_PATH, json=body, **auth).json() or {}).get("data") or {}
            news = data.get("news") or []
            items += [self._to_item(n, base) for n in news if n.get("title")]
            total = (data.get("pagination") or {}).get("total_count")
            if len(news) < PAGE_SIZE or (total is not None and page * PAGE_SIZE >= total):
                break
        return items

    def _to_item(self, n: dict, base: str) -> IntelItem:
        source = (n.get("source") or "").strip()
        if source.startswith(("http://", "https://")):
            link = source  # original article: lets dedup merge it with the same story from RSS sources
        else:
            link = self.options.get("link_template", "{base_url}/knowledgebase/news/{id}").format(
                base_url=base, id=n.get("id", ""))
        # Tags (CVEs, actors, malware, organizations) and labels go into the summary so keyword scoring
        # can match them. Region/country/sector tags are skipped by default: they are applied broadly.
        meta = []
        if n.get("labels"):
            meta.append("Labels: " + ", ".join(n["labels"]))
        skip = {t.lower() for t in self.options.get("skip_tag_types", DEFAULT_SKIP_TAG_TYPES)}
        tags = [t["value"] for t in n.get("tags") or [] if t.get("value") and (t.get("type") or "").lower() not in skip]
        if tags:
            meta.append("Tags: " + ", ".join(tags[:15]))
        if source and link != source:
            meta.append(f"Source: {source}")
        content = n.get("content") or ""
        summary = f"[{'; '.join(meta)}] {content}" if meta else content
        return self.item(title=n["title"], link=link, summary=summary, published=parse_date(n.get("created")))
