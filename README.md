# cti-brief

Modular cyber threat intelligence collector and daily brief generator. It pulls items from RSS feeds, APIs (CISA KEV, ENISA EUVD, Cyberint, Brave Search), scraped sites and search, scores them against your own gating keywords and source trust, de-duplicates them (fuzzy, and against history), lets an LLM (Claude Code CLI) pick what matters, and writes a brief:

- **Act**: items that need action (patch, mitigate, hunt, investigate, monitor), with severity and why it matters.
- **Signal**: one-liners worth knowing.
- **Vulnerabilities**: a table from the vulnerability feeds, built without the LLM.

Output: HTML, Markdown with front matter (e.g. into an Obsidian vault), email and Microsoft Teams. A weekly review shows everything the tool saw, for tuning the gate. Run several **profiles** side by side (say a work brief and a personal one), each with its own sources, keywords, prompt and history.

How it works inside: see [ARCHITECTURE.md](ARCHITECTURE.md).

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/) and the [Claude Code](https://claude.com/claude-code) CLI, installed and logged in.

```bash
git clone https://github.com/sbroekhoven/cti-briefings && cd cti-briefings
uv sync                                          # creates .venv and installs dependencies
uv run python -m src.main --init mine            # creates the profile "mine" in ~/.config/cti-brief/profiles/
```

`--init` copies the template from `templates/profile/`. If they don't exist yet, it also creates the shared secrets file `~/.config/cti-brief/.env` (mode 600) and a commented-out `~/.config/cti-brief/config.yaml`. It stores the absolute path of `claude` in the new profile, and prints the next steps. It never overwrites anything.

Then:

1. Add your API keys to `~/.config/cti-brief/.env` (`BRAVE_API_KEY`, `SMTP_PASSWORD`, ...). A source whose key is missing fails with a clear message, and the run continues without it. Set `enabled: false` in `sources.yaml` for the ones you don't use.
2. Edit the profile in `~/.config/cti-brief/profiles/mine/`. Start with `gating.yaml` (who you are, which keywords matter), then `sources.yaml`, `settings.yaml` and `prompt.md`.
3. Try it:

```bash
uv run python -m src.main --profile mine --dry-run              # collect, score, dedup; writes nothing
uv run python -m src.main --profile mine --dry-run --with-llm   # also call the LLM; writes a *_dryrun.html preview
uv run python -m src.main --profile mine --no-deliver           # first real run: database + brief, no email/Teams
```

The template starts safe: email and Teams are off, and Markdown goes next to the HTML instead of into a vault. Turn them on in `settings.yaml` once the brief looks right, then schedule it (see [Cron](#cron)).

## Where things live

The code lives in the checkout. Everything that is yours lives outside it, so `git pull` never touches your profiles and the repo never contains them.

```text
~/.config/cti-brief/
  config.yaml                 # optional app settings (see below)
  .env                        # shared secrets for all profiles
  profiles/
    mine/
      settings.yaml  sources.yaml  gating.yaml  prompt.md
      .env                    # optional: overrides the shared .env for this profile only
      data/                   # created on the first run
        intel.db  briefs/  backups/  logs/
```

Each profile is self-contained: copy, move, back up or delete it as one folder. Markdown briefs go to `markdown.dir` (e.g. your Obsidian vault) when set.

**Changing the locations.** For each setting the first match wins: command-line flag, then environment variable, then `config.yaml`, then the default.

| Setting | Flag | Environment | `config.yaml` | Default |
| --- | --- | --- | --- | --- |
| App config file | `--config FILE` | `CTI_CONFIG` | | `$XDG_CONFIG_HOME/cti-brief/config.yaml` (`~/.config/...`) |
| Profiles folder | `--profiles-dir DIR` | `CTI_PROFILES_DIR` | `profiles_dir` | `profiles/` next to `config.yaml` |
| Data folder | `--data-dir DIR` | `CTI_DATA_DIR` | `data_dir` | `<profile>/data` |
| Shared secrets | | `CTI_ENV_FILE` | `env_file` | `.env` next to `config.yaml` |
| Profile | `--profile NAME` | `CTI_PROFILE` | `default_profile` | none: the tool stops and lists the profiles |

```yaml
# ~/.config/cti-brief/config.yaml: relative paths are relative to this file's folder
profiles_dir: profiles
data_dir: ~/.local/share/cti-brief      # data then goes to ~/.local/share/cti-brief/<profile>/
default_profile: mine
```

With `data_dir` set, each profile gets its own subfolder, so profiles never share a database. To move existing data there, use `mv ~/.config/cti-brief/profiles/mine/data ~/.local/share/cti-brief/mine`. An unknown key in `config.yaml` is an error, so a typo can't silently fall back to a default.

**Check what is in effect** with `--where`. It shows each location and where it came from:

```text
$ uv run python -m src.main --where --profile mine
config file   /home/you/.config/cti-brief/config.yaml   (default)
profiles dir  /home/you/.config/cti-brief/profiles   (default)
data dir      <profile>/data   (default)
env file      /home/you/.config/cti-brief/.env   (default)
profile       /home/you/.config/cti-brief/profiles/mine   (mine)
profile data  /home/you/.config/cti-brief/profiles/mine/data
database      /home/you/.config/cti-brief/profiles/mine/data/intel.db
prompt        /home/you/.config/cti-brief/profiles/mine/prompt.md
```

## Profiles

- **More briefs:** `--init work`, `--init personal` and so on. Each has its own sources, keywords, prompt, schedule, database and history. They share only the code and the brief templates in `src/templates/`.
- **Picking one:** `--profile NAME` per command, `export CTI_PROFILE=NAME` per shell, or `default_profile` in `config.yaml`. In cron, always pass `--profile`.
- **Listing:** `--list-profiles`.
- **One-off folder:** `--config-dir DIR` runs any folder as a profile, e.g. a copy for experiments. Its data goes to `DIR/data`.
- **Paths in `settings.yaml`:** data paths (`paths.database`, `briefs_dir`, `backups_dir`, a relative `markdown.dir`) are relative to the profile's data folder. `paths.prompt` is relative to the profile folder. Absolute paths are used as they are, so each profile can write to its own vault.
- **Secrets:** the real environment wins, then `<profile>/.env`, then the shared `.env`. Put only what differs (e.g. a profile's own Teams webhook) in the profile `.env`. YAML files only name the variables, never the values.

## Run

```bash
export CTI_PROFILE=mine                           # or pass --profile mine to each command
uv run python -m src.main --dry-run               # collect, score, dedup and list candidates. No LLM, no DB writes, no delivery
uv run python -m src.main --dry-run --with-llm    # also call the LLM and write briefs/*_dryrun.html (no DB, no Markdown, no delivery)
uv run python -m src.main                         # full run: brief, Markdown, DB, email/Teams
uv run python -m src.main --no-deliver            # full run without email/Teams
uv run python -m src.main --since-hours 72        # wider window (e.g. after a weekend)
uv run python -m src.main --source bleepingcomputer --dry-run   # test one source
uv run python -m src.main --list-sources
uv run python -m src.main --history               # review the last 7 days (see Weekly review)
uv run python -m src.main --reset                 # start over with an empty database (see Operations)
uv run python -m src.main --where                 # which config, profile and data folder are used
```

Exit codes: 0 success, 1 failed, 2 brief written but a delivery channel failed.

## Configuration overview

| File / section | What it controls |
| --- | --- |
| `settings.yaml` `general` | lookback window, timezone, HTTP, backups, retention |
| `settings.yaml` `paths` | database, briefs, backups (relative to the data folder), prompt (relative to the profile), `brief_formats` (`[html, md]`) |
| `settings.yaml` `markdown` | Markdown folder (vault), front matter, tags, note names |
| `settings.yaml` `scoring`, `limits` | score weights, quiet score (`min_score`), how many items go to the LLM and into the brief |
| `settings.yaml` `vulnerabilities` | the vulnerability table: threshold, size, relevance filter |
| `settings.yaml` `dedup` | title similarity, how far back history is checked |
| `settings.yaml` `llm` | Claude Code binary, model, timeout, tool lockdown |
| `settings.yaml` `delivery` | email (SMTP) and Teams |
| `sources.yaml` | one entry per source: `type`, `enabled`, `trust`, `category`, `section`, `options` |
| `gating.yaml` | keyword groups with weight, cap, `always_include`, `sections`; the LLM profile |

Secrets never go in YAML: config only names environment variables, whose values live in `.env` (see [Where things live](#where-things-live)).

## Scoring

`score = trust * trust_weight + category bonus + keyword points + recency + source bonus`, clamped to 0..100.

- Keyword groups in `gating.yaml` add `weight` per distinct hit (x1.5 in the title), capped per group. Negative weights (the `noise` groups) subtract.
- A group with `always_include: true` (e.g. `organization`) lets an item bypass the quiet score.
- A group with `sections: [vulnerabilities]` (or `[news]`) only applies to items from those sources.
- Items below `limits.min_score` are not sent to the LLM. Nothing is dropped just for lacking a keyword match.

Rough guide: a trusted news source with no keyword hit scores about 35, just under the default quiet score of 40.

## Vulnerabilities

Sources with `section: vulnerabilities` in `sources.yaml` (ENISA EUVD, CISA KEV, MSRC) are ranked separately and shown as a **Vulnerabilities** table: CVE, product, CVSS, EPSS, exploited/ransomware, KEV due date. They never take news ACT/SIGNAL slots. The top `vulnerabilities.to_llm` rows are also shown to the LLM, which may promote one to ACT; it then leaves the table.

Vulnerability feeds are noisy, so by default (`require_match: true`) a row needs a hit in one of `match_groups` (`organization`, `stack`) or a known-exploited flag (CISA KEV, ENISA exploited feed). Fill the `stack` group with the technology you actually run: for vulnerabilities it works as an allowlist. The `vuln_noise` group lowers products you don't run without penalising news about them.

## Outputs

| Output | Where | Notes |
| --- | --- | --- |
| HTML brief | `<data>/briefs/brief_<stamp>.html` | browser version; dry runs add `_dryrun` |
| Markdown brief | `markdown.dir`, e.g. `CTI Brief 2026-10-12 0700.md` | only on real runs, when `md` is in `paths.brief_formats` |
| Email | `delivery.email` | Outlook-friendly template (tables, inline styles) plus a plain-text part |
| Teams | `delivery.teams` | Adaptive Card via a Workflows webhook, full width; item counts capped by `max_*_items` |
| Review | `<data>/briefs/review_<stamp>.html` + Markdown in `markdown.dir` | from `--history` |

### Markdown and Obsidian

```yaml
paths:
  brief_formats: [html, md]
markdown:
  dir: ~/Obsidian/MyVault/CTI/Briefs   # Markdown goes here; HTML stays in the data folder
  frontmatter: true                    # YAML properties for Obsidian
  tags: [cti, cti-brief]
  brief_filename: "CTI Brief {date} {time}"   # {date} 2026-10-12, {time} 0700, {stamp} 20261012_070012
  review_filename: "CTI Review {date}"
```

Front matter of a brief: `type`, `date`, `generated`, `tags`, `act`, `signal`, `vulnerabilities`, `severity` (highest ACT severity), `cves`, `reviewed`, `sources_ok`, `failed_sources`, `dry_run`. Dates are unquoted, so Obsidian shows them as date properties and Bases or Dataview can filter on them. Reviews have `type: cti-review` with item counts.

An existing note is never overwritten: a second file with the same name gets the time appended. Dry runs never write Markdown, so test runs never end up in the vault.

Hermes Agent picks up these briefs and, on request, stores useful items in the LLM wiki: see [integrations/hermesagent/](integrations/hermesagent/README.md).

## Weekly review

`--history [DAYS]` lists everything the tool saw in the last DAYS (default 7) and writes it as HTML and Markdown:

- **Per source**: how many items were briefed, merged, reviewed or filtered, and the average score. Many *filtered* items with a low average means a source is mostly noise.
- **Keyword hits**: the 40 most frequent `gating.yaml` terms and how many of those items were briefed. Terms that hit often but are never briefed are candidates for a lower weight or the noise group.
- **All items** by day, with status, score, source, the brief headline and the matched keywords.

Filters: `--status briefed|merged|reviewed|filtered` (repeatable), `--source ID`, `--search TEXT`, `--limit N` (default 1000).

Statuses: *briefed* (in ACT, SIGNAL or the vulnerability table), *merged* (same event as a briefed item), *reviewed* (seen by the LLM, not chosen), *filtered* (below the quiet score, or a vulnerability without a relevant match).

## Cron

Example: a work brief every weekday at 07:00 (Monday covers the weekend), a weekly review on Friday, and a personal brief every evening. Each profile gets its own lock and log. `--init` prints a ready-to-paste line for a new profile.

**1. Use absolute paths.** Cron runs with a minimal `PATH` and doesn't load your shell profile. `--init` already stored the absolute `claude` path in `llm.engines.claude_code.binary`. For `uv`, run `which uv`.

**2. Create the log folders:** `mkdir -p ~/.config/cti-brief/profiles/{work,personal}/data/logs`

**3. Add the jobs** with `crontab -e`:

```cron
# cti-brief. Times are in the timezone set by CRON_TZ (cron otherwise uses the system timezone).
CRON_TZ=Europe/Amsterdam
CTI=/home/you/apps/cti-briefings
UV=/home/you/.local/bin/uv

# WORK. Tue-Fri 07:00: last 24 hours. flock skips a run if the previous one is still going.
0 7 * * 2-5  cd $CTI && flock -n /tmp/cti-work.lock $UV run python -m src.main --profile work >> /home/you/.config/cti-brief/profiles/work/data/logs/cron.log 2>&1
# Monday 07:00: last 72 hours, so the weekend is covered.
0 7 * * 1    cd $CTI && flock -n /tmp/cti-work.lock $UV run python -m src.main --profile work --since-hours 72 >> /home/you/.config/cti-brief/profiles/work/data/logs/cron.log 2>&1
# Friday 16:00: review of all items this week (all statuses, per-source counts, keyword hits) for gate tuning.
0 16 * * 5   cd $CTI && $UV run python -m src.main --profile work --history 7 >> /home/you/.config/cti-brief/profiles/work/data/logs/cron.log 2>&1

# PERSONAL. Every day at 21:00.
0 21 * * *   cd $CTI && flock -n /tmp/cti-personal.lock $UV run python -m src.main --profile personal >> /home/you/.config/cti-brief/profiles/personal/data/logs/cron.log 2>&1
```

Notes:

- `/home/you` is your **home folder** (`echo $HOME`). Cron doesn't expand `~` or nested variables, so write the paths out in full. If `cd $CTI` fails, nothing runs and nothing is logged.
- Profiles, data and secrets are found through `~/.config/cti-brief` as usual (cron sets `HOME`). If you moved them with `config.yaml`, nothing changes in the crontab. If you use `CTI_*` variables instead, set them at the top of the crontab.
- Always pass `--profile` in cron, so a line can never run the wrong brief.
- Secrets come from `.env`, so nothing secret goes into the crontab.
- `CRON_TZ` is supported by cronie (Fedora/RHEL/Arch) and Debian/Ubuntu cron. If yours ignores it, use system-timezone times.
- The Claude Code CLI must be logged in as the same user that owns the crontab.
- Already-briefed items are never repeated, so an overlapping window (like Monday's 72h) is safe.

**4. Test it in a cron-like environment** before relying on it:

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin bash -c 'cd /home/you/apps/cti-briefings && /home/you/.local/bin/uv run python -m src.main --profile work --dry-run'
```

**5. Check the result** after a run: `tail -50 ~/.config/cti-brief/profiles/<name>/data/logs/cron.log`, or the `runs` table in that profile's `intel.db` (status `success` / `partial` / `failed`, counts and the error per run). A failed real run also sends a notice to email and Teams (see Operations).

**Optional log rotation** (`/etc/logrotate.d/cti-brief`):

```text
/home/you/.config/cti-brief/profiles/*/data/logs/cron.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

## Updating

```bash
cd ~/apps/cti-briefings
git pull --ff-only && uv sync && uv run pytest -q
```

Updates never touch your profiles: they live outside the checkout, and `--init` never changes an existing profile. When a release adds settings to the template, [CHANGELOG.md](CHANGELOG.md) says what to copy into your profiles. Missing settings fall back to built-in defaults. To roll back, `git checkout <previous tag>`.

## Development

Keep two checkouts of the same repo: a **live** one that cron runs and that you never edit, and a **dev** one where you work.

```text
~/code/cti-briefings   dev: branches, tests, experiments. Push to GitHub when ready
~/apps/cti-briefings   live: cron runs here. Update with git pull (see Updating)
```

Both read the same `~/.config/cti-brief`, so you can try code changes against your real profiles from dev:

- `--dry-run` and `--dry-run --with-llm` are safe there. They never write the database, Markdown or deliveries.
- A real run from dev updates the live database. That does no harm, but those items then count as already briefed.
- For a fully separate sandbox, use `export CTI_PROFILES_DIR=/tmp/cti-sandbox` (or `CTI_CONFIG=...`) in the dev shell, then `--init test`. Or run a copy of a profile with `--config-dir`.
- `uv run pytest` always uses `templates/profile` in temporary folders, never your real configuration.

More tips (sandboxes, testing sources and prompts, the database, releasing): see [DEVELOPMENT.md](DEVELOPMENT.md).

## Operations

- **Backups:** every real run first copies the profile's `intel.db` to `backups/intel_<UTC timestamp>.db` in its data folder; the newest `general.backup_keep` (14) are kept. To restore: stop cron, then `cp backups/intel_<stamp>.db intel.db` inside the data folder (`--where` shows it). Backing up the profile folder also backs up its history, unless `data_dir` moved the data elsewhere.
- **Start over:** `--reset` backs up `intel.db` to `backups/reset_intel_<stamp>.db` (never rotated away), removes it and runs on an empty database, so everything in the lookback window is new again. It asks you to type `reset`; add `--yes` in scripts. Combine with `--no-deliver` or `--since-hours`. Briefs and vault notes are kept.
- **Retention:** items older than `general.retention_days` (180, never less than `dedup.history_days`) are deleted after a successful run. `0` keeps everything.
- **Failure notice:** when a real run fails (not `--dry-run`, not `--no-deliver`), a short "CTI brief run FAILED (<profile>)" message with the error goes to every enabled channel.
- **Silent sources:** a source that returned nothing in this run and the 3 real runs before it is listed under *Notes* in the brief; usually a changed feed URL or an expired key.
- **Version control:** the repo holds only code and `templates/profile`. Your profiles can go into a separate **private** repo: `git init ~/.config/cti-brief`, with a `.gitignore` for `.env`, `*/.env` and `profiles/*/data/`.

## Security

Story text comes from the internet and is passed to an LLM, so treat it as hostile:

- The prompt marks story text as untrusted data, and the reply is validated in code: unknown story ids, duplicates, re-mentions without a materiality note, over-long text, non-http links and over-limit counts are dropped.
- Claude Code runs with **no tools and no MCP servers** (`--tools "" --strict-mcp-config`) in an **empty temporary directory**, so an injected instruction cannot read `.env`, run commands or use connectors such as Gmail. `llm.engines.claude_code.allow_tools: true` turns this off; don't, unless you know why.
- Secrets are only read from the environment and the `.env` files (the shared `.env` is created with mode 600); config files hold variable names.

## Extending

- **Source:** create `src/sources/mysource.py`, subclass `Source`, decorate with `@register("mysource")`, implement `fetch(since)` returning `self.item(...)` objects, then add an entry with `type: mysource` to `sources.yaml`. It is discovered automatically. Examples: `enisa_euvd.py` (public API), `cyberint_news.py` (authenticated POST API), `example_api.py` (template).
- **Output format:** add `src/templates/brief.<fmt>.j2` and list `<fmt>` in `paths.brief_formats`.
- **LLM engine:** a file in `src/llm/` with `@register_engine("name")`, selected with `llm.engine`.
- **Delivery channel:** a `Deliverer` subclass in `src/delivery/`, registered in `src/delivery/__init__.py`.

Details in [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT, see [LICENSE](LICENSE).
