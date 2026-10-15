"""Safety and reliability: Claude call lockdown, DB backup/prune, silent sources, failure notice."""
import json
import subprocess
from datetime import timedelta

from src import pipeline
from src.db import Database
from src.llm.claude_code import ClaudeCodeEngine
from src.util import utcnow
from tests.test_pipeline import _cfg


def _fake_run(calls):
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": "{}"}), stderr="")
    return run


def test_claude_runs_without_tools_in_empty_dir(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    ClaudeCodeEngine({"binary": "claude", "model": "opus"}).complete("prompt")
    cmd, kw = calls[0]
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd
    assert "cti-brief-llm-" in kw["cwd"] and kw["cwd"] != str(tmp_path)


def test_claude_allow_tools_and_cwd_override(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    ClaudeCodeEngine({"allow_tools": True, "cwd": str(tmp_path)}).complete("prompt")
    cmd, kw = calls[0]
    assert "--tools" not in cmd and "--strict-mcp-config" not in cmd and kw["cwd"] == str(tmp_path)


def test_backup_keeps_newest_copies(tmp_path, monkeypatch):
    db = Database(tmp_path / "intel.db")
    stamps = iter(["20261001_070000", "20261002_070000", "20261003_070000"])
    import src.db as dbmod
    for stamp in stamps:
        monkeypatch.setattr(dbmod, "utcnow", lambda s=stamp: __import__("datetime").datetime.strptime(s, "%Y%m%d_%H%M%S"))
        db.backup(tmp_path / "backups", keep=2)
    names = sorted(p.name for p in (tmp_path / "backups").glob("*.db"))
    assert names == ["intel_20261002_070000.db", "intel_20261003_070000.db"]
    copy = Database(tmp_path / "backups" / names[-1], readonly=True)
    assert copy.conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 0  # a valid SQLite copy


def test_prune_never_goes_below_history_days(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    cfg.settings["general"]["retention_days"] = 5          # shorter than dedup.history_days (30)
    db = Database(tmp_path / "intel.db")
    db.conn.executemany("INSERT INTO items (id, title, first_seen, status) VALUES (?, ?, ?, 'reviewed')", [
        ("old", "old", (utcnow() - timedelta(days=40)).isoformat()),
        ("mid", "mid", (utcnow() - timedelta(days=10)).isoformat()),
    ])
    db.conn.execute("INSERT INTO briefings (item_id, kind) VALUES ('old', 'signal')")
    pipeline._prune(db, cfg)
    assert [r["id"] for r in db.conn.execute("SELECT id FROM items")] == ["mid"]
    assert db.conn.execute("SELECT COUNT(*) c FROM briefings").fetchone()["c"] == 0


def test_silent_source_warning_after_repeated_empty_runs(tmp_path):
    db = Database(tmp_path / "intel.db")
    for _ in range(3):
        rid = db.start_run(False)
        db.finish_run(rid, "success", {}, {"dead": {"ok": True, "items": 0}, "live": {"ok": True, "items": 5}})
    now = {"dead": {"ok": True, "items": 0}, "live": {"ok": True, "items": 4}}
    assert pipeline._silent_sources(db, now, 3) == [
        "Source dead returned no items in the last 4 runs; check its URL, key or options."]
    assert pipeline._silent_sources(db, now, 4) == []      # not enough history yet


class FakeDeliverer:
    name = "fake"
    alerts = []
    def send_alert(self, subject, text): self.alerts.append((subject, text))


class BrokenDeliverer:
    name = "broken"
    def send_alert(self, subject, text): raise RuntimeError("smtp down")


def test_failed_run_sends_notice_and_returns_1(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    def boom(*a, **k): raise RuntimeError("all sources exploded")
    monkeypatch.setattr(pipeline, "collect", boom)
    FakeDeliverer.alerts = []
    monkeypatch.setattr(pipeline, "enabled_deliverers", lambda s: [BrokenDeliverer(), FakeDeliverer()])
    assert pipeline.run_pipeline(cfg) == 1
    assert len(FakeDeliverer.alerts) == 1 and "all sources exploded" in FakeDeliverer.alerts[0][1]
    run = Database(tmp_path / "intel.db").conn.execute("SELECT status, error FROM runs").fetchone()
    assert run["status"] == "failed" and "exploded" in run["error"]


def test_no_notice_on_dry_run_or_no_deliver(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    FakeDeliverer.alerts = []
    monkeypatch.setattr(pipeline, "enabled_deliverers", lambda s: [FakeDeliverer()])
    assert pipeline.run_pipeline(cfg, deliver=False) == 1
    assert pipeline.run_pipeline(cfg, dry_run=True) == 1
    assert FakeDeliverer.alerts == []


def test_real_run_makes_a_backup(tmp_path, make_item, monkeypatch):
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, deliver=False) == 0     # first run: no DB yet, nothing to back up
    assert not (tmp_path / "backups").exists()
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    assert len(list((tmp_path / "backups").glob("intel_*.db"))) == 1


def test_email_brief_and_alert_go_through_smtp(monkeypatch):
    import smtplib
    from src.delivery.smtp_email import EmailDeliverer
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout): self.host = host
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context): pass
        def login(self, user, pw): assert pw == "pw"
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    d = EmailDeliverer({"smtp_host": "smtp.x", "username": "a@x", "to": ["b@x", "c@x"]})
    d.send_alert("CTI brief run FAILED", "boom")
    assert sent[0]["Subject"] == "CTI brief run FAILED" and sent[0]["To"] == "b@x, c@x"
    assert sent[0].get_content().strip() == "boom"


def test_reset_backs_up_removes_and_runs_fresh(tmp_path, make_item, monkeypatch):
    from src.main import reset_database
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    assert reset_database(cfg, confirm=False)
    assert not (tmp_path / "intel.db").exists()
    saved = list((tmp_path / "backups").glob("reset_intel_*.db"))
    assert len(saved) == 1
    old = Database(saved[0], readonly=True)
    assert old.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 1   # history kept in the backup
    # The same item is new again after the reset, so it is briefed again.
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    db = Database(tmp_path / "intel.db")
    assert db.conn.execute("SELECT status FROM items").fetchone()["status"] == "briefed"
    assert db.conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"] == 1


def test_reset_backup_survives_rotation(tmp_path):
    db = Database(tmp_path / "intel.db")
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "reset_intel_20260101_000000.db").write_bytes(b"x")
    db.backup(tmp_path / "backups", keep=1)
    db.backup(tmp_path / "backups", keep=1)
    assert (tmp_path / "backups" / "reset_intel_20260101_000000.db").exists()


def test_reset_needs_confirmation_when_not_interactive(tmp_path, make_item, monkeypatch, capsys):
    from src.main import reset_database
    cfg = _cfg(tmp_path, make_item, monkeypatch)
    assert pipeline.run_pipeline(cfg, deliver=False) == 0
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert reset_database(cfg, confirm=True) is False
    assert (tmp_path / "intel.db").exists() and "--yes" in capsys.readouterr().out


def test_reset_with_dry_run_is_refused():
    import pytest
    from src.main import main
    with pytest.raises(SystemExit):
        main(["--reset", "--dry-run", "--yes"])
