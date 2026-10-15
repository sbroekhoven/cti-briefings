"""CLI entry point:  uv run python -m src.main [options]"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT, Config, Locations, available_profiles, init_profile
from .db import Database
from .pipeline import markdown_name, markdown_settings, output_dir, run_pipeline, unique_path
from .render import render
from .sources import REGISTRY, build_sources

STATUSES = ["briefed", "merged", "reviewed", "filtered"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cti-brief", description="Collect, score and brief cyber threat intel.")
    p.add_argument("--dry-run", action="store_true",
                   help="Collect, score and dedup only. No LLM call, no DB writes, no delivery.")
    p.add_argument("--with-llm", action="store_true",
                   help="With --dry-run: also call the LLM and write a *_dryrun.html preview (still no DB writes or delivery).")
    p.add_argument("--no-deliver", action="store_true", help="Real run, but skip email/Teams (HTML file and DB still written).")
    p.add_argument("--since-hours", type=float, help="Override general.lookback_hours.")
    p.add_argument("--source", action="append", metavar="ID", help="Run only this source id (repeatable, ignores enabled flag).")
    p.add_argument("--list-sources", action="store_true", help="List configured sources and exit.")
    p.add_argument("--history", nargs="?", type=float, const=7, metavar="DAYS",
                   help="Review past items from the database (default: last 7 days), print them and write "
                        "review_<stamp>.html and .md (see markdown.dir). "
                        "Combine with --status, --search and --source.")
    p.add_argument("--status", action="append", choices=STATUSES,
                   help="With --history: only items with this status (repeatable).")
    p.add_argument("--search", metavar="TEXT", help="With --history: match title, summary or briefed headline.")
    p.add_argument("--limit", type=int, default=1000, help="With --history: maximum items (default 1000).")
    p.add_argument("--reset", action="store_true",
                   help="Start over: back up the profile's intel.db to backups/reset_intel_<stamp>.db, remove it, "
                        "then run normally on an empty database. Briefs and notes are kept. Asks for confirmation.")
    p.add_argument("--yes", action="store_true", help="With --reset: skip the confirmation (for scripts).")
    g = p.add_argument_group("profiles and locations (flag > environment variable > config.yaml > default)")
    g.add_argument("--profile", metavar="NAME",
                   help="Profile to run (a folder in the profiles dir). Default: $CTI_PROFILE, then default_profile.")
    g.add_argument("--init", metavar="NAME", help="Create a new profile from the template, then print next steps.")
    g.add_argument("--list-profiles", action="store_true", help="List available profiles and exit.")
    g.add_argument("--where", action="store_true", help="Show where config, profiles, data and secrets are read from.")
    g.add_argument("--config", type=Path, metavar="FILE",
                   help="App config file ($CTI_CONFIG, default ~/.config/cti-brief/config.yaml).")
    g.add_argument("--profiles-dir", type=Path, metavar="DIR",
                   help="Folder with one subfolder per profile ($CTI_PROFILES_DIR, config.yaml profiles_dir).")
    g.add_argument("--data-dir", type=Path, metavar="DIR",
                   help="Data goes to DIR/<profile> ($CTI_DATA_DIR, config.yaml data_dir). Default: <profile>/data.")
    g.add_argument("--config-dir", type=Path, metavar="DIR",
                   help="Use this folder as the profile, bypassing the profiles dir (e.g. a test copy).")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    try:
        loc = Locations.resolve(config=args.config, profiles_dir=args.profiles_dir, data_dir=args.data_dir)
    except ValueError as exc:
        p.error(str(exc))
    if args.init:
        return _init(loc, args.init)
    if args.list_profiles:
        names = available_profiles(loc.profiles_dir)
        print("\n".join(names) if names else "No profiles yet: create one with --init NAME")
        print(f"\n(profiles dir: {loc.profiles_dir})")
        return 0
    if args.where:
        return _where(loc, args)
    try:
        cfg = Config(args.profile, config_dir=args.config_dir, locations=loc)
    except (ValueError, FileNotFoundError) as exc:
        p.error(str(exc))
    level = "DEBUG" if args.verbose else cfg.get("general.log_level", "INFO")
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("cti-brief").info("Profile: %s (%s, data in %s)", cfg.name, cfg.base, cfg.data)

    if args.list_sources:
        build_sources([], {})  # triggers plugin discovery
        print(f"Source types: {', '.join(sorted(REGISTRY))}\n")
        for s in cfg.sources:
            print(f"{'on ' if s.get('enabled', True) else 'off'} {s['id']:<28} {s.get('type'):<14} "
                  f"trust={s.get('trust', 50):<3} {s.get('category', '')}")
        return 0

    if args.history is not None:
        return _print_history(cfg, args)

    if args.reset:
        if args.dry_run:
            p.error("--reset cannot be combined with --dry-run (a dry run never writes a database)")
        if not reset_database(cfg, confirm=not args.yes):
            return 1

    return run_pipeline(cfg, dry_run=args.dry_run, with_llm=args.with_llm, only=args.source,
                        since_hours=args.since_hours, deliver=not args.no_deliver)


def _where(loc: Locations, args) -> int:
    o = loc.origin
    rows = [("config file", loc.config_file, o["config file"]), ("profiles dir", loc.profiles_dir, o["profiles dir"]),
            ("data dir", loc.data_dir or "<profile>/data", o["data dir"]), ("env file", loc.env_file, o["env file"])]
    name = args.profile or os.environ.get("CTI_PROFILE") or loc.default_profile
    if name or args.config_dir:
        try:
            cfg = Config(name, config_dir=args.config_dir, locations=loc)
            rows += [("profile", cfg.base, cfg.name), ("profile data", cfg.data, ""),
                     ("database", cfg.path("paths.database", "intel.db"), ""),
                     ("prompt", cfg.path("paths.prompt", "prompt.md"), "")]
        except (ValueError, FileNotFoundError) as exc:
            rows.append(("profile", name, f"error: {exc}"))
    for label, value, source in rows:
        print(f"{label:<13} {value}" + (f"   ({source})" if source else ""))
    return 0


def _init(loc: Locations, name: str) -> int:
    try:
        made = init_profile(name, loc)
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    prof = made["profile"]
    print(f"Created profile '{name}' in {prof}")
    for key, what in (("env_file", "shared secrets (mode 600)"), ("config_file", "app settings (all commented out)")):
        if made[key]:
            print(f"Created {what}: {made[key]}")
    if not made["claude"]:
        print("warning: 'claude' not found on PATH; set llm.engines.claude_code.binary in settings.yaml "
              "to its absolute path before running from cron")
    uv = made["uv"] or "uv"
    sel = f"--profile {name}" + (f" --profiles-dir {loc.profiles_dir}" if loc.origin["profiles dir"] == "--profiles-dir" else "")
    print(f"""
Next steps:
  1. Add API keys and the SMTP password to {loc.env_file}
  2. Edit the profile in {prof}/:
       gating.yaml    who you are and which keywords matter (start here)
       sources.yaml   which feeds and APIs to use
       settings.yaml  limits, Markdown folder, email/Teams (off by default)
       prompt.md      the instructions for the LLM
  3. Try it:
       uv run python -m src.main {sel} --dry-run              # collect and score, writes nothing
       uv run python -m src.main {sel} --dry-run --with-llm   # preview brief in {loc.profile_data(prof) / 'briefs'}
       uv run python -m src.main {sel} --no-deliver           # first real run, no email/Teams
  4. Schedule it (crontab -e), e.g. daily at 07:00:
       0 7 * * *  cd {ROOT} && {uv} run python -m src.main {sel} >> {loc.profile_data(prof) / 'logs' / 'cron.log'} 2>&1
     (create the logs folder first: mkdir -p {loc.profile_data(prof) / 'logs'})""")
    return 0


def _print_history(cfg: Config, args) -> int:
    path = cfg.path("paths.database", "intel.db")
    if not path.exists():
        print(f"No database yet at {path}")
        return 0
    db = Database(path, readonly=True)
    try:
        rows = db.search_items(args.history, statuses=args.status, sources=args.source, text=args.search,
                               limit=args.limit)
    finally:
        db.close()
    tz = ZoneInfo(cfg.get("general.timezone", "UTC"))
    for r in rows:
        seen = datetime.fromisoformat(r["first_seen"]).astimezone(tz)
        r.update(day=seen.strftime("%Y-%m-%d"), time=seen.strftime("%H:%M"),
                 label=f"{r['status']}/{r['kind']}" if r["kind"] else r["status"],
                 terms=json.loads(r.get("matched_terms") or "[]"))
        print(f"{r['day']} {r['time']}  {r['label']:<15} {r['score'] or 0:5.1f}  {r['source_id']:<18} {r['title']}")
        if r["headline"] and r["headline"] != r["title"]:
            print(f"{'':<42}brief: {r['headline']}")
        print(f"{'':<42}{r['link']}")
    limit_reached = len(rows) == args.limit
    print(f"\n{len(rows)} item(s) in the last {args.history:g} day(s)"
          + (" (limit reached, narrow the filters)" if limit_reached else ""))

    now = datetime.now(tz)
    filters = [f"status: {', '.join(args.status)}" if args.status else "",
               f"source: {', '.join(args.source)}" if args.source else "",
               f'search: "{args.search}"' if args.search else ""]
    ctx = {"rows": rows, "by_day": [(d, list(g)) for d, g in groupby(rows, key=lambda r: r["day"])],
           **_review_summary(rows), "min_score": cfg.get("limits.min_score", 40),
           "days": f"{args.history:g}", "filters": " · ".join(f for f in filters if f),
           "limit_reached": limit_reached, "date": now.strftime("%Y-%m-%d"),
           "generated": now.strftime("%Y-%m-%d %H:%M %Z")}
    ctx.update(generated_iso=now.strftime("%Y-%m-%dT%H:%M:%S"), markdown=markdown_settings(cfg))
    stamp = now.strftime("%Y%m%d_%H%M%S")
    for fmt in ("html", "md"):
        name = markdown_name(cfg, "review", stamp) if fmt == "md" else f"review_{stamp}"
        out = unique_path(output_dir(cfg, fmt), name, fmt, stamp)
        out.write_text(render(ctx, fmt, "review"), encoding="utf-8")
        print(f"Review written: {out}")
    return 0

def reset_database(cfg: Config, *, confirm: bool = True) -> bool:
    """Back up and remove intel.db so the next run starts with no history. Returns False when cancelled."""
    path = cfg.path("paths.database", "intel.db")
    if not path.exists():
        print(f"No database at {path}; nothing to reset.")
        return True
    if confirm:
        if not sys.stdin.isatty():
            print("--reset needs confirmation; add --yes when running non-interactively.")
            return False
        answer = input(f"This removes all history of profile '{cfg.name}' in {path} (a backup is kept). "
                       f"Type 'reset' to continue: ")
        if answer.strip() != "reset":
            print("Cancelled.")
            return False
    backups = cfg.path("paths.backups_dir", "backups")
    backups.mkdir(parents=True, exist_ok=True)
    # reset_*.db is outside the intel_*.db rotation, so it is never deleted automatically.
    dest = backups / f"reset_intel_{datetime.now():%Y%m%d_%H%M%S}.db"
    db = Database(path, readonly=True)
    try:
        target = sqlite3.connect(dest)
        try:
            db.conn.backup(target)
        finally:
            target.close()
    finally:
        db.close()
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    print(f"Database backed up to {dest} and removed; starting from scratch.")
    return True


def _review_summary(rows: list[dict]) -> dict:
    """Per-source status counts and keyword hit counts: the numbers you need to tune gating.yaml."""
    by_source: dict[str, dict] = {}
    for r in rows:
        s = by_source.setdefault(r["source_id"], {"source": r["source_id"], "total": 0, "score_sum": 0.0,
                                                  **{st: 0 for st in STATUSES}})
        s["total"] += 1
        s["score_sum"] += r["score"] or 0
        s[r["status"]] = s.get(r["status"], 0) + 1
    sources = sorted(by_source.values(), key=lambda s: -s["total"])
    for s in sources:
        s["avg_score"] = round(s.pop("score_sum") / s["total"], 1)
    terms = Counter(t for r in rows for t in r["terms"])
    briefed_terms = Counter(t for r in rows if r["status"] == "briefed" for t in r["terms"])
    return {"statuses": STATUSES, "by_source": sources,
            "status_totals": {st: sum(s[st] for s in sources) for st in STATUSES},
            "top_terms": [{"term": t, "hits": n, "briefed": briefed_terms[t]} for t, n in terms.most_common(40)],
            "with_terms": sum(1 for r in rows if r.get("matched_terms") is not None)}


if __name__ == "__main__":
    sys.exit(main())
