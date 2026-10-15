from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   autoescape=select_autoescape(["html", "html.j2"]), trim_blocks=True)
# Brackets in link text would break Markdown links.
_env.filters["mdlink"] = lambda s: str(s).replace("[", "\\[").replace("]", "\\]")
class _FrontMatterDumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    # Dates unquoted, so Obsidian shows them as Date / Date & time properties.
    if _ISO_DATE.match(value):
        return dumper.represent_scalar("tag:yaml.org,2002:timestamp", value)
    return dumper.represent_str(value)


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")
_FrontMatterDumper.add_representer(str, _str)
# Front matter: let PyYAML quote whatever else needs quoting.
_env.filters["yaml"] = lambda d: yaml.dump(d, Dumper=_FrontMatterDumper, sort_keys=False, allow_unicode=True,
                                           default_flow_style=None, width=1000)
# Pipes and newlines would break a Markdown table row.
_env.filters["mdcell"] = lambda s: " ".join(str(s).split()).replace("|", "\\|")


_CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)


def vuln_rows(items: list) -> list[dict]:
    """Table rows for the vulnerability section. Sources fill item.details where they can (ENISA, KEV);
    for plain feeds (e.g. MSRC RSS) the CVE and description are taken from the title."""
    rows = []
    for it in items:
        d = it.details
        m = _CVE.search(it.title)
        cve = d.get("cve") or (m.group(0).upper() if m else "")
        desc = d.get("description") or _CVE.sub("", it.title).strip(" :-")
        rows.append({"cve": cve, "product": d.get("product") or "", "description": desc,
                     "cvss": d.get("cvss"), "epss": d.get("epss"), "exploited": bool(d.get("exploited")),
                     "ransomware": bool(d.get("ransomware")), "due": d.get("due"),
                     "link": it.link, "source": it.source_name, "score": it.score, "item": it})
    return rows


def build_context(analysis: dict, stats: dict, vulns: list | None = None, *, tz: str, lookback: float,
                  dry_run: bool, failed_sources: list[str], now: datetime) -> dict:
    local = now.astimezone(ZoneInfo(tz))
    return {
        "act": analysis["act"], "signal": analysis["signal"], "near_miss": analysis["near_miss"],
        "vulns": vuln_rows(vulns or []),
        "warnings": analysis["warnings"], "stats": stats, "lookback": f"{lookback:g}", "dry_run": dry_run,
        "failed_sources": failed_sources, "date": local.strftime("%Y-%m-%d"),
        "generated": local.strftime("%Y-%m-%d %H:%M %Z"), "filename_stamp": local.strftime("%Y%m%d_%H%M%S"),
        "generated_iso": local.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def render(ctx: dict, fmt: str, template: str = "brief") -> str:
    """Render templates/<template>.<fmt>.j2 (e.g. brief.html, review.md)."""
    return _env.get_template(f"{template}.{fmt}.j2").render(**ctx)


def render_html(ctx: dict) -> str:
    return render(ctx, "html")


def render_text(ctx: dict) -> str:
    lines = [f"CTI Brief {ctx['generated']}", ""]
    lines.append(f"ACT ({len(ctx['act'])})")
    for a in ctx["act"]:
        lines += [f"- [{a['severity'].upper()}/{a['action']}] {a['headline']}", f"  {a['why_it_matters']}", f"  {a['links'][0]}"]
    if not ctx["act"]:
        lines.append("- Nothing requires action today.")
    lines += ["", f"SIGNAL ({len(ctx['signal'])})"]
    lines += [f"- {s['line']} ({s['item'].link})" for s in ctx["signal"]]
    if ctx.get("vulns"):
        lines += ["", f"VULNERABILITIES ({len(ctx['vulns'])})"]
        lines += [f"- {v['cve'] or v['source']}{' [EXPLOITED]' if v['exploited'] else ''} {v['product']} "
                  f"{v['description']} ({v['link']})".replace("  ", " ") for v in ctx["vulns"]]
    return "\n".join(lines) + "\n"
