"""Orchestration: collect -> score -> dedup -> filter -> LLM -> validate -> persist -> render -> deliver."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from . import http
from .analysis import build_input, build_prompt, validate
from .config import Config
from .db import Database
from .dedup import dedup_within_run, filter_history
from .delivery import enabled_deliverers
from .llm import get_engine
from .models import IntelItem
from .render import DEFAULT_TITLES, build_context, render, render_html, render_text
from .scoring import Scorer
from .sources import build_sources
from .util import utcnow

log = logging.getLogger("cti")


def collect(sources, since, workers: int) -> tuple[list[IntelItem], dict]:
    stats: dict[str, dict] = {}

    def run_one(src):
        t0 = time.time()
        try:
            items = src.fetch(since)
            stats[src.id] = {"ok": True, "items": len(items), "seconds": round(time.time() - t0, 1)}
            return items
        except Exception as exc:  # one broken source must never stop the run
            log.warning("Source %s failed: %s", src.id, exc)
            stats[src.id] = {"ok": False, "error": str(exc)[:300], "seconds": round(time.time() - t0, 1)}
            return []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(run_one, sources))
    return [i for batch in results for i in batch], stats


def run_pipeline(cfg: Config, *, dry_run: bool = False, with_llm: bool = False, only: list[str] | None = None,
                 since_hours: float | None = None, deliver: bool = True) -> int:
    started = time.time()
    now = utcnow()
    general = cfg.settings.get("general", {})
    lookback = float(since_hours or general.get("lookback_hours", 24))
    limits = cfg.settings.get("limits", {})
    dd = cfg.settings.get("dedup", {})
    http.configure(general.get("user_agent"), general.get("http_timeout_seconds"))
    general = {**general, "lookback_hours": lookback}

    db_path = cfg.path("paths.database", "intel.db")
    had_db = db_path.exists()
    db = Database(db_path, readonly=dry_run)
    if not dry_run and had_db:
        try:
            dest = db.backup(cfg.path("paths.backups_dir", "backups"), int(general.get("backup_keep", 14)))
            log.info("Database backed up to %s", dest)
        except Exception as exc:  # a failed backup must not stop the brief
            log.warning("Database backup failed: %s", exc)
    run_id = None if dry_run else db.start_run(False)
    metrics: dict = {}
    source_stats: dict = {}
    status = "failed"
    try:
        sources = build_sources(cfg.sources, general, only)
        if not sources:
            raise RuntimeError("No sources enabled (check config/sources.yaml)")
        since = now - timedelta(hours=lookback)
        raw, source_stats = collect(sources, since, int(general.get("max_workers", 8)))
        metrics["sources_ok"] = sum(1 for s in source_stats.values() if s["ok"])
        metrics["sources_failed"] = len(source_stats) - metrics["sources_ok"]
        metrics["collected"] = len(raw)
        run_warnings = [] if only else _silent_sources(db, source_stats, SILENT_RUNS)

        items = [i for i in raw if i.title and (i.published is None or i.published >= since)]
        metrics["after_lookback"] = len(items)

        scorer = Scorer(cfg.gating, cfg.settings.get("scoring", {}), lookback)
        for i in items:
            scorer.score(i, now)
        threshold = float(dd.get("title_similarity", 80))
        items, removed = dedup_within_run(items, threshold)
        history = db.recent_items(int(dd.get("history_days", 30)))
        seen = db.existing_ids([i.id for i in items])
        items, dropped = filter_history(items, history, seen, threshold)
        metrics["duplicates_removed"] = removed + dropped

        # News and vulnerability feeds are ranked separately so they never compete for the same slots.
        vcfg = cfg.settings.get("vulnerabilities", {})
        min_score = float(limits.get("min_score", 40))
        news, below = _rank([i for i in items if i.section != "vulnerabilities"], min_score)
        vulns, vulns_below = _rank([i for i in items if i.section == "vulnerabilities"],
                                   float(vcfg.get("min_score", min_score)))
        if vcfg.get("require_match", True):
            relevant = [v for v in vulns if _vuln_relevant(v, vcfg)]
            vulns_below += [v for v in vulns if v not in relevant]
            vulns = relevant
        below += vulns_below
        metrics["below_min_score"] = len(below)
        # Overflow beyond the caps is not persisted, so it can compete again on the next run.
        vuln_table = vulns[: int(vcfg.get("max_items", 15))]
        candidates = news[: int(limits.get("max_candidates_to_llm", 60))]
        # The top vulnerabilities also go to the LLM so it can promote one to ACT.
        candidates += vuln_table[: int(vcfg.get("to_llm", 5))]
        metrics["sent_to_llm"] = len(candidates)
        metrics["vuln_count"] = len(vuln_table)

        if dry_run and not with_llm:
            _print_dry_run(candidates, vuln_table, metrics, source_stats, min_score)
            status = "dry-run"
            return 0
        if not candidates and not vuln_table and not limits.get("send_empty_brief", False):
            log.info("No new candidate items; nothing to brief.")
            metrics.update(act_count=0, signal_count=0)
            if not dry_run:
                db.save_items(below, {i.id: "filtered" for i in below}, run_id)
            status = "success"
            return 0

        max_items = int(limits.get("max_items_reported", 15))
        act_max = min(int(limits.get("act_max", 5)), max_items)
        engine = get_engine(cfg.settings)
        metrics["llm_engine"] = engine.name
        analysis = {"act": [], "signal": [], "near_miss": None, "warnings": [], "merged_ids": set(), "reviewed_ids": set()}
        by_id = {}
        if candidates:
            payload, by_id = build_input(candidates, cfg.gating)
            prompt = build_prompt(cfg.path("paths.prompt", "prompt.md"), payload, act_max=act_max,
                                  signal_max=max_items - act_max, lookback_hours=lookback)
            log.info("Sending %d stories to %s", len(candidates), engine.name)
            analysis = validate(engine.analyze(prompt), by_id, act_max=act_max, max_items=max_items)
        metrics.update(act_count=len(analysis["act"]), signal_count=len(analysis["signal"]))
        analysis["warnings"] = run_warnings + analysis["warnings"]
        # A vulnerability promoted to ACT is not repeated in the table.
        promoted = {e["item"].id for e in analysis["act"]}
        vuln_table = [v for v in vuln_table if v.id not in promoted]
        metrics["vuln_count"] = len(vuln_table)

        reviewed = len({i.id for i in candidates + vuln_table})
        ctx = build_context(analysis, {"reviewed": reviewed, "sources_ok": metrics["sources_ok"]}, vuln_table,
                            tz=general.get("timezone", "UTC"), lookback=lookback, dry_run=dry_run,
                            failed_sources=[k for k, v in source_stats.items() if not v["ok"]], now=now)
        # html is always rendered because email delivery needs it, even when not written to disk.
        ctx["titles"] = brief_titles(cfg)
        html, text = render_html(ctx), render_text(ctx)
        ctx["markdown"] = markdown_settings(cfg)
        suffix = "_dryrun" if dry_run else ""
        formats = cfg.get("paths.brief_formats") or ["html"]
        written = []
        if dry_run:  # previews are HTML only: Markdown belongs in the vault, and test runs don't
            formats = [f for f in formats if f != "md"] or ["html"]
        for fmt in formats:
            name = f"brief_{ctx['filename_stamp']}{suffix}"
            if fmt == "md":
                name = markdown_name(cfg, "brief", ctx["filename_stamp"])
            path = unique_path(output_dir(cfg, fmt, dry_run), name, fmt, ctx["filename_stamp"])
            path.write_text(html if fmt == "html" else render(ctx, fmt), encoding="utf-8")
            written.append(path)
        # The HTML file (or the first format) is the one recorded in the DB and passed to deliverers.
        brief_path = next((p for p in written if p.suffix == ".html"), written[0])
        metrics["brief_file"] = str(brief_path)
        log.info("Brief written: %s (%d act, %d signal)", ", ".join(map(str, written)),
                 len(analysis["act"]), len(analysis["signal"]))
        for w in analysis["warnings"]:
            log.warning(w)

        if dry_run:
            print(f"\nDry run with LLM: preview written to {', '.join(map(str, written))} (nothing saved, nothing sent)")
            status = "dry-run"
            return 0

        # Persist only after a successful analysis so a failed LLM call is retried next run.
        briefed = {e["item"].id: "briefed" for e in analysis["act"] + analysis["signal"]}
        briefed.update({v.id: "briefed" for v in vuln_table})
        considered = list({i.id: i for i in candidates + vuln_table}.values())
        statuses = {i.id: briefed.get(i.id, "merged" if i.id in analysis["merged_ids"] else "reviewed")
                    for i in considered}
        statuses.update({i.id: "filtered" for i in below})
        db.save_items(considered + below, statuses, run_id)
        db.save_briefings(
            [(e["item"].id, "act", e["headline"]) for e in analysis["act"]]
            + [(e["item"].id, "signal", e["line"]) for e in analysis["signal"]]
            + [(v.id, "vuln", v.title) for v in vuln_table], run_id, str(brief_path))

        status = "success"
        if deliver:
            for d in enabled_deliverers(cfg.settings):
                try:
                    d.send(ctx, html, text, str(brief_path))
                    log.info("Delivered via %s", d.name)
                except Exception as exc:
                    log.error("Delivery via %s failed: %s", d.name, exc)
                    status = "partial"
        return 0 if status == "success" else 2
    except Exception as exc:
        log.exception("Run failed")
        metrics["error"] = str(exc)[:500]
        status = "failed"
        if not dry_run and deliver:
            _alert_failure(cfg, exc)
        return 1
    finally:
        metrics["duration_s"] = round(time.time() - started, 1)
        if run_id is not None:
            db.finish_run(run_id, status, metrics, source_stats)
            if status in ("success", "partial"):
                _prune(db, cfg)
        db.close()


SILENT_RUNS = 3  # a source with 0 items in this many earlier real runs (plus this one) gets a warning


def _silent_sources(db: Database, source_stats: dict, runs: int) -> list[str]:
    """Sources that returned nothing (or failed) now and in each of the last `runs` real runs:
    usually a changed feed URL or a dead API key that otherwise goes unnoticed."""
    history = db.recent_source_stats(runs)
    if len(history) < runs:
        return []
    silent = [sid for sid, s in source_stats.items()
              if not (s.get("ok") and s.get("items"))
              and all(sid in h and not (h[sid].get("ok") and h[sid].get("items")) for h in history)]
    return [f"Source {sid} returned no items in the last {runs + 1} runs; check its URL, key or options."
            for sid in silent]


def _prune(db: Database, cfg: Config) -> None:
    days = int(cfg.get("general.retention_days", 0) or 0)
    if days <= 0:
        return
    days = max(days, int(cfg.get("dedup.history_days", 30)))  # never forget what dedup still needs
    try:
        n = db.prune(days)
        if n:
            log.info("Pruned %d items older than %d days", n, days)
    except Exception as exc:
        log.warning("Pruning failed: %s", exc)


def _alert_failure(cfg: Config, exc: Exception) -> None:
    """Tell the team a scheduled run failed; cron output is otherwise only in the cron log."""
    text = (f"The cti-brief run (profile {cfg.name}) at {utcnow():%Y-%m-%d %H:%M} UTC failed, so no brief was "
            f"sent today.\n\nError: {str(exc)[:400]}\n\nSee the cron log and the runs table in "
            f"{cfg.path('paths.database', 'intel.db')}.")
    for d in enabled_deliverers(cfg.settings):
        try:
            d.send_alert(f"{brief_titles(cfg)['short']} run FAILED ({cfg.name})", text)
            log.info("Failure notice sent via %s", d.name)
        except Exception as alert_exc:  # never hide the original error
            log.error("Failure notice via %s failed: %s", d.name, alert_exc)


def brief_titles(cfg: Config) -> dict:
    """Headings for briefs and reviews: settings.yaml `titles` over the CTI defaults."""
    return {**DEFAULT_TITLES, **{k: str(v) for k, v in (cfg.get("titles") or {}).items() if k in DEFAULT_TITLES and v}}


def markdown_settings(cfg: Config) -> dict:
    m = cfg.settings.get("markdown") or {}
    return {"frontmatter": m.get("frontmatter", True), "tags": list(m.get("tags") or ["cti", "cti-brief"])}


def markdown_name(cfg: Config, kind: str, stamp: str) -> str:
    """Note name from markdown.<kind>_filename; stamp is YYYYMMDD_HHMMSS in local time."""
    pattern = cfg.get(f"markdown.{kind}_filename") or f"{kind}_{{stamp}}"
    return pattern.format(date=f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", time=stamp[9:13], stamp=stamp)


def unique_path(folder: Path, name: str, ext: str, stamp: str) -> Path:
    """Never overwrite: a second brief on the same day becomes '<name> <HHMM>' (then '<name> <HHMMSS>')."""
    for candidate in (name, f"{name} {stamp[9:13]}", f"{name} {stamp[9:15]}"):
        path = folder / f"{candidate}.{ext}"
        if not path.exists() or candidate.endswith(stamp[9:15]):
            return path
    return path


def output_dir(cfg: Config, fmt: str, dry_run: bool = False) -> Path:
    """Markdown can go to its own folder (e.g. an Obsidian vault); everything else, and dry runs, to briefs_dir."""
    d = cfg.path("paths.briefs_dir", "briefs")
    if fmt == "md" and not dry_run and cfg.get("markdown.dir"):
        d = cfg.path("markdown.dir", "")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rank(items: list[IntelItem], min_score: float) -> tuple[list[IntelItem], list[IntelItem]]:
    """Split into (eligible sorted by score, below the quiet threshold)."""
    eligible = sorted((i for i in items if i.score >= min_score or i.always_include), key=lambda i: i.score,
                      reverse=True)
    ids = {i.id for i in eligible}
    return eligible, [i for i in items if i.id not in ids]


def _vuln_relevant(item: IntelItem, vcfg: dict) -> bool:
    """Vulnerability feeds are high-volume: only keep items that hit one of the match_groups
    (gating.yaml), or that are known exploited when exploited_always is set."""
    if item.always_include or (vcfg.get("exploited_always", True) and item.details.get("exploited")):
        return True
    groups = set(vcfg.get("match_groups", ["organization", "stack"]))
    return any(t.split(":", 1)[0] in groups for t in item.matched_terms if not t.startswith("-"))


def _print_dry_run(candidates, vuln_table, metrics, source_stats, min_score) -> None:
    print("\nSOURCES")
    for sid, s in source_stats.items():
        print(f"  {'ok  ' if s['ok'] else 'FAIL'} {sid:<28} {s.get('items', 0):>4} items  {s.get('error', '')}")
    print(f"\nCANDIDATES that would go to the LLM ({len(candidates)}), quiet score {min_score:g}")
    for n, i in enumerate(candidates, 1):
        flag = "*" if i.always_include else " "
        print(f"{n:>3}{flag} {i.score:>5.1f}  {i.source_name[:18]:<18} {i.title[:80]}")
        if i.matched_terms:
            print(f"            terms: {', '.join(i.matched_terms[:8])}")
        if i.prior_briefings:
            print(f"            previously briefed: {i.prior_briefings[0]['date']}")
    print(f"\nVULNERABILITIES table ({len(vuln_table)}), top ones above are also shown to the LLM")
    for n, i in enumerate(vuln_table, 1):
        flag = "*" if i.always_include else " "
        print(f"{n:>3}{flag} {i.score:>5.1f}  {i.source_name[:18]:<18} {i.title[:80]}")
    print("\nMETRICS " + ", ".join(f"{k}={v}" for k, v in metrics.items()))
    print("(* = always_include match, bypasses the quiet score. Nothing saved, nothing sent.)")
