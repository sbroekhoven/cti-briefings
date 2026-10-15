from src.scoring import Scorer
from src.util import utcnow

GATING = {"groups": {
    "organization": {"weight": 30, "cap": 60, "always_include": True, "terms": ["ExampleBank"]},
    "sector": {"weight": 8, "cap": 16, "terms": ["bank", "trading"]},
    "noise": {"weight": -10, "cap": 30, "terms": ["webinar"]},
}}
CFG = {"trust_weight": 0.35, "title_multiplier": 1.5, "recency_max": 10, "category_bonus": {"government": 15}}


def test_keyword_hits_raise_score_and_org_forces_inclusion(make_item):
    s = Scorer(GATING, CFG, 24)
    plain = s.score(make_item("Generic news item"), utcnow())
    hit = s.score(make_item("ExampleBank phishing wave hits bank customers"), utcnow())
    assert hit.score > plain.score
    assert hit.always_include and not plain.always_include
    assert "organization:ExampleBank" in hit.matched_terms


def test_negative_keywords_and_category_bonus(make_item):
    s = Scorer(GATING, CFG, 24)
    noisy = s.score(make_item("Join our webinar"), utcnow())
    plain = s.score(make_item("Join our meeting"), utcnow())
    gov = s.score(make_item("Join our meeting", category="government"), utcnow())
    assert noisy.score < plain.score < gov.score


def test_group_cap_and_clamp(make_item):
    s = Scorer(GATING, CFG, 24)
    it = s.score(make_item("bank trading", trust=100), utcnow())
    assert it.score_breakdown["keywords"] == 16  # capped
    assert 0 <= it.score <= 100


def test_whole_word_matching(make_item):
    s = Scorer(GATING, CFG, 24)
    it = s.score(make_item("Banking sector update"), utcnow())  # "bank" must not match "Banking"
    assert it.matched_terms == []


def test_group_sections_limit_where_a_group_applies(make_item):
    from src.scoring import Scorer
    from src.util import utcnow
    gating = {"groups": {"vuln_noise": {"weight": -15, "cap": 45, "sections": ["vulnerabilities"], "terms": ["WordPress"]}}}
    sc = Scorer(gating, {}, 24)
    news, vuln = make_item("WordPress sites mass-hacked"), make_item("WordPress plugin SQL injection")
    vuln.section = "vulnerabilities"
    sc.score(news, utcnow()); sc.score(vuln, utcnow())
    assert news.score_breakdown["keywords"] == 0 and vuln.score_breakdown["keywords"] < 0
