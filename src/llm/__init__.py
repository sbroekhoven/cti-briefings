from __future__ import annotations

import importlib
import pkgutil

from .base import ENGINES, LLMEngine, LLMError


def get_engine(settings: dict) -> LLMEngine:
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_") and mod.name != "base":
            importlib.import_module(f"{__name__}.{mod.name}")
    llm = settings.get("llm", {})
    name = llm.get("engine", "claude_code")
    if name not in ENGINES:
        raise ValueError(f"Unknown LLM engine {name!r}. Known: {sorted(ENGINES)}")
    return ENGINES[name]((llm.get("engines") or {}).get(name, {}))


__all__ = ["get_engine", "LLMEngine", "LLMError"]
