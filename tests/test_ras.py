"""rasdaemon-Auswertung: Baseline, neue Ereignisse, Einstufung und Runner-Integration."""

from __future__ import annotations

import sqlite3
import subprocess
import sys

import pytest

from hardwaretest.core import ras
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters

UC_STATUS = (1 << 63) | (1 << 61)  # VAL + UC
CE_STATUS = 1 << 63


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ras-mc_event.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE mc_event (id INTEGER PRIMARY KEY, timestamp TEXT, err_count INTEGER, err_type TEXT,
            err_msg TEXT, label TEXT);
        CREATE TABLE mce_record (id INTEGER PRIMARY KEY, timestamp TEXT, status INTEGER, bank_name TEXT,
            cpu INTEGER, socketid INTEGER, error_msg TEXT, mcistatus_msg TEXT);
        CREATE TABLE aer_event (id INTEGER PRIMARY KEY, timestamp TEXT, dev_name TEXT, err_type TEXT,
            err_msg TEXT);
        CREATE TABLE memory_failure_event (id INTEGER PRIMARY KEY, timestamp TEXT, pfn TEXT,
            page_type TEXT, action_result TEXT);
        INSERT INTO mc_event (timestamp, err_count, err_type, err_msg, label)
            VALUES ('old', 1, 'Corrected', 'old error', 'DIMM A1');
    """)
    con.commit()
    con.close()
    return path


def _add(path, sql, *args):
    con = sqlite3.connect(path)
    con.execute(sql, args)
    con.commit()
    con.close()


def test_snapshot_and_only_new_events(db):
    baseline = ras.snapshot(db_path=str(db))
    assert baseline == {"mc_event": 1, "mce_record": 0, "aer_event": 0, "memory_failure_event": 0}
    assert ras.new_events(baseline, db_path=str(db)).events == []

    _add(db, "INSERT INTO mc_event (timestamp, err_count, err_type, err_msg, label) VALUES (?,?,?,?,?)",
         "t1", 3, "Corrected", "read error", "CPU_SrcID#0_MC#0_Chan#1_DIMM#0")
    _add(db, "INSERT INTO mce_record (timestamp, status, bank_name, cpu, socketid, error_msg, mcistatus_msg)"
         " VALUES (?,?,?,?,?,?,?)", "t2", UC_STATUS - (1 << 64), "Bank 4", 7, 1, "L3 error", "Uncorrected")
    _add(db, "INSERT INTO mce_record (timestamp, status, bank_name, cpu, socketid, error_msg, mcistatus_msg)"
         " VALUES (?,?,?,?,?,?,?)", "t3", CE_STATUS - (1 << 64), "Bank 1", 2, 0, "cache", "Corrected")
    _add(db, "INSERT INTO aer_event (timestamp, dev_name, err_type, err_msg) VALUES (?,?,?,?)",
         "t4", "0000:3b:00.0", "Uncorrected (Fatal)", "Surprise Down")
    _add(db, "INSERT INTO memory_failure_event (timestamp, pfn, page_type, action_result) VALUES (?,?,?,?)",
         "t5", "0x1234", "dirty LRU page", "Recovered")

    report = ras.new_events(baseline, db_path=str(db))
    assert len(report.events) == 5
    assert [e.table for e in report.corrected] == ["mc_event", "mce_record"]
    assert {e.table for e in report.uncorrected} == {"mce_record", "aer_event", "memory_failure_event"}
    assert report.corrected[0].count == 3
    assert report.corrected[0].describe().startswith("rasdaemon CE ×3: t1 Corrected: read error")
    assert "socket 1" in report.uncorrected[0].describe()


def test_missing_tables_and_unreadable_db(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert ras.snapshot(db_path=str(empty)) == {}
    assert ras.snapshot(db_path=str(tmp_path / "missing.db")) is None


def test_root_fallback_runs_query_script(db, monkeypatch):
    monkeypatch.setattr(ras.os, "access", lambda *_args: False)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.run([sys.executable, *cmd[1:]], **kwargs)

    baseline = ras.snapshot(run, db_path=str(db))
    assert baseline["mc_event"] == 1 and calls
    _add(db, "INSERT INTO aer_event (timestamp, dev_name, err_type, err_msg) VALUES (?,?,?,?)",
         "t", "dev", "Corrected", "Receiver Error")
    report = ras.new_events(baseline, run, db_path=str(db))
    assert [e.uncorrected for e in report.events] == [False]
    assert ras.snapshot(None, db_path=str(db)) is None


class _Runner(BaseTestRunner):
    _use_watchdog = False

    def build_command(self):
        return [sys.executable, "-c", "import time; time.sleep(0.5)"]


def _run_with_db(db, monkeypatch, insert):
    """Startet einen echten Runner; ``insert`` trägt nach dem Start (während des Tests) ein."""
    monkeypatch.setattr(ras, "rasdaemon_installed", lambda: True)
    monkeypatch.setattr(ras.snapshot, "__defaults__", (None, str(db)))
    monkeypatch.setattr(ras.new_events, "__defaults__", (None, str(db)))
    runner = _Runner(TestParameters(duration_seconds=0))
    runner.start()
    insert()
    runner._stdout_thread.join(timeout=10)
    runner.stop()
    return runner.get_result()


def _mc_event(db, kind, count, label):
    return lambda: _add(db, "INSERT INTO mc_event (timestamp, err_count, err_type, err_msg, label)"
                        " VALUES (?,?,?,?,?)", "t", count, kind, "memory read", label)


def test_runner_fails_on_new_uncorrected_event(db, monkeypatch):
    result = _run_with_db(db, monkeypatch, _mc_event(db, "Uncorrected", 1, "DIMM B2"))
    assert not result.passed
    assert any("rasdaemon UE" in error and "DIMM B2" in error for error in result.errors)


def test_runner_warns_on_corrected_event(db, monkeypatch):
    result = _run_with_db(db, monkeypatch, _mc_event(db, "Corrected", 2, "DIMM A1"))
    assert result.passed
    assert result.errors == []
    assert any(w.startswith("rasdaemon CE ×2") for w in result.warnings)


def test_runner_without_rasdaemon_is_unchanged(db, monkeypatch):
    monkeypatch.setattr(ras, "rasdaemon_installed", lambda: False)
    runner = _Runner(TestParameters(duration_seconds=0))
    runner.start()
    runner._stdout_thread.join(timeout=10)
    runner.stop()
    assert runner.get_result().passed and runner.get_result().warnings == []
