# Changelog

## Unreleased

- **Hugo blog posts:** add `hugo` to `paths.brief_formats` to write each brief as a Hugo page bundle (`<content_dir>/<date>/index.en.md` + `index.nl.md`) for a public blog: minimal front matter (title, date, draft, categories, tags), no internal details, and Act headings tagged with their severity (`{.sev-high}`) for styling. The Dutch text is written by the same LLM call. Opt-in: copy the `hugo:` block from `templates/profile/settings.yaml`. See [README_HUGO.md](README_HUGO.md).
- **Configurable titles:** `titles.brief`, `titles.short` and `titles.review` in `settings.yaml` set the headings of briefs, reviews, email, plain text and Teams, so a profile can be something other than a CTI brief (e.g. an AI landscape brief). Defaults are unchanged; add the `titles:` block from `templates/profile/settings.yaml` only if you want other names.
- **Custom sections:** a source can use any `section` value (e.g. `section: releases`). It is ranked with the news, and keyword groups can be limited to it with `sections: [releases]`.

## 0.2.0

- **Profiles:** run several independent briefs (e.g. work and personal) from one checkout. Each profile has its own `settings.yaml`, `sources.yaml`, `gating.yaml`, `prompt.md` and data (database, briefs, backups, logs). Select one with `--profile NAME` or `CTI_PROFILE`.
- **Config outside the repo:** profiles and secrets live in `~/.config/cti-brief/` (XDG). The repo only holds the template in `templates/profile/`.
- **`--init NAME`** creates a profile from the template, plus the shared `.env` (mode 600) and a commented `config.yaml` when missing.
- **Locations** are configurable with flags, `CTI_*` variables or `config.yaml`: `profiles_dir`, `data_dir`, `env_file`, `default_profile`. `--where` shows what is in effect and why.
- **Secrets:** `<profile>/.env` overrides the shared `.env`; the real environment overrides both.
- **Upgrading from 0.1** (a single `config/` + `prompts/` in the checkout): run `--init NAME`, copy your `config/*.yaml` and `prompts/analyst.md` (as `prompt.md`) into the new profile, and move `intel.db`, `briefs/` and `backups/` into `~/.config/cti-brief/profiles/NAME/data/`. In `paths`, use `database: intel.db`, `briefs_dir: briefs`, `backups_dir: backups` and `prompt: prompt.md`. Move `.env` to `~/.config/cti-brief/.env`, and add `--profile NAME` to your cron lines.
