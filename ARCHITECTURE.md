# Architecture

How cti-brief works inside: the flow of a run, what each module owns, the data model, and the reasons behind the main design choices. For installing and running it, see [README.md](README.md).

## Design principles

- **Plugins, not branches.** Sources, LLM engines, output formats and delivery channels are added as new files, not by editing the pipeline.
- **Code decides, the LLM advises.** Scoring, ranking, dedup and every limit are deterministic code. The LLM only chooses among candidates it was given and writes the wording; its reply is validated before use.
- **Story text is hostile.** Everything fetched from the internet is treated as untrusted data, from the prompt to the LLM's sandbox.
- **One broken part never stops the brief.** A failing source, backup, prune or delivery channel is logged and skipped; only a failure in the core flow fails the run, and that is reported.
- **No secrets in config.** YAML names environment variables; values come from `.env`.

## A run, step by step

`src/main.py` parses the CLI and calls `run_pipeline()` in `src/pipeline.py`:

```text
backup intel.db ─► collect (parallel) ─► lookback filter ─► score ─► dedup in run ─► dedup vs history
                                                                                          │
              ┌───────────────────────────────────────────────────────────────────────────┘
              ▼
   split by section ──► news:  rank ≥ min_score ──► top N candidates ─────────────┐
                    └─► vulns: rank ≥ min_score ─► relevance filter ─► table ──┬──┤ (top to_llm also)
                                                                               │  ▼
                                                     prompt ─► LLM ─► validate ─► ACT / SIGNAL / merges
                                                                               │  │
                                                     render (HTML, Markdown) ◄─┴──┘
                                                              │
                                    persist items + briefings ─► deliver (email, Teams) ─► prune
```

1. **Backup.** On a real run with an existing database, `Database.backup()` copies it to `backups/` first (SQLite online backup, rotation keeps `general.backup_keep`).
2. **Collect.** `collect()` runs every enabled source's `fetch(since)` in a thread pool. A source that raises is recorded as failed in `source_stats`; the others continue.
3. **Lookback filter.** Items published before `now - lookback` are dropped (items without a date are kept).
4. **Score.** `Scorer` (`src/scoring.py`) sets `score`, `score_breakdown` and `matched_terms` on each item. See Scoring.
5. **Dedup in run.** `dedup_within_run()` (`src/dedup.py`) groups items with the same id or a fuzzy-similar title (`rapidfuzz` `token_sort_ratio` ≥ `dedup.title_similarity`), keeps the highest score and records the others in `also_reported_by`.
6. **Dedup against history.** `filter_history()` drops items whose id is already in the database. An item similar to an already *briefed* story is kept but flagged with `prior_briefings`, so the LLM can decide whether it is a material update; similar to a non-briefed story, it is dropped.
7. **Split and rank.** Items from `section: vulnerabilities` sources and all other ("news") items are ranked separately by `_rank()`, each against its own quiet score. Vulnerabilities additionally pass `_vuln_relevant()`: a hit in `vulnerabilities.match_groups`, or a known-exploited flag.
8. **Candidates.** The top `limits.max_candidates_to_llm` news items, plus the top `vulnerabilities.to_llm` table rows, go to the LLM. Items over a cap are *not* saved, so they compete again next run.
9. **LLM.** `build_input()` and `build_prompt()` (`src/analysis.py`) turn candidates into the prompt; the engine returns JSON; `validate()` enforces the rules. See LLM contract.
10. **Render.** `build_context()` (`src/render.py`) builds one context used by every template. Files are written per `paths.brief_formats`.
11. **Persist.** Items are saved with a status; ACT, SIGNAL and table rows are saved as briefings.
12. **Deliver.** Each enabled deliverer sends the brief. A failing channel makes the run `partial` (exit code 2) but does not stop the others.
13. **Finish.** The run row in `runs` gets status, metrics and per-source stats; after a successful run, `prune()` applies retention.

If anything in steps 2–12 raises, the run is marked `failed` (exit code 1) and, on a real run with delivery on, `_alert_failure()` sends a short notice through every enabled channel.

**Dry runs** open the database read-only, write nothing to it and never deliver. Without `--with-llm` they stop after step 8 and print the candidates; with it they render an HTML preview only.

## Modules

| Module | Responsibility |
| --- | --- |
| `main.py` | CLI, `--history` review (`_print_history`, `_review_summary`), `--reset` (`reset_database`) |
| `pipeline.py` | `run_pipeline()` orchestration, ranking, vulnerability relevance, output paths and note names, failure notice, silent-source check, prune |
| `config.py` | `Locations.resolve()`: config file, profiles dir, data dir and shared `.env`, each from flag > `CTI_*` env > `config.yaml` > default (`~/.config/cti-brief`), with the origin kept for `--where`. `Config`: picks a profile (`--profile` > `CTI_PROFILE` > `default_profile`, or `config_dir`), loads its `*.yaml`, then `<profile>/.env` and the shared `.env` (real environment wins); `Config.get("a.b.c")`; `Config.path()` resolves `DATA_KEYS` (database, briefs, backups, markdown.dir) against the data dir (`<profile>/data` or `<data_dir>/<profile>`) and other paths against the profile folder. `init_profile()`: `--init`, copies `templates/profile/` and creates `.env` (600) and `config.yaml` when missing; never overwrites |
| `models.py` | `IntelItem`, URL normalisation, stable item ids |
| `sources/` | one collector per type; `base.py` holds the plugin contract and registry |
| `scoring.py` | keyword groups, trust, category, recency, source bonus |
| `dedup.py` | within-run and history dedup |
| `analysis.py` | prompt input, prompt template filling, validation of the LLM reply |
| `llm/` | engine contract (`base.py`, JSON extraction and retry) and the Claude Code CLI engine |
| `render.py` | template context, Jinja environment and filters, vulnerability rows, plain-text version |
| `templates/` | `brief.html.j2` (browser), `brief.md.j2` (Markdown + front matter), `email.html.j2` (Outlook-safe), `review.*.j2` |
| `delivery/` | `Deliverer` contract, SMTP email, Teams Workflows webhook |
| `db.py` | SQLite schema and migrations, backup, prune, history queries |
| `http.py` | shared HTTP client: user agent, timeout, retries on 429/5xx |
| `util.py` | dates (`utcnow`, `parse_date`), text cleaning and truncation |

## Data model

### IntelItem (`src/models.py`)

Every source, whatever its input, produces `IntelItem`s via `Source.item(...)`:

- From the source: `source_id`, `source_name`, `category`, `trust`, `section`, `title`, `link`, `summary` (cleaned, truncated to `general.summary_max_chars`), `published` (UTC), `source_bonus`, and optional structured `details` (vulnerability feeds fill `cve`, `product`, `description`, `cvss`, `epss`, `exploited`, `ransomware`, `due`).
- From the pipeline: `score`, `score_breakdown`, `matched_terms`, `always_include`, `also_reported_by`, `prior_briefings`.
- `id`: the first 16 hex characters of the SHA-1 of the normalised URL (tracking parameters, `www.`, fragments and trailing slashes removed), or of the title when there is no URL. The same story from the same URL always gets the same id.

### Database (`intel.db`, `src/db.py`)

| Table | Contents |
| --- | --- |
| `items` | every item that reached ranking: id, source, title, link, summary, dates, score, `status`, `matched_terms` (JSON), run |
| `briefings` | one row per item put in a brief: `kind` = `act`, `signal` or `vuln`, the headline, the brief file |
| `runs` | one row per real run: status, timing, counts, error, `source_stats` (JSON per source: ok, items, seconds, error) |

Item status: `briefed` (in ACT, SIGNAL or the vulnerability table), `merged` (the LLM merged it into another story), `reviewed` (seen by the LLM, not chosen), `filtered` (below the quiet score, or an irrelevant vulnerability).

The database is the tool's memory: it prevents repeats (by id and by similar title within `dedup.history_days`) and feeds the review and the silent-source check. Schema changes are additive: `Database._migrate()` adds new columns to existing databases on open.

## Sources (`src/sources/`)

A source is a class with `@register("type")` and `fetch(since) -> list[IntelItem]`. `build_sources()` imports every module in the package, so a new file is enough; `sources.yaml` entries pick a class by `type`. `since` is a hint: the pipeline filters by date again.

| Type | What it does |
| --- | --- |
| `rss` / `google_alerts` | generic feed reader (`feedparser`); Google Alerts unwraps redirect links |
| `scraper` | HTML page with CSS selectors for sites without a feed |
| `cisa_kev` | CISA Known Exploited Vulnerabilities JSON; bonus points, extra for known ransomware use |
| `enisa_euvd` | ENISA EU Vulnerability Database (exploited, critical, latest feeds; optional query) |
| `cyberint_news` | Cyberint knowledge base news (POST, cookie auth, paging); region/country/sector tags skipped |
| `brave_search` | Brave Search news API for configured queries |
| `example_api` | template for an authenticated JSON API |

Sources report problems by raising (`SourceError` for config issues); `self.secret(name)` reads a required environment variable.

## Scoring (`src/scoring.py`)

```text
score = trust × trust_weight + category_bonus[category] + keyword points + recency + source_bonus   (clamped 0..100)
```

- **Keyword points:** for each group in `gating.yaml`, each distinct term that matches adds `weight` (× `title_multiplier` when in the title), capped at the group's `cap`. Negative groups subtract, down to `-cap`. Terms match as whole words, case-insensitive.
- **`always_include`:** a hit sets `item.always_include`, which bypasses the quiet score in ranking.
- **`sections`:** a group with `sections: [...]` only applies to items whose `section` is listed.
- **Recency:** up to `recency_max` points, linear from now to the start of the lookback window.

Scoring only ranks. Nothing is dropped for having no match; the quiet score decides what reaches the LLM.

## LLM contract

**Input** (`build_input`): the profile and keyword groups from `gating.yaml`, and the candidates as `stories` with small integer ids `1..N` (title, summary, url, source, category, section, trust, score, published, matched terms, also_reported_by, prior_briefings). Integer ids mean the model can only refer to stories it was given.

**Prompt:** the profile's `prompt.md` (`paths.prompt`) with `{act_max}`, `{signal_max}`, `{lookback_hours}` filled in, followed by the input as JSON. It asks for a single JSON object: `act`, `signal`, `merges`, `materiality_notes`, `near_miss`.

**Engine** (`src/llm/`): `LLMEngine.analyze()` calls `complete()`, extracts the JSON object (tolerating code fences or prose), and retries `retries` times with a "reply with only JSON" nudge. `ClaudeCodeEngine` pipes the prompt into `claude -p --output-format json` and reads `result` from the envelope.

**Validation** (`validate`), so the model is never trusted:

- unknown, duplicate or merged-away story ids are dropped;
- a story with `prior_briefings` is only allowed with a `materiality_notes` entry;
- ACT is capped at `act_max`, ACT + SIGNAL at `max_items_reported`;
- vulnerability-section stories may be ACT but never SIGNAL (they are in the table);
- headlines and lines are truncated, `action` and `severity` must be known values, links must be http(s) (otherwise the story's own link is used);
- every dropped entry becomes a warning shown under *Notes*.

## Rendering and outputs (`src/render.py`, `src/templates/`)

`build_context()` produces one dictionary (act, signal, vulns, near_miss, warnings, stats, dates, failed sources); every template renders from it, so formats can't drift apart in content.

- **Jinja:** HTML templates (`*.html.j2`) are autoescaped; Markdown is not. Filters: `mdlink` and `mdcell` escape characters that would break Markdown links and tables; `yaml` dumps front matter, with ISO dates unquoted so Obsidian treats them as dates.
- **Paths:** `output_dir()` sends Markdown to `markdown.dir` (on real runs) and everything else to `paths.briefs_dir`. `markdown_name()` builds note names from `markdown.*_filename`; `unique_path()` never overwrites an existing file.
- **Email** uses its own template (`email.html.j2`): tables, inline styles, a fixed 680px width and `bgcolor` attributes, because Outlook on Windows renders with Word's engine and ignores most CSS.
- **Plain text** (`render_text`) is the email's text part.

## Delivery (`src/delivery/`)

`Deliverer` has `send(ctx, html, text, brief_path)` and `send_alert(subject, text)`. Channels are registered in `_CHANNELS` in `delivery/__init__.py` and enabled per `delivery.<name>.enabled`.

- **Email:** SMTP with STARTTLS, SSL or none; recipients from `to`; HTML from `email.html.j2` plus the plain-text part.
- **Teams:** an Adaptive Card 1.4 posted to a Workflows webhook (the old Office 365 connectors are retired), `msteams.width: Full`, item counts capped per section.

## Safety design

| Risk | Mitigation | Where |
| --- | --- | --- |
| Prompt injection reads secrets or uses connectors | Claude runs with `--tools ""` and `--strict-mcp-config` in an empty temp dir | `llm/claude_code.py` |
| Model invents or repeats stories | integer ids, validation, materiality notes | `analysis.py` |
| Database loss | backup before each real run; `--reset` keeps a separate backup | `db.py`, `main.py` |
| Silent failure under cron | failure notice via all channels; failed status and error in `runs` | `pipeline.py` |
| Dead source unnoticed | warning after 4 consecutive empty runs | `pipeline._silent_sources` |
| Unbounded growth | retention, never shorter than the dedup history | `db.prune`, `pipeline._prune` |
| Test runs leaking into real data | dry runs never write the DB, Markdown or deliver; tests redirect every path to a temp dir | `pipeline.py`, `tests/test_pipeline._cfg` |

## Testing

`uv run pytest`. Tests use fixtures instead of the network: `make_item` (`tests/conftest.py`) builds items, and `_cfg()` (`tests/test_pipeline.py`) copies `templates/profile` into `tmp_path`, replaces `collect()` with one fake item and the LLM with `FakeEngine`, and points every path (database, briefs, backups, Markdown) into `tmp_path`. It refuses to run if the database or briefs path is absolute. An autouse fixture in `conftest.py` points `XDG_CONFIG_HOME` at a temp dir and clears the `CTI_*` variables, so no test can read the real `~/.config/cti-brief`. Any test that runs the pipeline should use `_cfg()`, so it can never touch the real database, vault or delivery channels.

| File | Covers |
| --- | --- |
| `test_sources.py` | source parsing (RSS, ENISA, Cyberint) with fake HTTP responses |
| `test_scoring.py`, `test_dedup.py` | scoring rules, section-scoped groups, fuzzy dedup |
| `test_analysis.py` | LLM reply validation |
| `test_pipeline.py` | end-to-end runs, formats, Markdown paths and front matter, vulnerability table, review |
| `test_hardening.py` | Claude lockdown, backup and reset, prune, silent sources, failure notice, SMTP alert |

## Extension points

| To add | Do this |
| --- | --- |
| A source | `src/sources/<name>.py` with `@register("<type>")`; an entry in `sources.yaml` |
| An output format | `src/templates/brief.<fmt>.j2`; add `<fmt>` to `paths.brief_formats` |
| An LLM engine | `src/llm/<name>.py` with `@register_engine("<name>")`; set `llm.engine` |
| A delivery channel | `Deliverer` subclass in `src/delivery/`; add it to `_CHANNELS`; a `delivery.<name>` section |
| A database column | add it to `SCHEMA` and to `Database._migrate()` |
