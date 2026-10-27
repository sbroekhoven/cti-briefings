import json
import shutil
from pathlib import Path

import pytest
import yaml

from src import pipeline
from src.config import Config
from src.db import Database
from src.llm import ENGINES, get_engine
from src.llm.base import LLMEngine


class FakeEngine(LLMEngine):
    name = "fake"
    def complete(self, prompt):
        data = json.loads(prompt.split("```json", 1)[1].rsplit("```", 1)[0])
        sid = data["stories"][0]["id"]
        return json.dumps({"act": [{"story_id": sid, "headline": "H", "why_it_matters": "W", "relevance_refs": ["sector"],
                                    "severity": "high", "action": "patch", "links": []}],
                           "signal": [], "merges": [], "materiality_notes": {}, "near_miss": None})


class TranslatingEngine(LLMEngine):
    name = "fake-nl"
    def complete(self, prompt):
        assert "Dutch (nl)" in prompt
        data = json.loads(prompt.split("```json", 1)[1].rsplit("```", 1)[0])
        sid = data["stories"][0]["id"]
        return json.dumps({"act": [{"story_id": sid, "headline": "Phishing {{< evil >}}", "why_it_matters": "Matters",
                                    "relevance_refs": ["ExampleBank internal"], "severity": "high", "action": "patch",
                                    "links": [], "translations": {"nl": {"headline": "Phishing NL",
                                                                         "why_it_matters": "Daarom"}}}],
                           "signal": [], "merges": [], "materiality_notes": {}, "near_miss": None})


def _cfg(tmp_path, make_item, monkeypatch):
    root = Path(__file__).resolve().parent.parent
    shutil.copytree(root / "templates" / "profile", tmp_path, dirs_exist_ok=True)
    cfg = Config(config_dir=tmp_path)
    cfg.data = tmp_path  # data straight under tmp_path, so the assertions below stay short
    # Never write outside tmp_path, whatever the real config points at (e.g. an Obsidian vault).
    cfg.settings["markdown"]["dir"] = str(tmp_path / "markdown")
    cfg.settings["hugo"]["content_dir"] = str(tmp_path / "blog")
    # Pin what the assertions rely on, so the tests do not depend on the contents of the real config.
    cfg.settings["markdown"].update(brief_filename="CTI Brief {date} {time}", review_filename="CTI Review {date}")
    cfg.gating.setdefault("groups", {})["organization"] = {"weight": 30, "cap": 60, "always_include": True,
                                                           "terms": ["ExampleBank"]}
    for key in ("database", "briefs_dir", "backups_dir"):
        assert not Path(cfg.settings["paths"][key]).expanduser().is_absolute(), f"paths.{key} must be relative for tests"
    item = make_item("ExampleBank customers targeted by phishing", summary="bank trading", link="https://example.com/p")
    monkeypatch.setattr(pipeline, "collect", lambda s, since, w: ([item], {"x": {"ok": True, "items": 1, "seconds": 0}}))
    get_engine(cfg.settings)  # discover real engines first
    ENGINES["claude_code"] = FakeEngine
    return cfg


def test_full_run_then_second_run_is_deduplicated(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    briefs = list((tmp_path / "briefs").glob("brief_*.html"))
    assert len(briefs) == 1 and "ExampleBank" not in briefs[0].name
    db = Database(tmp_path / "intel.db")
    run = db.conn.execute("SELECT * FROM runs").fetchone()
    assert run["status"] == "success" and run["act_count"] == 1
    # same item again: already handled, so no new candidates and no second brief
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    assert len(list((tmp_path / "briefs").glob("brief_*.html"))) == 1
    assert db.conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 2


def test_dry_run_writes_nothing(tmp_path, make_item, monkeypatch, capsys):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, dry_run=True) == 0
    assert not (tmp_path / "intel.db").exists() and not list((tmp_path / "briefs").glob("*.html"))
    assert "CANDIDATES" in capsys.readouterr().out


def test_multiple_brief_formats(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["paths"]["brief_formats"] = ["html", "md"]
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    md = list((tmp_path / "markdown").glob("CTI Brief *.md"))
    assert len(md) == 1 and len(list((tmp_path / "briefs").glob("brief_*.html"))) == 1
    text = md[0].read_text()
    assert "\n# Cyber Threat Intelligence Brief\n" in text and "&amp;" not in text and "&#" not in text
    db = Database(tmp_path / "intel.db")
    assert db.conn.execute("SELECT brief_file FROM runs").fetchone()["brief_file"].endswith(".html")


def test_history_search(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    db = Database(tmp_path / "intel.db", readonly=True)
    rows = db.search_items(7, statuses=["briefed"], text="examplebank")
    assert len(rows) == 1 and rows[0]["kind"] == "act" and rows[0]["headline"]
    assert db.search_items(7, text="no such story") == []
    assert db.search_items(7, sources=["other"]) == []


def test_history_writes_review_files(tmp_path, make_item, monkeypatch):
    from argparse import Namespace
    from src.main import _print_history
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    assert _print_history(cfg, Namespace(history=7, status=None, source=None, search=None, limit=1000)) == 0
    html = list((tmp_path / "briefs").glob("review_*.html"))
    md = list((tmp_path / "markdown").glob("CTI Review *.md"))
    assert len(html) == 1 and len(md) == 1
    assert "ExampleBank" in html[0].read_text() and "| Time | Status |" in md[0].read_text()
    assert md[0].read_text().startswith("---\ntype: cti-review\n") and "cti-review]" in md[0].read_text()
    text, page = md[0].read_text(), html[0].read_text()
    assert "## Per source" in text and "| src | 1 |" in text
    assert "## Keyword hits" in text and "organization:ExampleBank" in text and "organization:ExampleBank" in page


def test_vulnerabilities_get_their_own_table(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["paths"]["brief_formats"] = ["html", "md"]
    news = make_item("ExampleBank customers targeted by phishing", summary="bank trading", link="https://example.com/p")
    vuln = make_item("CVE-2026-0001 Foo Server RCE", link="https://example.com/v", trust=95)
    vuln.section, vuln.details = "vulnerabilities", {"cve": "CVE-2026-0001", "product": "Foo Server", "exploited": True}
    monkeypatch.setattr(pipeline, "collect", lambda s, since, w: ([news, vuln], {"x": {"ok": True, "items": 2, "seconds": 0}}))
    cfg.settings["vulnerabilities"]["min_score"] = 0
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    md = next((tmp_path / "markdown").glob("CTI Brief *.md")).read_text()
    assert "## Vulnerabilities (1)" in md and "[CVE-2026-0001](https://example.com/v)" in md and "exploited" in md
    db = Database(tmp_path / "intel.db")
    kinds = {r["item_id"]: r["kind"] for r in db.conn.execute("SELECT item_id, kind FROM briefings")}
    assert kinds == {news.id: "act", vuln.id: "vuln"}


def test_vuln_relevance_filter():
    from src.models import IntelItem
    def v(terms=(), **details):
        it = IntelItem(source_id="s", source_name="S", category="x", trust=50, title="t", link="https://x/" + str(id(terms)))
        it.matched_terms, it.details = list(terms), details
        return it
    cfg = {"match_groups": ["organization", "stack"], "exploited_always": True}
    assert pipeline._vuln_relevant(v(["stack:Azure"]), cfg)
    assert pipeline._vuln_relevant(v(exploited=True), cfg)
    assert not pipeline._vuln_relevant(v(["severity:critical"]), cfg)
    assert not pipeline._vuln_relevant(v(exploited=True), {**cfg, "exploited_always": False})


def test_email_template_is_outlook_friendly(tmp_path, make_item, monkeypatch):
    from src.render import build_context, render
    from src.util import utcnow
    item = make_item("Foo & Bar breach")
    an = {"act": [{"item": item, "headline": "Foo & Bar", "why_it_matters": "W", "severity": "high", "action": "patch",
                   "cves": [], "relevance_refs": [], "materiality_note": None, "links": [item.link]}],
          "signal": [], "near_miss": None, "warnings": []}
    ctx = build_context(an, {"reviewed": 1, "sources_ok": 1}, [item], tz="UTC", lookback=24, dry_run=False,
                        failed_sources=[], now=utcnow())
    html = render(ctx, "html", "email")
    assert "<style" not in html and "#f3f4f6" not in html and 'bgcolor="#ffffff"' in html
    assert "Foo &amp; Bar" in html and "Vulnerabilities (1)" in html


def test_markdown_goes_to_its_own_dir_with_frontmatter(tmp_path, make_item, monkeypatch):
    import yaml
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    vault = tmp_path / "vault" / "CTI"
    cfg.settings["paths"]["brief_formats"] = ["html", "md"]
    cfg.settings["markdown"] = {"dir": str(vault), "frontmatter": True, "tags": ["cti"]}
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    assert list((tmp_path / "briefs").glob("brief_*.html")) and not list((tmp_path / "briefs").glob("*.md"))
    text = next(vault.glob("brief_*.md")).read_text()
    assert text.startswith("---\n")
    props = yaml.safe_load(text.split("---\n")[1])
    assert props["type"] == "cti-brief" and props["tags"] == ["cti"] and props["act"] == 1
    assert "\ndate: 20" in text and "'20" not in text.split("---\n")[1]   # dates unquoted for Obsidian


def test_dry_run_markdown_stays_in_briefs_dir(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["markdown"] = {"dir": str(tmp_path / "vault")}
    assert pipeline.output_dir(cfg, "md", dry_run=True) == tmp_path / "briefs"
    assert pipeline.output_dir(cfg, "md") == tmp_path / "vault"
    assert pipeline.output_dir(cfg, "html") == tmp_path / "briefs"


def test_markdown_filenames_and_no_overwrite(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["markdown"] = {"brief_filename": "CTI Brief {date}"}
    assert pipeline.markdown_name(cfg, "brief", "20261009_070012") == "CTI Brief 2026-10-09"
    assert pipeline.markdown_name(cfg, "review", "20261009_070012") == "review_20261009_070012"  # default
    first = pipeline.unique_path(tmp_path, "CTI Brief 2026-10-09", "md", "20261009_070012")
    first.write_text("x")
    second = pipeline.unique_path(tmp_path, "CTI Brief 2026-10-09", "md", "20261009_143000")
    assert first.name == "CTI Brief 2026-10-09.md" and second.name == "CTI Brief 2026-10-09 1430.md"


def test_dry_run_with_llm_writes_html_only(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["paths"]["brief_formats"] = ["html", "md"]
    assert pipeline.run_pipeline(cfg, dry_run=True, with_llm=True) == 0
    assert [p.suffix for p in (tmp_path / "briefs").glob("brief_*")] == [".html"]
    assert not (tmp_path / "markdown").exists()


def test_titles_come_from_settings(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["titles"] = {"brief": "AI Landscape Brief", "short": "AI Brief"}
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    html = next((tmp_path / "briefs").glob("brief_*.html")).read_text()
    md = next((tmp_path / "markdown").glob("*.md")).read_text()
    assert "<title>AI Brief " in html and "<h1>AI Landscape Brief</h1>" in html and "# AI Landscape Brief" in md
    assert "Cyber Threat Intelligence" not in html
    assert pipeline.brief_titles(cfg)["review"] == "CTI Review"   # unset keys keep the default


def test_hugo_bundle_per_language(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    ENGINES["claude_code"] = TranslatingEngine
    cfg.settings["paths"]["brief_formats"] = ["html", "hugo"]
    cfg.settings["hugo"].update(languages=["en", "nl"], categories=["Threat Intelligence"], tags=["cti"])
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    bundles = list((tmp_path / "blog").iterdir())
    assert len(bundles) == 1 and sorted(p.name for p in bundles[0].iterdir()) == ["index.en.md", "index.nl.md"]
    en, nl = (bundles[0] / "index.en.md").read_text(), (bundles[0] / "index.nl.md").read_text()
    front = yaml.safe_load(en.split("---\n")[1])
    assert set(front) == {"title", "date", "draft", "categories", "tags"}
    assert front["title"] == f"CTI Brief {bundles[0].name}" and front["categories"] == ["Threat Intelligence"]
    assert "### Phishing { {< evil >} }" in en and "{{<" not in en
    assert "## Actie (1)" in nl and "### Phishing NL {.sev-high}" in nl and "Daarom" in nl and "**HOOG** · patchen" in nl
    for text in (en, nl):
        assert "ExampleBank internal" not in text and "score" not in text and "Notes" not in text
    assert not list((tmp_path / "markdown").glob("*.md"))           # md not in brief_formats


def test_hugo_bundle_names_and_dry_run(tmp_path, make_item, monkeypatch):
    first = pipeline.hugo_bundle_dir(tmp_path, "2026-10-09", "20261009_070012")
    second = pipeline.hugo_bundle_dir(tmp_path, "2026-10-09", "20261009_143000")
    assert first.name == "2026-10-09" and second.name == "2026-10-09-1430"
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["paths"]["brief_formats"] = ["hugo"]
    assert pipeline.run_pipeline(cfg, dry_run=True, with_llm=True) == 0
    assert not (tmp_path / "blog").exists()
    cfg.settings["hugo"]["languages"] = ["en", "fr"]
    with pytest.raises(ValueError, match="fr"):
        pipeline.hugo_settings(cfg)
