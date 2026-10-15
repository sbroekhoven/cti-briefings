#!/usr/bin/env python3
"""Helper for the cti-brief-to-wiki skill: find unreviewed CTI briefs, parse their items, keep state.

Stdlib only, so it runs under Hermes' own Python.

  briefs.py pending [--json] [--all]   unreviewed briefs and their items (URLs already in the wiki are flagged)
  briefs.py mark FILE... [--ingested URL ...] [--skipped URL ...]
                                       record that these briefs were reviewed (and what happened to which URL)
  briefs.py skip-before YYYY-MM-DD     mark every brief before that date as reviewed, without ingesting (backlog)
  briefs.py remind                     one-line reminder when new briefs with Act items wait; silent otherwise
  briefs.py status                     paths, counts, last reviewed brief

Paths: CTI_BRIEFS_PATH, WIKI_PATH and CTI_WIKI_STATE override the defaults below. CTI_BRIEFS_PATH may list
several folders separated by ':' (one per cti-briefings profile); each brief is labelled with its folder.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_BRIEFS = "~/Obsidian/Personal/CTI/Briefs:~/Obsidian/Work/CTI/Briefs"   # set CTI_BRIEFS_PATH to yours
BRIEF_DIRS = [Path(d).expanduser() for d in os.environ.get("CTI_BRIEFS_PATH", DEFAULT_BRIEFS).split(os.pathsep) if d]
WIKI = Path(os.environ.get("WIKI_PATH", "~/Obsidian/llm-wiki")).expanduser()
STATE = Path(os.environ.get("CTI_WIKI_STATE", "~/.hermes/plugin-data/cti-brief-to-wiki/state.json")).expanduser()

BRIEF_GLOB = "*CTI Brief *.md"   # "Private CTI Brief ..." and "CTI Brief ..."; reviews never match
LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")


# --- state ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except FileNotFoundError:
        return {"processed": {}, "last_reminded": []}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(STATE)


# --- parsing -------------------------------------------------------------------------

def frontmatter(text: str) -> dict:
    """Flat `key: value` frontmatter; enough for the fields the brief template writes."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    out = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def folder_label(d: Path) -> str:
    """.../Knowledge/Personal/CTI/Briefs -> Personal: the vault area the profile writes to."""
    return d.parent.parent.name if d.name == "Briefs" and d.parent.name == "CTI" else d.name


def brief_files() -> list:
    """(folder label, path) for every brief file in every existing folder."""
    return [(folder_label(d), p) for d in BRIEF_DIRS if d.is_dir() for p in sorted(d.glob(BRIEF_GLOB))]


def parse_brief(path: Path, folder: str = "") -> dict:
    text = path.read_text(encoding="utf-8")
    fm = frontmatter(text)
    items, section, cur = [], None, None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].split(" (")[0].strip().lower()
            cur = None
        elif section == "act" and line.startswith("### "):
            cur = {"section": "act", "title": line[4:].strip(), "severity": None, "action": None,
                   "summary": "", "relevance": None, "source": None, "links": []}
            items.append(cur)
        elif section == "act" and cur is not None:
            s = line.strip()
            m = re.match(r"\*\*(\w+)\*\*\s*·\s*([^·`]+)", s)
            if m and cur["severity"] is None:
                cur["severity"], cur["action"] = m.group(1).lower(), m.group(2).strip()
            elif s.startswith("- Relevance:"):
                cur["relevance"] = s[len("- Relevance:"):].strip()
            elif s.startswith("- ") and LINK_RE.search(s):
                cur["source"] = s[2:].split(" · ")[0].strip()
                cur["links"] = [u for _, u in LINK_RE.findall(s)]
            elif s and not s.startswith("- ") and not cur["summary"]:
                cur["summary"] = s
        elif section == "signal" and line.startswith("- "):
            links = LINK_RE.findall(line)
            if not links:
                continue
            src_name, url = links[-1]
            title = line[2:line.rfind(" ([")].strip() if " ([" in line else line[2:].strip()
            items.append({"section": "signal", "title": title, "severity": None, "action": None,
                          "summary": "", "relevance": None, "source": src_name, "links": [url]})
    return {"file": path.name, "folder": folder, "path": str(path), "date": fm.get("date"), "dry_run": fm.get("dry_run") == "true",
            "type": fm.get("type"), "items": items}


def wiki_source_urls() -> set:
    """source_url values already captured in the wiki's raw/ layer."""
    urls = set()
    raw = WIKI / "raw"
    if raw.is_dir():
        for p in raw.rglob("*.md"):
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            for m in re.finditer(r"^source_url:\s*['\"]?(\S+?)['\"]?\s*$", head, re.M):
                urls.add(m.group(1))
    return urls


def all_briefs() -> list:
    out = []
    for folder, p in brief_files():
        b = parse_brief(p, folder)
        if b["dry_run"] or (b["type"] and b["type"] != "cti-brief"):
            continue
        out.append(b)
    return sorted(out, key=lambda b: (b["date"] or "", b["file"]))  # chronological across folders


def pending_briefs(state: dict, include_all: bool = False) -> list:
    seen = set(wiki_source_urls())
    for rec in state["processed"].values():
        seen.update(rec.get("ingested", []))
    out = []
    for b in all_briefs():
        if not include_all and b["file"] in state["processed"]:
            continue
        for it in b["items"]:
            it["already_in_wiki"] = any(u in seen for u in it["links"])
        out.append(b)
    return out


# --- commands ------------------------------------------------------------------------

def cmd_pending(args) -> int:
    briefs = pending_briefs(load_state(), args.all)
    if args.json:
        print(json.dumps(briefs, indent=2, ensure_ascii=False))
        return 0
    if not briefs:
        print("No unreviewed briefs.")
        return 0
    for b in briefs:
        print(f"## {b['file']}  ({b['date']}, {b['folder']})")
        for i, it in enumerate(b["items"], 1):
            flag = "  [already in wiki]" if it["already_in_wiki"] else ""
            sev = f" {it['severity'].upper()}" if it["severity"] else ""
            print(f"{i:>2}. [{it['section']}{sev}] {it['title']}{flag}")
            if it["relevance"]:
                print(f"    relevance: {it['relevance']}")
            for u in it["links"]:
                print(f"    {u}")
        print()
    return 0


def cmd_mark(args) -> int:
    state = load_state()
    known = {p.name for _, p in brief_files()}
    today = dt.date.today().isoformat()
    for f in args.files:
        name = Path(f).name
        if name not in known:
            print(f"error: no such brief: {name}", file=sys.stderr)
            return 1
        rec = state["processed"].setdefault(name, {"ingested": [], "skipped": []})
        rec["reviewed"] = today
        rec["ingested"] = sorted(set(rec["ingested"]) | set(args.ingested))
        rec["skipped"] = sorted(set(rec["skipped"]) | set(args.skipped))
    save_state(state)
    print(f"Marked {len(args.files)} brief(s) as reviewed.")
    return 0


def cmd_skip_before(args) -> int:
    state = load_state()
    n = 0
    for b in all_briefs():
        if b["date"] and b["date"] < args.date and b["file"] not in state["processed"]:
            state["processed"][b["file"]] = {"reviewed": dt.date.today().isoformat(),
                                             "ingested": [], "skipped": [], "backlog": True}
            n += 1
    save_state(state)
    print(f"Marked {n} brief(s) before {args.date} as reviewed (backlog, nothing ingested).")
    return 0


def cmd_remind(args) -> int:
    """Silent unless there are unreviewed briefs with Act items that were not in the previous reminder."""
    state = load_state()
    briefs = [b for b in pending_briefs(state)
              if any(it["section"] == "act" and not it["already_in_wiki"] for it in b["items"])]
    files = sorted(b["file"] for b in briefs)
    if not files or files == sorted(state.get("last_reminded", [])):
        return 0
    acts = sum(1 for b in briefs for it in b["items"] if it["section"] == "act" and not it["already_in_wiki"])
    dates = sorted(b["date"] for b in briefs if b["date"])
    span = dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"
    print(f"CTI briefs waiting for wiki review: {len(files)} ({span}), {acts} Act items. "
          f"Say \"check my CTI briefs for wiki material\" when you have a moment.")
    state["last_reminded"] = files
    save_state(state)
    return 0


def cmd_status(args) -> int:
    state = load_state()
    briefs = all_briefs()
    done = [b for b in briefs if b["file"] in state["processed"]]
    for d in BRIEF_DIRS:
        mine = [b for b in briefs if Path(b["path"]).parent == d]
        n_done = sum(1 for b in mine if b["file"] in state["processed"])
        found = f"{len(mine)} briefs, {len(mine) - n_done} unreviewed" if d.is_dir() else "MISSING"
        print(f"briefs:  {d}  [{folder_label(d)}]  ({found})")
    print(f"wiki:    {WIKI}  ({len(wiki_source_urls())} raw sources)")
    print(f"state:   {STATE}")
    if done:
        print(f"last reviewed: {max(b['file'] for b in done)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="include briefs already reviewed")
    p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("mark")
    p.add_argument("files", nargs="+")
    p.add_argument("--ingested", nargs="*", default=[])
    p.add_argument("--skipped", nargs="*", default=[])
    p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("skip-before")
    p.add_argument("date")
    p.set_defaults(fn=cmd_skip_before)
    sub.add_parser("remind").set_defaults(fn=cmd_remind)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    missing = [d for d in BRIEF_DIRS if not d.is_dir()]
    if len(missing) == len(BRIEF_DIRS):
        print(f"error: no briefs folder found: {os.pathsep.join(map(str, BRIEF_DIRS))} (set CTI_BRIEFS_PATH)",
              file=sys.stderr)
        return 1
    for d in missing:
        print(f"warning: briefs folder not found, skipped: {d}", file=sys.stderr)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
