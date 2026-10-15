"""Source plugin contract.

To add a source: create src/sources/<name>.py with a class that subclasses Source,
decorate it with @register("<type>") and implement fetch(). It is auto-discovered.
Then reference the type in config/sources.yaml.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..config import env
from ..models import IntelItem
from ..util import clean_text

REGISTRY: dict[str, type["Source"]] = {}


class SourceError(RuntimeError):
    pass


def register(*names: str):
    def deco(cls):
        for n in names:
            REGISTRY[n] = cls
        return cls

    return deco


class Source(ABC):
    def __init__(self, cfg: dict, general: dict):
        self.id: str = cfg["id"]
        self.name: str = cfg.get("name", self.id)
        self.category: str = cfg.get("category", "news")
        self.trust: float = float(cfg.get("trust", 50))
        self.section: str = cfg.get("section", "news")
        self.options: dict = cfg.get("options") or {}
        self.general = general
        self.summary_max: int = int(general.get("summary_max_chars", 600))

    @abstractmethod
    def fetch(self, since: datetime) -> list[IntelItem]:
        """Return items. `since` is a hint; the pipeline filters by date again."""

    # helpers -------------------------------------------------------------
    def item(self, *, title: str, link: str, summary: str = "", published: datetime | None = None,
             bonus: float = 0.0, details: dict | None = None) -> IntelItem:
        return IntelItem(
            source_id=self.id,
            source_name=self.name,
            category=self.category,
            trust=self.trust,
            title=clean_text(title, 300),
            link=link or "",
            summary=clean_text(summary, self.summary_max),
            published=published,
            source_bonus=bonus,
            section=self.section,
            details=details or {},
        )

    def secret(self, env_name: str | None) -> str:
        value = env(env_name)
        if not value:
            raise SourceError(f"Environment variable {env_name!r} is not set (see .env.example)")
        return value
