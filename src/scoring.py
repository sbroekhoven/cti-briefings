"""Score = trust + category bonus + keyword hits (gating.yaml) + recency + source bonus. Clamped to 0..100."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .models import IntelItem


@dataclass
class Group:
    name: str
    weight: float
    cap: float
    always_include: bool
    patterns: list[tuple[str, re.Pattern]]
    sections: set[str] | None = None  # None = applies to every section


def _compile(term: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.IGNORECASE)


class Scorer:
    def __init__(self, gating: dict, scoring: dict, lookback_hours: float):
        self.cfg = scoring
        self.lookback = max(float(lookback_hours), 1.0)
        self.groups: list[Group] = []
        for name, g in (gating.get("groups") or {}).items():
            terms = [t for t in (g.get("terms") or []) if str(t).strip()]
            self.groups.append(Group(
                name=name,
                weight=float(g.get("weight", 5)),
                cap=float(g.get("cap", 30)),
                always_include=bool(g.get("always_include", False)),
                patterns=[(str(t), _compile(str(t))) for t in terms],
                sections=set(g["sections"]) if g.get("sections") else None,
            ))

    def score(self, item: IntelItem, now: datetime) -> IntelItem:
        c = self.cfg
        title_mult = float(c.get("title_multiplier", 1.5))
        keyword_points = 0.0
        matched: list[str] = []
        for g in self.groups:
            if g.sections is not None and item.section not in g.sections:
                continue
            pts = 0.0
            for term, rx in g.patterns:
                in_title = bool(rx.search(item.title))
                if in_title or rx.search(item.summary):
                    pts += g.weight * (title_mult if in_title else 1.0)
                    if g.weight > 0:
                        matched.append(f"{g.name}:{term}")
                        item.always_include = item.always_include or g.always_include
                    else:
                        matched.append(f"-{term}")
            pts = min(pts, g.cap) if g.weight > 0 else max(pts, -g.cap)
            keyword_points += pts

        base = item.trust * float(c.get("trust_weight", 0.35))
        category = float((c.get("category_bonus") or {}).get(item.category, 0))
        if item.published is not None:
            age_h = max((now - item.published).total_seconds() / 3600, 0)
        else:
            age_h = self.lookback / 2  # unknown date: assume mid-window
        recency = float(c.get("recency_max", 10)) * max(0.0, 1 - age_h / self.lookback)

        total = base + category + keyword_points + recency + item.source_bonus
        item.score = round(max(0.0, min(100.0, total)), 1)
        item.matched_terms = matched
        item.score_breakdown = {
            "trust": round(base, 1), "category": category, "keywords": round(keyword_points, 1),
            "recency": round(recency, 1), "source_bonus": item.source_bonus,
        }
        return item
