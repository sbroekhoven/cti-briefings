"""TEMPLATE for a custom API source (authenticated JSON API). Copy this file and adapt.

It shows the three things a custom API collector usually needs:
  1. a secret read from the environment (.env), never from YAML
  2. calling the API through src.http (timeouts, retries, user agent)
  3. mapping the vendor-specific JSON onto the common IntelItem via self.item()

Example sources.yaml entry (disabled by default):

  - id: my_vendor_api
    type: example_api
    enabled: false
    name: My Vendor Threat API
    category: vendor
    trust: 75
    options:
      url: https://api.example.com/v1/reports
      api_key_env: EXAMPLE_API_KEY
      auth_header: Authorization          # or X-Api-Key
      auth_prefix: "Bearer "              # use "" for plain key headers
      params: {limit: 50}
      items_path: data.items              # dotted path to the list in the response
      field_map: {title: name, link: url, summary: description, published: created_at}
"""
from __future__ import annotations

from datetime import datetime

from .. import http
from ..models import IntelItem
from ..util import parse_date
from .base import Source, SourceError, register


def _dig(data, dotted: str):
    for key in filter(None, dotted.split(".")):
        data = data.get(key) if isinstance(data, dict) else None
    return data


@register("example_api")
class ExampleApiSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        o = self.options
        if not o.get("url"):
            raise SourceError("options.url is required")
        key = self.secret(o.get("api_key_env", "EXAMPLE_API_KEY"))
        headers = {o.get("auth_header", "Authorization"): f"{o.get('auth_prefix', 'Bearer ')}{key}"}
        params = dict(o.get("params") or {})
        params.setdefault("since", since.isoformat())  # only if the API supports it; remove otherwise

        payload = http.get(o["url"], headers=headers, params=params).json()
        records = _dig(payload, o.get("items_path", "")) if o.get("items_path") else payload
        if not isinstance(records, list):
            raise SourceError(f"items_path {o.get('items_path')!r} did not resolve to a list")

        fm = {"title": "title", "link": "url", "summary": "summary", "published": "published",
              **(o.get("field_map") or {})}
        items = []
        for r in records:
            title, link = r.get(fm["title"]), r.get(fm["link"])
            if title and link:
                items.append(self.item(title=title, link=link, summary=r.get(fm["summary"], "") or "",
                                       published=parse_date(r.get(fm["published"]))))
        return items
