"""Hardwarefehler aus der rasdaemon-Datenbank (MCE, EDAC, PCIe-AER …) während eines Tests.

rasdaemon protokolliert Machine-Check-, Speicher- und PCIe-Fehler in
``/var/lib/rasdaemon/ras-mc_event.db``. Beim Teststart wird die höchste
Eintragsnummer je Tabelle gemerkt; am Ende werden nur die neuen Einträge
ausgewertet. Unkorrigierbare Fehler lassen den Test fehlschlagen, korrigierte
werden als Warnung gemeldet.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3  # noqa: F401 - für PyInstaller; genutzt in QUERY_SCRIPT
import subprocess
import sys
from typing import Callable, Optional

Runner = Callable[..., subprocess.CompletedProcess]

DB_PATH = "/var/lib/rasdaemon/ras-mc_event.db"
_MAX_EVENTS = 50

# Läuft im eigenen Prozess oder – falls die Datenbank nur für root lesbar ist –
# per ``python3 -c`` über die Root-Sitzung. Ausgabe: JSON mit Zählerstand und
# neuen Ereignissen.
QUERY_SCRIPT = r'''
import json, sqlite3, sys

def query(path, baseline, limit):
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ids, events = {}, []

        def rows(table, columns):
            if table not in tables:
                return []
            ids[table] = con.execute("SELECT COALESCE(MAX(id), 0) FROM %s" % table).fetchone()[0]
            if baseline is None:
                return []
            start = baseline.get(table, 0)
            return con.execute("SELECT %s FROM %s WHERE id > ? ORDER BY id LIMIT ?" % (columns, table),
                               (start, limit)).fetchall()

        def add(table, uncorrected, count, text, timestamp):
            events.append({"table": table, "uncorrected": bool(uncorrected),
                           "count": max(1, int(count or 1)), "text": " ".join(str(text or "").split())[:300],
                           "timestamp": str(timestamp or "")})

        for ts, err_type, count, msg, label in rows("mc_event", "timestamp, err_type, err_count, err_msg, label"):
            kind = str(err_type or "")
            add("mc_event", kind.lower() not in ("corrected", "info", "informational"), count,
                "%s: %s %s" % (kind, msg or "", label or ""), ts)
        for ts, status, bank, cpu, socket, msg, mci in rows(
                "mce_record", "timestamp, status, bank_name, cpu, socketid, error_msg, mcistatus_msg"):
            uc = (int(status or 0) & 0xFFFFFFFFFFFFFFFF) >> 61 & 1
            add("mce_record", uc, 1, "MCE CPU %s socket %s %s: %s %s" % (cpu, socket, bank or "", msg or "", mci or ""), ts)
        for ts, dev, err_type, msg in rows("aer_event", "timestamp, dev_name, err_type, err_msg"):
            kind = str(err_type or "")
            add("aer_event", not kind.lower().startswith("corrected"), 1, "PCIe AER %s %s: %s" % (dev, kind, msg or ""), ts)
        # CPER-Schweregrad: 0 behebbar (aber unkorrigiert), 1 fatal, 2 korrigiert, 3 Info.
        for ts, severity, count, text in rows("extlog_event", "timestamp, severity, error_count, fru_text"):
            add("extlog_event", int(severity or 0) in (0, 1), count, "extlog severity %s %s" % (severity, text or ""), ts)
        for ts, severity, text in rows("non_standard_event", "timestamp, severity, fru_text"):
            kind = str(severity or "")
            add("non_standard_event", kind.lower() not in ("corrected", "informational", "info"), 1,
                "%s %s" % (kind, text or ""), ts)
        for ts, pfn, page, action in rows("memory_failure_event", "timestamp, pfn, page_type, action_result"):
            add("memory_failure_event", True, 1, "memory failure pfn %s %s: %s" % (pfn, page or "", action or ""), ts)
        for ts, dev, sector, error in rows("disk_errors", "timestamp, dev, sector, error"):
            add("disk_errors", True, 1, "disk %s sector %s: %s" % (dev, sector, error or ""), ts)
        return {"ids": ids, "events": events}
    finally:
        con.close()

if __name__ == "__main__":
    print(json.dumps(query(sys.argv[1], json.loads(sys.argv[2]), int(sys.argv[3]))))
'''

_namespace: dict = {"__name__": "rasdaemon_query"}
exec(compile(QUERY_SCRIPT, "<rasdaemon-query>", "exec"), _namespace)  # eigener, fester Code
_query = _namespace["query"]


@dataclass
class RasEvent:
    table: str
    uncorrected: bool
    count: int
    text: str
    timestamp: str = ""

    def describe(self) -> str:
        # UE/CE wie bei EDAC: unkorrigierbar (uncorrected) / korrigiert (corrected).
        kind = "UE" if self.uncorrected else "CE"
        prefix = f"{self.timestamp} " if self.timestamp else ""
        times = f" ×{self.count}" if self.count > 1 else ""
        return f"rasdaemon {kind}{times}: {prefix}{self.text}".strip()


@dataclass
class RasReport:
    events: list[RasEvent]

    @property
    def uncorrected(self) -> list[RasEvent]:
        return [event for event in self.events if event.uncorrected]

    @property
    def corrected(self) -> list[RasEvent]:
        return [event for event in self.events if not event.uncorrected]


def rasdaemon_installed() -> bool:
    return bool(shutil.which("rasdaemon") or Path("/usr/sbin/rasdaemon").exists() or Path(DB_PATH).exists())


def default_runner() -> Optional[Runner]:
    """Root ohne Passwortabfrage: direkt als root, sonst ``sudo -n`` (Live-ISO)."""
    if os.geteuid() == 0:
        return subprocess.run
    if not shutil.which("sudo"):
        return None

    def run(cmd, **kwargs):
        return subprocess.run(["sudo", "-n", *cmd], **kwargs)
    return run


def _run_query(baseline: Optional[dict], run: Optional[Runner], db_path: str) -> Optional[dict]:
    try:
        if os.access(db_path, os.R_OK) and os.access(os.path.dirname(db_path) or ".", os.R_OK | os.X_OK):
            return _query(db_path, baseline, _MAX_EVENTS)
    except Exception:  # z. B. gesperrt oder kaputt; dann über root versuchen
        pass
    if run is None:
        return None
    python = shutil.which("python3") or sys.executable
    try:
        result = run([python, "-c", QUERY_SCRIPT, db_path, json.dumps(baseline), str(_MAX_EVENTS)],
                     capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return None
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


def snapshot(run: Optional[Runner] = None, db_path: str = DB_PATH) -> Optional[dict[str, int]]:
    """Höchste Eintragsnummer je Tabelle; ``None`` wenn rasdaemon nicht lesbar ist."""
    if db_path == DB_PATH and not rasdaemon_installed():
        return None
    data = _run_query(None, run, db_path)
    if not data or not isinstance(data.get("ids"), dict):
        return None
    return {str(key): int(value) for key, value in data["ids"].items()}


def new_events(baseline: dict[str, int], run: Optional[Runner] = None,
               db_path: str = DB_PATH) -> Optional[RasReport]:
    """Seit ``baseline`` hinzugekommene Ereignisse; ``None`` wenn nicht lesbar."""
    data = _run_query(baseline, run, db_path)
    if not data or not isinstance(data.get("events"), list):
        return None
    events = []
    for item in data["events"]:
        try:
            events.append(RasEvent(str(item["table"]), bool(item["uncorrected"]), int(item["count"]),
                                   str(item["text"]), str(item.get("timestamp", ""))))
        except (KeyError, TypeError, ValueError):
            continue
    return RasReport(events)
