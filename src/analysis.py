"""Build the LLM prompt from prompts/*.md + candidate items, and validate the model's answer."""
from __future__ import annotations

import json
from pathlib import Path

from .models import IntelItem
from .util import truncate

ACTIONS = {"patch", "mitigate", "hunt", "investigate", "monitor"}
SEVERITIES = {"critical", "high", "medium", "low"}


def build_input(items: list[IntelItem], gating: dict) -> tuple[dict, dict[int, IntelItem]]:
    """Story ids are small integers 1..N so the model can only refer to what it was given."""
    by_id: dict[int, IntelItem] = {}
    stories = []
    for n, it in enumerate(items, start=1):
        by_id[n] = it
        stories.append({
            "id": n, "title": it.title, "summary": it.summary, "url": it.link, "source": it.source_name,
            "category": it.category, "section": it.section, "trust": it.trust, "score": it.score,
            "published": it.published.isoformat() if it.published else None,
            "matched_terms": it.matched_terms, "also_reported_by": it.also_reported_by,
            "prior_briefings": it.prior_briefings,
        })
    groups = {
        name: {"weight": g.get("weight"), "examples": (g.get("terms") or [])[:12]}
        for name, g in (gating.get("groups") or {}).items()
    }
    profile = {**(gating.get("profile") or {}), "interest_groups": groups}
    return {"profile": profile, "stories": stories}, by_id


def build_prompt(prompt_file: Path, payload: dict, *, act_max: int, signal_max: int, lookback_hours: float,
                 translations: dict[str, str] | None = None) -> str:
    """translations: extra output languages as {code: name}, e.g. {"nl": "Dutch"} (for Hugo posts)."""
    template = prompt_file.read_text(encoding="utf-8")
    for key, val in {"{act_max}": act_max, "{signal_max}": signal_max, "{lookback_hours}": f"{lookback_hours:g}"}.items():
        template = template.replace(key, str(val))
    if translations:
        template += "\n\n" + translation_rule(translations)
    return f"{template}\n\nINPUT:\n```json\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n```\n"


def translation_rule(translations: dict[str, str]) -> str:
    """Added in code rather than in prompt.md, so profiles without extra languages send the same prompt as before."""
    names = ", ".join(f"{name} ({code})" for code, name in translations.items())
    act = ", ".join(f'"{c}": {{"headline": "", "why_it_matters": ""}}' for c in translations)
    sig = ", ".join(f'"{c}": {{"line": ""}}' for c in translations)
    return (f"Translations: also write each ACT headline and why_it_matters, and each SIGNAL line, in {names}. "
            f'Add them to each ACT entry as "translations": {{{act}}} and to each SIGNAL entry as '
            f'"translations": {{{sig}}}. Translate faithfully with the same length limits; keep CVE ids, product, '
            f"vendor and threat actor names unchanged.")


def _valid_id(value, by_id) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n in by_id else None


def _str_list(value) -> list[str]:
    return [str(v) for v in value if v] if isinstance(value, list) else []


def _translations(e: dict, languages, limits: dict[str, int]) -> dict[str, dict[str, str]]:
    """Keep only requested languages and known string fields, truncated like the English ones."""
    raw = e.get("translations")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for lang in languages:
        t = raw.get(lang)
        if isinstance(t, dict):
            fields = {k: truncate(str(t[k]).strip(), n) if n else str(t[k]).strip()
                      for k, n in limits.items() if isinstance(t.get(k), str) and t[k].strip()}
            if fields:
                out[lang] = fields
    return out


def validate(result: dict, by_id: dict[int, IntelItem], *, act_max: int, max_items: int,
             languages: list[str] | tuple = ()) -> dict:
    """Enforce the rules in code, never trust the model: unknown ids, re-mentions without a
    materiality note, over-long text and over-limit counts are all handled here."""
    warnings: list[str] = []
    notes = result.get("materiality_notes") or {}
    notes = {str(k): str(v) for k, v in notes.items() if str(v).strip()} if isinstance(notes, dict) else {}

    merged_away: set[int] = set()
    merges = []
    for pair in result.get("merges") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            keep, dupe = _valid_id(pair[0], by_id), _valid_id(pair[1], by_id)
            if keep and dupe and keep != dupe:
                merges.append((keep, dupe))
                merged_away.add(dupe)

    used: set[int] = set()
    act = []
    for e in result.get("act") or []:
        sid = _valid_id(e.get("story_id") if isinstance(e, dict) else None, by_id)
        if sid is None or sid in used or sid in merged_away:
            warnings.append(f"Dropped ACT entry with invalid, duplicate or merged story_id: {e!r:.80}")
            continue
        item = by_id[sid]
        if item.prior_briefings and str(sid) not in notes:
            warnings.append(f"Dropped re-mention of story {sid} without materiality note")
            continue
        if len(act) >= act_max:
            warnings.append(f"Dropped ACT entry {sid}: over act_max")
            continue
        action = str(e.get("action", "monitor")).lower()
        severity = str(e.get("severity", "medium")).lower()
        links = [l for l in _str_list(e.get("links")) if l.startswith(("http://", "https://"))] or [item.link]
        used.add(sid)
        act.append({
            "story_id": sid, "item": item,
            "headline": truncate(str(e.get("headline", item.title)), 90),
            "why_it_matters": str(e.get("why_it_matters", "")).strip(),
            "relevance_refs": _str_list(e.get("relevance_refs")),
            "cves": _str_list(e.get("cves")),
            "action": action if action in ACTIONS else "monitor",
            "severity": severity if severity in SEVERITIES else "medium",
            "links": links, "materiality_note": notes.get(str(sid)),
            "translations": _translations(e, languages, {"headline": 90, "why_it_matters": 0}),
        })

    signal = []
    for e in result.get("signal") or []:
        sid = _valid_id(e.get("story_id") if isinstance(e, dict) else None, by_id)
        if sid is None or sid in used or sid in merged_away:
            continue
        item = by_id[sid]
        if item.prior_briefings and str(sid) not in notes:
            continue
        if item.section == "vulnerabilities":  # already listed in the vulnerability table
            continue
        if len(act) + len(signal) >= max_items:
            break
        used.add(sid)
        signal.append({"story_id": sid, "item": item, "line": truncate(str(e.get("line", item.title)), 140),
                       "translations": _translations(e, languages, {"line": 140})})

    missing = {lang: [e["story_id"] for e in act + signal if lang not in e["translations"]] for lang in languages}
    warnings += [f"No {lang} translation for story {', '.join(map(str, ids))}; the English text is used"
                 for lang, ids in missing.items() if ids]

    near = _valid_id(result.get("near_miss"), by_id)
    return {
        "act": act, "signal": signal, "merges": merges, "near_miss": by_id[near] if near and near not in used else None,
        "reviewed_ids": {by_id[i].id for i in by_id}, "merged_ids": {by_id[d].id for d in merged_away},
        "warnings": warnings,
    }
