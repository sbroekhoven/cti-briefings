# Development

Tips for working on cti-brief without disturbing the briefs you rely on. For how the code fits together, see [ARCHITECTURE.md](ARCHITECTURE.md). For setup and usage, see [README.md](README.md).

## Setup

```bash
git clone https://github.com/sbroekhoven/cti-briefings ~/code/cti-briefings
cd ~/code/cti-briefings
uv sync                    # includes pytest (dev dependency group)
uv run pytest -q
```

## Two checkouts: dev and live

If you use cti-brief daily, keep the checkout that cron runs separate from the one you edit:

```text
~/code/cti-briefings   dev: branches, experiments, tests
~/apps/cti-briefings   live: cron runs here; never edit, only git pull
```

Both read the same `~/.config/cti-brief` (profiles, data, secrets), so code from dev can run against your real profiles. Flow:

1. Work on a branch in dev and run `uv run pytest -q`.
2. Merge to `main` and push. Tag a release if you want something to roll back to (see [Releasing](#releasing)).
3. In live: `git pull --ff-only && uv sync && uv run pytest -q`.
4. If live breaks: `git checkout <previous tag>` there, then fix forward in dev.

Never edit files in live. A local change there makes `git pull --ff-only` refuse, which is a sign something was edited by accident.

## Running safely against real profiles

Check first which profile and folders a command will use:

```bash
uv run python -m src.main --where --profile private
```

What each mode writes:

| Command | Database | HTML in data folder | Markdown (vault) | Email/Teams | Calls LLM |
| --- | --- | --- | --- | --- | --- |
| `--dry-run` | no | no | no | no | no |
| `--dry-run --with-llm` | no | `*_dryrun.html` | no | no | yes |
| `--no-deliver` | **yes** | yes | **yes** | no | yes |
| (normal run) | **yes** | yes | **yes** | **yes** | yes |
| `--history N` | no | `review_*.html` | **yes** | no | no |

- **Dry runs are always safe**, including from dev against your live profiles.
- **A real run from dev** (`--no-deliver` or normal) writes to the live database. Those items then count as already briefed, and the next scheduled brief skips them.
- **`--history` writes a review note into the vault** when `markdown.dir` is set. When you only want the terminal output, run it on a copy (next section) or delete the note afterwards.

## Sandboxes

**A throwaway copy of a real profile**, to try settings, keywords or a new prompt on real history:

```bash
cp -r ~/.config/cti-brief/profiles/private /tmp/p
# optional: in /tmp/p/settings.yaml set markdown.dir: null and delivery.*.enabled: false
uv run python -m src.main --config-dir /tmp/p --dry-run --with-llm
```

`--config-dir` uses the folder as the profile and keeps data in `/tmp/p/data`. The copy brings its database along, so dedup and "already briefed" behave as in live, and nothing you do touches the original.

**A completely separate config home**, e.g. to test `--init` or the quickstart as a new user would:

```bash
export XDG_CONFIG_HOME=$(mktemp -d)       # or CTI_CONFIG=/tmp/x/config.yaml
uv run python -m src.main --init test
uv run python -m src.main --profile test --dry-run
```

The variable only lasts for that shell. `--where` confirms you're in the sandbox.

## Tests

```bash
uv run pytest -q                     # everything, under a second
uv run pytest tests/test_config.py   # one file
uv run pytest -k init -x             # by name, stop at first failure
```

- **No network, no LLM:** `_cfg()` in `tests/test_pipeline.py` copies `templates/profile` into a temp dir, replaces `collect()` with one fake item and the LLM with `FakeEngine`. Use `_cfg()` for any test that runs the pipeline.
- **Isolation:** an autouse fixture in `tests/conftest.py` points `XDG_CONFIG_HOME` at a temp dir and clears all `CTI_*` variables, so tests can never read or write your real `~/.config/cti-brief`.
- **Items:** build them with the `make_item` fixture (`tests/conftest.py`).
- **Test data:** the tests use the template profile plus pinned keywords (e.g. an `ExampleBank` organization term). Changing your own profiles never breaks them, and changing the template only matters if a test depends on it.

## Working on a source

```bash
uv run python -m src.main --profile private --list-sources                 # ids, types, on/off
uv run python -m src.main --profile private --source bleepingcomputer --dry-run -v
```

`--source ID` runs only that source and ignores its `enabled` flag. You can test a disabled source this way before switching it on. `-v` switches the log level to DEBUG for more detail per source.

New source type:

1. Copy `src/sources/example_api.py` (an authenticated JSON API) or start from `rss.py` / `scraper.py`.
2. Register it with `@register("mytype")` and return `self.item(...)` from `fetch(since)`.
3. Read secrets with `self.secret(options["api_key_env"])`. The key itself goes in `.env`, never in YAML.
4. Add an entry to a sandbox profile's `sources.yaml` and iterate with `--source my_id --dry-run -v`.
5. Add a test in `tests/test_sources.py` with a canned response, not the live API.

Discovery is automatic: any module in `src/sources/` is imported. Check that yours shows up in the "Source types" line of `--list-sources`.

## Tuning keywords and scores

- `--dry-run` prints every candidate with its score and matched terms (`group:term`), plus the items under the quiet threshold. Change `gating.yaml`, run again, compare.
- `--history 7` shows, per source and per keyword, what was briefed versus filtered. Terms that hit often but never get briefed are candidates for a lower weight. Sources with mostly *filtered* items and low averages are candidates for a lower trust or removal.
- `limits.min_score` is the main noise dial. A trusted source with no keyword hit scores about 35.

## Working on the prompt

Each profile has its own `prompt.md`. To try a change without affecting the live brief:

```bash
cp -r ~/.config/cti-brief/profiles/private /tmp/p
$EDITOR /tmp/p/prompt.md
uv run python -m src.main --config-dir /tmp/p --dry-run --with-llm
```

Then compare `/tmp/p/data/briefs/brief_*_dryrun.html` with the live brief of the same day. The LLM is not deterministic, so run it twice before concluding a change helped. Keep the `{act_max}`, `{signal_max}` and `{lookback_hours}` placeholders and the JSON reply format: `src/analysis.py` validates the reply and drops anything that doesn't fit.

The analysis call runs Claude Code with no tools, no MCP servers and an empty working directory, because story text is untrusted. Keep it that way while developing (`allow_tools: false`).

## Templates (HTML, Markdown, email)

The templates are in `src/templates/*.j2`, shared by all profiles. To see a change without running the pipeline, re-run a dry run with `--with-llm` on a sandbox profile, or render from a test: `render(ctx, "html")` in `src/render.py` takes the same `ctx` the pipeline builds. The Hermes integration parses the Markdown brief (see [integrations/hermesagent/README.md](integrations/hermesagent/README.md)), so check its contract table before changing `brief.md.j2`.

## Looking inside the database

```bash
DB=~/.config/cti-brief/profiles/private/data/intel.db      # or take it from --where
sqlite3 -readonly -header -column "$DB" \
  "SELECT id, started_at, status, sources_failed, sent_to_llm, act_count, signal_count, error FROM runs ORDER BY id DESC LIMIT 10"
sqlite3 -readonly "$DB" "SELECT status, count(*) FROM items GROUP BY status"
sqlite3 -readonly "$DB" "SELECT kind, headline FROM briefings ORDER BY id DESC LIMIT 20"
```

Use `-readonly` against live data. Tables: `items` (everything seen, with score, status and matched terms), `briefings` (what went into which brief), `runs` (one row per run, with counts, errors and per-source stats as JSON in `source_stats`). Every real run first copies the database to `backups/` in the data folder, so `cp backups/intel_<stamp>.db intel.db` undoes a bad run (stop cron first).

## Testing the cron setup

Cron has almost no environment. Reproduce it before trusting a schedule:

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin bash -c \
  'cd ~/apps/cti-briefings && ~/.local/bin/uv run python -m src.main --profile private --dry-run'
```

Typical failures: a relative `claude` path in `llm.engines.claude_code.binary` (`--init` writes the absolute one), `~` in the crontab (write `/home/you`), or a missing `logs/` folder for the redirect.

## Hermes integration

The helper script can be exercised without touching the real Hermes state:

```bash
H=integrations/hermesagent/skill/cti-brief-to-wiki/scripts/briefs.py
CTI_WIKI_STATE=/tmp/state.json python3 -I $H status
CTI_WIKI_STATE=/tmp/state.json python3 -I $H pending
```

The repo copy uses generic default paths. Hermes runs the installed copy in `~/.hermes/`, and its folders come from `CTI_BRIEFS_PATH` in `~/.hermes/.env`. After changing the repo copy, install it with the commands in the integration README.

## Releasing

1. Update `CHANGELOG.md`. When a release adds settings to `templates/profile/`, say what existing users should copy into their profiles, because `--init` never touches existing ones and missing settings fall back to defaults.
2. Bump `version` in `pyproject.toml`, then run `uv lock`.
3. `uv run pytest -q`, commit, then `git tag vX.Y.Z && git push && git push --tags`.
4. Update live (see [Two checkouts](#two-checkouts-dev-and-live)).

## Before you push: keep private data out

The repo is public. Your profiles, data and secrets live in `~/.config/cti-brief`, so they can't end up in a commit by accident. Text you write can, though: an organization name in a test, a path in a doc, a real keyword in the template. Before pushing:

```bash
git grep -niI -E 'your-company|your-name|/home/[a-z]+/|@your-domain' -- . ':!uv.lock'
git diff --cached --stat        # anything unexpected staged?
```

Add your own terms to the pattern (employer, brands, private domains). For a deeper check, run [gitleaks](https://github.com/gitleaks/gitleaks) (`gitleaks detect`). Use generic names in tests and docs, such as `ExampleBank`, `example.com` and `/home/you`.
