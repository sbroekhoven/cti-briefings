# Briefs on a Hugo blog

cti-brief can write each brief as a post for a [Hugo](https://gohugo.io/) site: one **page bundle** per brief, with one file per language (English and Dutch). The posts are made for a **public** blog. They contain the Act and Signal items, the vulnerability table and the links. They leave out the internal parts: scores, relevance references (which name your organisation and stack), materiality notes, the near miss, warnings and failed sources.

## What you get

```text
<content_dir>/                      # e.g. ~/code/blog/content/briefs
  2026-10-27/
    index.en.md
    index.nl.md
  2026-10-28/
    ...
```

Every file starts with a small front matter block:

```yaml
---
title: CTI Brief 2026-10-27
date: 2026-10-27T07:00:12+01:00
draft: false
categories: [Threat Intelligence]
tags: [cti]
---
```

The body follows: an intro line (stories reviewed, sources, lookback), then `## Act`, `## Signal` and `## Vulnerabilities`. Dutch posts use Dutch headings (`Actie`, `Signalen`, `Kwetsbaarheden`) and Dutch severity and action words. There is no `# H1`, because your theme renders `title` itself.

**How the Dutch text is made.** When the profile has a language other than `en`, the same LLM call that picks the stories also writes the translations, so it costs no extra call. If the model leaves a translation out, that item stays in English and the run logs a warning. Vulnerability descriptions come straight from the feeds and are not translated.

**Second run on the same day.** An existing bundle is never overwritten: the second run writes `2026-10-27-1430/`.

## Turning it on (per profile)

In `~/.config/cti-brief/profiles/<name>/settings.yaml`, add `hugo` to the formats and add a `hugo:` block. Profiles created before this feature don't have the block yet; copy it from `templates/profile/settings.yaml`.

```yaml
paths:
  brief_formats: [html, md, hugo]

hugo:
  content_dir: ~/code/blog/content/briefs   # null = <briefs_dir>/hugo, handy for a first look
  bundle_name: "{date}"                     # folder and URL name; {date} {time} {stamp}
  languages: [en, nl]
  draft: false                              # true: you publish each post by hand
  title: {en: "CTI Brief {date}", nl: "CTI-briefing {date}"}
  categories: [Threat Intelligence]
  tags: [cti]
```

- `content_dir` is the **section folder** inside your Hugo `content/`. Each profile can point at its own section (`content/briefs`, `content/ai-briefs`, ...) or share one; bundle names are dated, and two profiles on the same day get `-HHMM`.
- `languages`: `en` and `nl` are supported. `[en]` alone gives English posts and no translation. More languages need labels in `LABELS` in `src/render.py`.
- Dry runs (`--dry-run`, `--dry-run --with-llm`) never write to the blog. Weekly `--history` reviews are internal and never go to Hugo.

Start with `content_dir: null`, run `uv run python -m src.main --profile <name> --no-deliver`, and look at `data/briefs/hugo/`. When the posts look right, point `content_dir` at the blog.

## Setting up the Hugo site

The bundles use Hugo's **translation by filename**: `index.en.md` and `index.nl.md` in the same folder are linked as translations of each other automatically. You need nothing beyond declaring the languages.

### `hugo.toml`

```toml
baseURL = "https://blog.example.org/"
defaultContentLanguage = "en"
defaultContentLanguageInSubdir = false   # English at /briefs/..., Dutch at /nl/briefs/...

[languages]
  [languages.en]
    languageName = "English"
    weight = 1
    title = "My blog"
  [languages.nl]
    languageName = "Nederlands"
    weight = 2
    title = "Mijn blog"

# categories and tags are Hugo's defaults; listed here so it's explicit.
[taxonomies]
  category = "categories"
  tag = "tags"
```

On recent Hugo versions some language keys were renamed (for example `languageName` to `label`). If `hugo` warns about a deprecated key, follow its message.

### Section pages

Give the section a list page in both languages: `content/briefs/_index.en.md` and `content/briefs/_index.nl.md`, each with only a title.

```yaml
---
title: Threat intelligence briefs     # _index.nl.md: Dreigingsinformatie
---
```

Each post then has these URLs:

- `/briefs/2026-10-27/` (English)
- `/nl/briefs/2026-10-27/` (Dutch)
- `/categories/threat-intelligence/`, `/tags/cti/` and their `/nl/...` versions

### Templates

Most themes already show `.Translations` (a language switcher) and the taxonomies. If yours doesn't, or you want a dedicated layout for briefs, create `layouts/briefs/single.html`:

```go-html-template
{{ define "main" }}
<article>
  <h1>{{ .Title }}</h1>
  <p>
    <time datetime="{{ .Date.Format "2006-01-02T15:04:05Z07:00" }}">{{ .Date | time.Format ":date_long" }}</time>
    {{ with .Translations }} · {{ range . }}<a href="{{ .RelPermalink }}" hreflang="{{ .Language.Lang }}">{{ .Language.LanguageName }}</a> {{ end }}{{ end }}
  </p>
  {{ .Content }}
  {{ with .GetTerms "tags" }}
    <p>{{ range . }}<a href="{{ .RelPermalink }}">#{{ .LinkTitle }}</a> {{ end }}</p>
  {{ end }}
</article>
{{ end }}
```

### Styling hooks

Each Act heading carries its severity as a class through Hugo's heading attributes (`### Headline {.sev-high}`, on by default via `markup.goldmark.parser.attribute.title`). The paragraph right after it is the `SEVERITY · action · CVE` line, with the severity in bold. In the vulnerability table, "exploited" is bold. So CSS alone can colour the brief:

```css
.brief .content h3.sev-critical { border-left: 4px solid #c92a2a; padding-left: .85rem; }
.brief .content h3.sev-critical + p strong { background: #c92a2a; color: #fff; border-radius: 999px; padding: 0 .5rem; }
.brief .table-wrap td strong { color: #c92a2a; }
```

Give the section its own `layouts/briefs/single.html` that wraps the content in `<article class="brief">`, and leave out the cover image, since briefs have none. The [suspiciousbytes.com](https://suspiciousbytes.com) repo has a complete example: `layouts/briefs/single.html` (jump list via `.TableOfContents`, AI note, older/newer links), `layouts/briefs/list.html` (briefs per month with their Act headlines from `.Fragments`) and the `CTI briefings` block in `static/css/custom.css`.

Apart from the heading attribute, the posts are plain Markdown: headings, lists, links and a pipe table. Hugo's default renderer (Goldmark) handles all of them, and `markup.goldmark.renderer.unsafe` is not needed. A theme that styles tables poorly may need a little CSS for the vulnerability table.

**Shortcodes.** Story text comes from the internet. `{{` in any title or summary is written as `{ {`, so a story can never trigger a Hugo shortcode or break your build.

## Publishing

cti-brief only writes files; it never commits or deploys. A small wrapper script commits and pushes after each run, and your CI (Netlify, Cloudflare Pages, GitHub Actions, ...) builds the site.

```bash
#!/usr/bin/env bash
# ~/bin/cti-to-blog: run the brief, then publish new posts.
set -uo pipefail
CTI=/home/you/apps/cti-briefings
BLOG=/home/you/code/blog
UV=/home/you/.local/bin/uv

cd "$CTI" && $UV run python -m src.main --profile private
rc=$?
[ "$rc" -le 2 ] || exit "$rc"      # 2 = brief written, but email/Teams failed: still publish

cd "$BLOG" || exit 1
git add content/briefs
git diff --cached --quiet && exit 0   # nothing new (quiet day with send_empty_brief: false)
git commit -m "CTI brief $(date +%F)" && git push
```

Call this script from cron instead of `src.main` (see [Cron](README.md#cron), keep the `flock` and the log redirect). If you would rather read every post before it goes out, set `draft: true`. Posts then only show with `hugo server -D`, and you publish one by changing `draft` to `false`.

## Things to keep in mind

- **Public means public.** The stripped-down post doesn't name your relevance references, but which stories were picked still reflects your profile (`gating.yaml`). For a work profile, consider whether that is fine to publish, or use a separate public profile with generic keywords.
- **Translation quality** depends on the model. CVE ids and product names are kept as they are.
- **Times.** `date` carries the timezone offset from `general.timezone`, so Hugo shows the right time whatever the build server's timezone. A post dated a few seconds in the future is skipped by Hugo unless `buildFuture` is on. This only matters when you build right after a run on a machine with a clock behind.
- **Translation by content directory** (`content/en/...`, `content/nl/...`) is not supported. If your site uses it, translation by filename still works there, or symlink one section folder into both trees. The filename approach is simpler.
