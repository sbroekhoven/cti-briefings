"""LLM engine contract. Add an engine: new file in src/llm/ with @register_engine("name")."""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)
ENGINES: dict[str, type["LLMEngine"]] = {}


class LLMError(RuntimeError):
    pass


def register_engine(name: str):
    def deco(cls):
        ENGINES[name] = cls
        return cls

    return deco


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences or stray prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("No JSON object found in model output")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid JSON from model: {exc}") from exc


class LLMEngine(ABC):
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.retries = int(cfg.get("retries", 1))

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Send the prompt, return the model's raw text answer."""

    def analyze(self, prompt: str) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            nudge = "" if attempt == 0 else "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object."
            try:
                return extract_json(self.complete(prompt + nudge))
            except LLMError as exc:
                last = exc
                log.warning("LLM attempt %d failed: %s", attempt + 1, exc)
        raise LLMError(str(last))
