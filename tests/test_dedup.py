from src.dedup import dedup_within_run, filter_history


def test_within_run_keeps_best_and_records_other_sources(make_item):
    a = make_item("Critical Fortinet flaw exploited in the wild", source="A"); a.score = 60
    b = make_item("Critical Fortinet flaw exploited in the wild!", source="B"); b.score = 50
    c = make_item("Unrelated ransomware story about hospitals", source="C"); c.score = 40
    kept, removed = dedup_within_run([a, b, c], 80)
    assert removed == 1 and len(kept) == 2
    assert kept[0].source_name == "A" and kept[0].also_reported_by == ["B"]


def test_history_drops_seen_and_flags_prior_briefings(make_item):
    seen = make_item("Seen story"); fresh = make_item("Brand new story about Citrix")
    followup = make_item("Update: Fortinet flaw now exploited by ransomware gang")
    history = [{"id": "x", "title": "Fortinet flaw now exploited by ransomware gang", "status": "briefed",
                "brief_date": "2026-10-01", "headline": "Fortinet flaw exploited"}]
    kept, dropped = filter_history([seen, fresh, followup], history, {seen.id}, 80)
    assert dropped == 1 and {i.title for i in kept} == {fresh.title, followup.title}
    assert followup.prior_briefings and not fresh.prior_briefings
