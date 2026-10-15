"""Fuzzy de-duplication, within a run and against history."""
from __future__ import annotations

from rapidfuzz import fuzz, process, utils

from .models import IntelItem


def _sim(a: str, b: str, **_kwargs) -> float:
    # process.extractOne passes extra kwargs (e.g. score_cutoff) to custom scorers.
    return fuzz.token_sort_ratio(a, b, processor=utils.default_process)


def dedup_within_run(items: list[IntelItem], threshold: float) -> tuple[list[IntelItem], int]:
    """Keep the highest-scoring item of each near-identical group; record the other sources on it."""
    kept: list[IntelItem] = []
    removed = 0
    for it in sorted(items, key=lambda x: x.score, reverse=True):
        match = None
        for k in kept:
            if k.id == it.id or _sim(k.title, it.title) >= threshold:
                match = k
                break
        if match is None:
            kept.append(it)
            continue
        removed += 1
        if it.source_name != match.source_name and it.source_name not in match.also_reported_by:
            match.also_reported_by.append(it.source_name)
        match.always_include = match.always_include or it.always_include
    return kept, removed


def filter_history(items: list[IntelItem], history: list[dict], seen_ids: set[str],
                   threshold: float) -> tuple[list[IntelItem], int]:
    """Drop items already handled. A near-duplicate of an already *briefed* story is kept and
    flagged via prior_briefings so the LLM can judge whether there is a material update."""
    briefed = [h for h in history if h.get("brief_date")]
    briefed_titles = [h["title"] for h in briefed]
    other_titles = [h["title"] for h in history if not h.get("brief_date")]
    kept: list[IntelItem] = []
    dropped = 0
    for it in items:
        if it.id in seen_ids:
            dropped += 1
            continue
        if briefed_titles:
            m = process.extractOne(it.title, briefed_titles, scorer=_sim, score_cutoff=threshold)
            if m:
                h = briefed[m[2]]
                it.prior_briefings.append({"date": h["brief_date"], "headline": h.get("headline") or h["title"]})
                kept.append(it)
                continue
        if other_titles and process.extractOne(it.title, other_titles, scorer=_sim, score_cutoff=threshold):
            dropped += 1
            continue
        kept.append(it)
    return kept, dropped
