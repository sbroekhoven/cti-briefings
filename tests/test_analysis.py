from src.analysis import build_prompt, validate


def _by_id(make_item):
    a, b, c = make_item("Story A"), make_item("Story B"), make_item("Story C")
    c.prior_briefings = [{"date": "2026-10-01", "headline": "old"}]
    return {1: a, 2: b, 3: c}


def test_validate_enforces_rules(make_item):
    by_id = _by_id(make_item)
    result = {
        "act": [
            {"story_id": 1, "headline": "x" * 200, "why_it_matters": "why", "action": "bogus", "severity": "critical", "links": ["javascript:alert(1)"]},
            {"story_id": 99, "headline": "invented"},                      # unknown id
            {"story_id": 3, "headline": "re-mention, no note"},            # prior briefing without note
            {"story_id": 2, "headline": "over limit"},
        ],
        "signal": [{"story_id": 1, "line": "dup of act"}, {"story_id": 2, "line": "ok"}],
        "merges": [[1, 2]], "materiality_notes": {}, "near_miss": 2,
    }
    out = validate(result, by_id, act_max=1, max_items=5)
    assert [a["story_id"] for a in out["act"]] == [1]
    assert len(out["act"][0]["headline"]) <= 90
    assert out["act"][0]["action"] == "monitor" and out["act"][0]["links"] == [by_id[1].link]
    assert out["signal"] == []          # 2 was merged away, 1 already in act
    assert out["warnings"]


def test_materiality_note_allows_remention(make_item):
    by_id = _by_id(make_item)
    out = validate({"act": [{"story_id": 3, "headline": "update"}], "materiality_notes": {"3": "now exploited"}},
                   by_id, act_max=3, max_items=5)
    assert out["act"][0]["materiality_note"] == "now exploited"


def test_vulnerability_items_never_signal(make_item):
    vuln = make_item("CVE-2026-1 Foo RCE")
    vuln.section = "vulnerabilities"
    by_id = {1: vuln, 2: make_item("News story")}
    out = validate({"act": [], "signal": [{"story_id": 1, "line": "vuln"}, {"story_id": 2, "line": "news"}]},
                   by_id, act_max=1, max_items=5)
    assert [s["story_id"] for s in out["signal"]] == [2]


def test_translation_rule_only_when_asked(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Pick stories.")
    plain = build_prompt(prompt, {"stories": []}, act_max=1, signal_max=1, lookback_hours=24)
    assert "translations" not in plain
    nl = build_prompt(prompt, {"stories": []}, act_max=1, signal_max=1, lookback_hours=24, translations={"nl": "Dutch"})
    assert "Dutch (nl)" in nl and '"nl": {"headline": "", "why_it_matters": ""}' in nl


def test_validate_keeps_requested_translations(make_item):
    by_id = _by_id(make_item)
    result = {"act": [{"story_id": 1, "headline": "H", "translations": {
                  "nl": {"headline": "x" * 200, "why_it_matters": "Waarom", "bogus": "?"}, "de": {"headline": "D"}}}],
              "signal": [{"story_id": 2, "line": "L", "translations": {"nl": {"line": 5}}}]}
    out = validate(result, by_id, act_max=3, max_items=5, languages=["nl"])
    assert set(out["act"][0]["translations"]) == {"nl"}
    nl = out["act"][0]["translations"]["nl"]
    assert len(nl["headline"]) <= 90 and nl["why_it_matters"] == "Waarom" and "bogus" not in nl
    assert out["signal"][0]["translations"] == {}                     # not a string: dropped
    assert any("No nl translation for story 2" in w for w in out["warnings"])
    assert validate(result, by_id, act_max=3, max_items=5)["act"][0]["translations"] == {}
