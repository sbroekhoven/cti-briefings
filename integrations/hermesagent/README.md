# Hermes Agent: CTI briefs into the LLM wiki

[Hermes Agent](https://github.com/NousResearch/hermes-agent) reads the Markdown briefs this project writes and,
on request, stores the useful items in the LLM wiki (`~/Obsidian/llm-wiki`). This is done by a Hermes skill,
`cti-brief-to-wiki`, built on top of Hermes' built-in `llm-wiki` skill. The skill and its reminder live in
`~/.hermes/`, not in this repo. This folder documents how it fits together.

The integration needs no code in this project. The Markdown brief **is** the interface.

```
cti-briefings (07:00 + 21:00)       Hermes cron 21:20             You, in chat              Hermes + llm-wiki
─────────────────────────────       ─────────────────             ────────────              ─────────────────
[Private] CTI Brief <date>.md ─▶    "N briefs waiting"   ──▶     "check my CTI      ──▶   fetch originals → raw/articles/
in each profile's markdown.dir      (Discord, only if new)        briefs for wiki"          entities/, concepts/, index, log
                                                                  pick "1,3,5"              mark briefs as reviewed
```

## Where things live

| Path | What it is |
|---|---|
| `~/.hermes/skills/research/cti-brief-to-wiki/SKILL.md` | Instructions Hermes follows: workflow, selection rules, pitfalls |
| `~/.hermes/skills/research/cti-brief-to-wiki/scripts/briefs.py` | Helper script (stdlib only): finds unreviewed briefs, parses items, flags duplicates, keeps state |
| `~/.hermes/scripts/cti-brief-wiki-reminder.py` | Cron wrapper that runs `briefs.py remind` |
| `~/.hermes/plugin-data/cti-brief-to-wiki/state.json` | Reviewed briefs, plus ingested and skipped URLs (created on first use) |
| `~/.hermes/.env`: `CTI_BRIEFS_PATH` | Briefs folders separated by `:`, one per profile: e.g. `…/Personal/CTI/Briefs` and `…/Work/CTI/Briefs`. This is also the default when unset |
| `~/.hermes/skills/research/llm-wiki/SKILL.md` | Built-in skill that owns the wiki path and the ingest rules |
| `~/Obsidian/llm-wiki/SCHEMA.md` | Wiki domain, conventions, tag taxonomy |

## Copies in this folder

For reference and backup, this folder holds copies of the Hermes files. The live versions in `~/.hermes/`
are what Hermes runs. Edit there, then copy back here (or the other way round to reinstall):

```
integrations/hermesagent/
├── README.md
├── skill/cti-brief-to-wiki/          → ~/.hermes/skills/research/cti-brief-to-wiki/
│   ├── SKILL.md
│   └── scripts/briefs.py
└── scripts/cti-brief-wiki-reminder.py → ~/.hermes/scripts/cti-brief-wiki-reminder.py
```

```bash
# Hermes → repo (after changing the live skill)
cp ~/.hermes/skills/research/cti-brief-to-wiki/SKILL.md            integrations/hermesagent/skill/cti-brief-to-wiki/
cp ~/.hermes/skills/research/cti-brief-to-wiki/scripts/briefs.py   integrations/hermesagent/skill/cti-brief-to-wiki/scripts/
cp ~/.hermes/scripts/cti-brief-wiki-reminder.py                    integrations/hermesagent/scripts/

# repo → Hermes (fresh install)
cp -r integrations/hermesagent/skill/cti-brief-to-wiki ~/.hermes/skills/research/
cp integrations/hermesagent/scripts/cti-brief-wiki-reminder.py ~/.hermes/scripts/
echo 'CTI_BRIEFS_PATH=$HOME/Obsidian/Personal/CTI/Briefs:$HOME/Obsidian/Work/CTI/Briefs' >> ~/.hermes/.env
hermes cron create "20 21 * * *" --name "CTI briefs → wiki reminder" \
  --script cti-brief-wiki-reminder.py --no-agent \
  --deliver discord:<chat_id> --failure-deliver discord:<chat_id>
```

Paths assume Hermes lives in `~/.hermes`; the briefs folders come only from `CTI_BRIEFS_PATH`.

## Contract with this project

The helper parses what `src/templates/brief.md.j2` writes. **If you change any of these, update `briefs.py`:**

| This project | Used by the skill as |
|---|---|
| `markdown.dir` in each profile's `settings.yaml` | one entry each in `CTI_BRIEFS_PATH` (`~/.hermes/.env`); the label (e.g. Personal, Work) is the folder two levels above `CTI/Briefs` |
| `markdown.brief_filename` (`Private CTI Brief {date} {time}`, `CTI Brief {date} {time}`) | `BRIEF_GLOB = "*CTI Brief *.md"` (reviews are ignored). File names must differ between profiles, since state is keyed by file name |
| Front matter `type: cti-brief`, `date`, `dry_run` | Which files count as briefs, and what date they cover |
| `## Act (n)` with `### headline`, `**SEVERITY** · action`, `- Relevance: ...`, `- Source · score · [source](url), [link 2](url)` | Act items with severity, relevance, links |
| `## Signal (n)` with `- line ([Source](url))` | Signal items |

Dry runs never write Markdown, so they never reach the wiki.

## Using it

Say to Hermes:
- **"Check my CTI briefs for wiki material"**: Hermes lists candidates from all unreviewed briefs and waits.
  Reply with numbers, `all`, or `none`. Every brief it showed is then marked as reviewed.
- **"Put the YAM Store takedown in the wiki"**: Hermes ingests just that item, without a proposal round.
- **"Skip the CTI brief backlog before 2026-10-15"**: marks older briefs as reviewed without ingesting anything.

**Proposed:** Act items about named actors, groups, malware or crime platforms; law-enforcement operations
(takedowns, arrests, indictments, sanctions); new techniques; vendor research; and any item (Signal too) that
adds to a page the wiki already has.
**Not proposed:** incidents with no named actor, consumer, policy or awareness news, and anything already in the wiki.

The skill writes entity and concept pages only. It does not write dated `news/` digests, because the briefs
already live in the vault.

### Why the wiki gets the original article, not the brief

The brief is an LLM summary and can carry the wrong link. In the 2026-10-14 brief, the FBI/ShinyHunters item
pointed to an unrelated article, because a Brave search result was mixed up with another story. So Hermes always fetches the source,
checks that it matches the summary, and cites only `raw/` files in the wiki. A mismatch is reported and the item is skipped.
Each raw file has a `via: "Private CTI Brief ..."` (or `"CTI Brief ..."`) front matter field, so you can trace where it came from.

Each mismatch is worth a look on this side too (gate or Brave collector), because the brief itself showed a wrong link.

## The reminder

Hermes cron job **"CTI briefs → wiki reminder"**: e.g. daily at 21:20, shortly after an evening brief. Briefs
from other profiles written earlier that day are included in the same reminder. It runs in `no_agent` mode (no
LLM call) and posts to the channel you gave with `--deliver`.

It only sends a message when unreviewed briefs with Act items are waiting **and** that set changed since the last
reminder. Ignoring it for an evening gives one updated message the next evening. Nothing waiting means no message.

```bash
hermes cron list                       # job id and status
hermes cron edit <job id> ...      # e.g. weekly instead: "30 9 * * 6"
hermes cron pause <job id>         # / resume
hermes cron runs <job id>          # execution history
```

## Helper commands

```bash
H=~/.hermes/skills/research/cti-brief-to-wiki/scripts/briefs.py
python3 $H status                      # paths, counts, last reviewed brief
python3 $H pending                     # unreviewed briefs and their items ([already in wiki] flagged)
python3 $H pending --all --json        # everything, structured
python3 $H mark "Private CTI Brief 2026-10-14 2100.md" --ingested <url> --skipped <url>
python3 $H skip-before 2026-10-15      # clear the backlog
python3 $H remind                      # what the cron job prints (updates last_reminded)
```

Set `CTI_WIKI_STATE=/some/tmp.json` to try commands without touching the real state. `WIKI_PATH` overrides the wiki location.

## Troubleshooting

- **No reminder:** check `hermes cron status` (the gateway must be running) and `hermes cron runs <id>`. Then run
  `python3 $H pending` to see whether anything is waiting at all.
- **Items missing or garbled in proposals:** the brief template changed. Compare `brief.md.j2` with `parse_brief()` in `briefs.py`.
- **No briefs found:** check that the `markdown.dir` and `CTI_BRIEFS_PATH` folders match, and that `md` is in `paths.brief_formats`.
- **Start over:** delete `state.json`. Every brief becomes unreviewed again. URLs already in the wiki's `raw/` stay flagged,
  because duplicate detection reads the wiki itself.
