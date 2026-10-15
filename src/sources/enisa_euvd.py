"""ENISA EU Vulnerability Database (EUVD). Public API, no key needed.

Endpoints used (base https://euvdservices.enisa.europa.eu):
  /api/exploitedvulnerabilities, /api/criticalvulnerabilities, /api/lastvulnerabilities (max 8 each)
  /api/vulnerabilities (flexible query: fromScore, toScore, exploited, text, fromDate, toDate, page, size)

options:
  feeds: [exploited, critical, latest]   (default all three)
  query: optional dict of /api/vulnerabilities parameters, e.g. {fromScore: 8, exploited: true}
  bonus_exploited: 20, bonus_critical: 10 (extra score points)
"""
from __future__ import annotations

from datetime import datetime

from .. import http
from ..models import IntelItem
from ..util import parse_date, truncate
from .base import Source, register

BASE = "https://euvdservices.enisa.europa.eu"
FEED_PATHS = {
    "exploited": "/api/exploitedvulnerabilities",
    "critical": "/api/criticalvulnerabilities",
    "latest": "/api/lastvulnerabilities",
}


def _names(entries, key: str) -> list[str]:
    out = []
    for e in entries or []:
        inner = e.get(key) if isinstance(e, dict) else None
        if isinstance(inner, dict) and inner.get("name"):
            out.append(inner["name"])
    return out


@register("enisa_euvd")
class EnisaEuvdSource(Source):
    def fetch(self, since: datetime) -> list[IntelItem]:
        o = self.options
        records: list[tuple[dict, float]] = []
        self._exploited: set[str] = set()
        for feed in o.get("feeds", ["exploited", "critical", "latest"]):
            bonus = float(o.get(f"bonus_{feed}", {"exploited": 20, "critical": 10}.get(feed, 0)))
            batch = self._records(http.get(BASE + FEED_PATHS[feed]).json())
            if feed == "exploited":
                self._exploited.update(r.get("id") for r in batch)
            records += [(r, bonus) for r in batch]
        if o.get("query"):
            params = dict(o["query"])
            params.setdefault("fromDate", since.strftime("%Y-%m-%d"))
            params.setdefault("size", 50)
            records += [(r, 0.0) for r in self._records(http.get(BASE + "/api/vulnerabilities", params=params).json())]

        best: dict[str, tuple[dict, float]] = {}
        for rec, bonus in records:  # same record can appear in several feeds; keep the highest bonus
            rid = rec.get("id")
            if rid and (rid not in best or bonus > best[rid][1]):
                best[rid] = (rec, bonus)
        return [self._to_item(r, b) for r, b in best.values()]

    @staticmethod
    def _records(payload) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("items") or payload.get("content") or []
        return []

    def _to_item(self, r: dict, bonus: float) -> IntelItem:
        aliases = [a.strip() for a in (r.get("aliases") or "").splitlines() if a.strip()]
        cve = next((a for a in aliases if a.upper().startswith("CVE-")), "")
        desc = (r.get("description") or "").strip()
        score = r.get("baseScore")
        if isinstance(score, (int, float)) and score >= 9:
            bonus += 10
        vendors = _names(r.get("enisaIdVendor"), "vendor")
        products = _names(r.get("enisaIdProduct"), "product")
        meta = []
        if score is not None:
            meta.append(f"CVSS {score}")
        if r.get("epss") is not None:
            meta.append(f"EPSS {r['epss']}")
        if vendors:
            meta.append("Vendor: " + ", ".join(vendors[:3]))
        if products:
            meta.append("Product: " + ", ".join(products[:3]))
        label = cve or r["id"]
        return self.item(
            title=f"{label} ({r['id']}): {desc[:110]}",
            link=f"https://euvd.enisa.europa.eu/vulnerability/{r['id']}",
            summary=f"{desc} [{'; '.join(meta)}]" if meta else desc,
            published=parse_date(r.get("datePublished")),
            bonus=bonus,
            details={"cve": cve, "product": ", ".join((vendors[:1] + products[:2])), "description": truncate(desc, 160),
                     "cvss": score, "epss": r.get("epss"), "exploited": r["id"] in self._exploited},
        )
