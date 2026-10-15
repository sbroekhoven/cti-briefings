from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import REGISTRY, Source, SourceError, register

log = logging.getLogger(__name__)
__all__ = ["REGISTRY", "Source", "SourceError", "register", "build_sources"]


def _discover() -> None:
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_") or mod.name == "base":
            continue
        importlib.import_module(f"{__name__}.{mod.name}")


def build_sources(entries: list[dict], general: dict, only: list[str] | None = None) -> list[Source]:
    _discover()
    sources: list[Source] = []
    for entry in entries:
        if only:
            if entry["id"] not in only:
                continue
        elif not entry.get("enabled", True):
            continue
        cls = REGISTRY.get(entry.get("type", ""))
        if cls is None:
            raise ValueError(
                f"Source {entry['id']!r}: unknown type {entry.get('type')!r}. Known: {sorted(REGISTRY)}"
            )
        sources.append(cls(entry, general))
    return sources
