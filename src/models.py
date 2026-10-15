"""Common data model. Every source, whatever its input, produces IntelItem."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref", "cmp", "at_")


def normalize_url(url: str) -> str:
    """Strip tracking params, fragments, 'www.' and trailing slashes so the same story hashes equal."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING_PREFIXES)
    )
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), urlencode(query), ""))


def make_id(link: str, title: str) -> str:
    key = normalize_url(link) or title.strip().lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class IntelItem:
    # Required output of every source
    source_id: str
    source_name: str
    category: str  # government | vendor | community | news | vulnerability_db | search
    trust: float  # 0..100, from sources.yaml
    title: str
    link: str
    summary: str = ""
    published: datetime | None = None  # timezone-aware UTC when known
    section: str = "news"  # news | vulnerabilities (sources.yaml); vulnerabilities get their own brief table
    details: dict = field(default_factory=dict)  # optional structured fields, e.g. cve, product, cvss, epss, exploited

    # Filled in by the pipeline
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    matched_terms: list[str] = field(default_factory=list)
    always_include: bool = False
    source_bonus: float = 0.0  # sources may add points (e.g. CISA KEV, CVSS >= 9)
    also_reported_by: list[str] = field(default_factory=list)
    prior_briefings: list[dict] = field(default_factory=list)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_id(self.link, self.title)
