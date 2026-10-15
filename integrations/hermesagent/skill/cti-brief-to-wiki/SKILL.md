---
name: cti-brief-to-wiki
description: "Use when reviewing CTI briefs for items worth adding to the LLM wiki."
version: 1.0.0
author: Sebastian
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [cti, threat-intelligence, wiki, knowledge-base, obsidian]
    category: research
    related_skills: [llm-wiki, obsidian]
---

# CTI briefs into the LLM wiki

The cti-briefings pipeline writes Markdown briefs for two profiles: a **personal** brief every evening at
21:00 ("Private CTI Brief ...") and a **work** brief on weekday mornings at 07:00
("CTI Brief ..."). This skill reviews the briefs from both folders
that have not been reviewed yet, **proposes** the items worth keeping, and after the user picks some,
ingests the **original articles** into the LLM wiki using the `llm-wiki` skill.

This skill handles selection and bookkeeping only. All wiki writing follows `llm-wiki`.

## When This Skill Activates

- "Check my CTI briefs for wiki material", "anything from the briefs for the wiki?"
- The user answers the cron reminder ("CTI briefs waiting for wiki review ...")
- The user names a brief item to store ("put the YAM Store takedown in the wiki")

## Locations

```bash
# Briefs folders, read-only for this skill. CTI_BRIEFS_PATH lists them separated by ':'; default:
BRIEFS="$CTI_BRIEFS_PATH"   # e.g. ~/Obsidian/Personal/CTI/Briefs:~/Obsidian/Work/CTI/Briefs
HELPER=~/.hermes/skills/research/cti-brief-to-wiki/scripts/briefs.py
# Wiki path: owned by llm-wiki (WIKI_PATH or its default). State: ~/.hermes/plugin-data/cti-brief-to-wiki/state.json
```

Always use the helper for finding briefs, parsing items, and keeping state. Do not parse the brief
files by hand or edit state.json directly.

## Workflow

① **Load `llm-wiki`** and do its orientation (SCHEMA.md, index.md, recent log.md).

② **List unreviewed briefs:** `python3 $HELPER pending` (add `--json` if you want structured fields).
   Each brief header shows its date and folder label (e.g. Personal or Work); `--json` has `folder` and `path`.
   Items marked `[already in wiki]` have a link that is already a `source_url` in `raw/`. Leave them out.
   If more than 5 briefs are waiting, ask whether to review them all or clear the backlog with
   `python3 $HELPER skip-before YYYY-MM-DD`.

③ **Select candidates** by applying the rules below to each item. Merge items about the same event
   across briefs (the same story often reappears on later days).

④ **Propose in one message, then stop and wait.** Use a numbered list. For each item give:
   title · brief date and folder · why it is wiki-worthy (one line) · pages it would create or update
   (check index.md and `search_files` first) · source URL.
   End with: `Reply with numbers, "all", or "none".` Also list the items you passed over in one
   short line, so the user can still pick one.

⑤ **Ingest each picked item** using the llm-wiki Ingest operation (use its Bulk Ingest variant for several items):
   - `web_extract` the **original source URL**, not the brief. When an item has several links, prefer the
     primary source (vendor lab, Europol/DoJ/police, the outlet that broke it) over aggregators.
   - **Check that the source matches the brief summary** (same event, same actors). Brief summaries
     can be attached to the wrong link (a search result mixed up with another story). If it does not match,
     do not ingest. Report it as `source mismatch` and record the item as skipped.
   - Save it to `raw/articles/<slug>.md` with llm-wiki raw frontmatter, plus a pointer back to the brief:
     ```yaml
     ---
     source_url: https://...
     ingested: YYYY-MM-DD
     sha256: <body digest>
     via: "Private CTI Brief 2026-10-14 2100"
     ---
     ```
   - Then follow llm-wiki Ingest steps ③ to ⑥: entities (actors, groups, malware, operations, platforms),
     concepts (techniques, crime models), index.md, log.md. The `sources:` frontmatter cites `raw/` files,
     **never the brief**.
   - Create pages only for things this wiki should hold long term (named threat actors, malware families,
     operations or takedowns, techniques). Do **not** write `news/` digests for briefs.

⑥ **Record the outcome** for every brief you showed, including the ones where nothing was picked:
   ```bash
   python3 $HELPER mark "Private CTI Brief 2026-10-14 2100.md" "CTI Brief 2026-10-15 0700.md" \
     --ingested https://... --skipped https://...
   ```
   Pass file names only (no folder); they are unique across both folders. Pass the source URLs as they appear in the brief. If the user answers "none", still mark the briefs as reviewed.

⑦ **Report** every wiki file created or updated, plus any source mismatches.

## Selection Rules

Propose an item when it fits at least one of these:
- An **Act** item about a named threat actor, ransomware or extortion group, malware family, or crime platform
- A **law-enforcement operation**: takedown, arrest, indictment, sanctions, seizure (Europol, Politie/OM, FBI/DoJ, ...)
- A **new or notable technique**, toolset, or crime-as-a-service model
- **Any item (Signal too) that adds facts about an entity that already has a wiki page.** These are the
  highest-value updates, because they build on what the wiki already holds.
- Vendor research (Fox-IT, DFIR Report, Microsoft TI, Unit 42, Talos, ESET, Check Point, ThreatFabric)
  that names actors or tooling

Work briefs lean towards vulnerabilities, patches and the financial sector. Apply the same rules: a
CVE or patch notice on its own is not wiki material, but an actively exploited bug tied to a named actor or
campaign is.

Pass over:
- One-off incidents with no named actor or technique ("company X hit by cyber incident")
- Consumer, product, policy, or awareness news; opinion pieces
- Items marked `[already in wiki]`, or the same event already ingested from another outlet

## Single item on request

When the user names a specific item, skip the proposal step. Find the item in the briefs (`pending --all`),
then do steps ⑤ to ⑦ for that item only. Mark only its URL. Do not mark the whole brief as reviewed
unless the user says so.

## Pitfalls

- **Never edit the briefs**. They belong to the cti-briefings pipeline.
- **Never ingest the brief text as a source.** It is an LLM summary. Sources are the original articles.
- **Never skip the proposal step** in interactive use. The user chose propose-then-confirm.
- No em dashes in prose you write (wiki SCHEMA rule).
- llm-wiki's "ask before touching 10+ existing pages" still applies.
- If `web_extract` fails (paywall, 403), say so and offer the next link of the item. Do not fill gaps
  with the brief summary.
