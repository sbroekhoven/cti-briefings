# Changelog

## 0.2.0

- **Profiles:** run several independent briefs (e.g. work and personal) from one checkout. Each profile has its own `settings.yaml`, `sources.yaml`, `gating.yaml`, `prompt.md` and data (database, briefs, backups, logs). Select one with `--profile NAME` or `CTI_PROFILE`.
- **Config outside the repo:** profiles and secrets live in `~/.config/cti-brief/` (XDG). The repo only holds the template in `templates/profile/`.
- **`--init NAME`** creates a profile from the template, plus the shared `.env` (mode 600) and a commented `config.yaml` when missing.
- **Locations** are configurable with flags, `CTI_*` variables or `config.yaml`: `profiles_dir`, `data_dir`, `env_file`, `default_profile`. `--where` shows what is in effect and why.
- **Secrets:** `<profile>/.env` overrides the shared `.env`; the real environment overrides both.
- **Upgrading from 0.1** (a single `config/` + `prompts/` in the checkout): run `--init NAME`, copy your `config/*.yaml` and `prompts/analyst.md` (as `prompt.md`) into the new profile, and move `intel.db`, `briefs/` and `backups/` into `~/.config/cti-brief/profiles/NAME/data/`. In `paths`, use `database: intel.db`, `briefs_dir: briefs`, `backups_dir: backups` and `prompt: prompt.md`. Move `.env` to `~/.config/cti-brief/.env`, and add `--profile NAME` to your cron lines.
