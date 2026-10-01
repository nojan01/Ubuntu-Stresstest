"""Pool-based ZFS diagnostics using options supported by Ubuntu 24.04/26.04."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import sys


@dataclass(frozen=True)
class ZfsPool:
    name: str
    guid: str
    health: str
    size: int
    allocated: int
    free: int


@dataclass(frozen=True)
class ZfsStatus:
    state: str
    scan: str
    scan_result: str
    progress: float | None
    has_errors: bool
    raw: str


def run_zpool(args: list[str]) -> str:
    executable = shutil.which("zpool")
    if not executable:
        raise FileNotFoundError("ZFS tools missing / ZFS-Werkzeuge fehlen: sudo apt install zfsutils-linux")
    env = {**os.environ, "LC_ALL": "C"}
    if getattr(sys, "frozen", False):
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
        else:
            env.pop("LD_LIBRARY_PATH", None)
    result = subprocess.run([executable, *args], capture_output=True, text=True,
                            errors="replace", timeout=20, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"zpool exit {result.returncode}")
    return result.stdout


def list_pools() -> list[ZfsPool]:
    output = run_zpool(["list", "-H", "-p", "-o", "name,guid,health,size,alloc,free"])
    pools = []
    for line in output.splitlines():
        if not line.strip() or line.strip() == "no pools available":
            continue
        fields = line.split()
        if len(fields) != 6 or not fields[1].isdigit():
            raise ValueError("Unrecognized ZFS inventory / ZFS-Inventar nicht auswertbar.")
        pools.append(ZfsPool(fields[0], fields[1], fields[2], *(int(v) for v in fields[3:])))
    return pools


def parse_status(raw: str) -> ZfsStatus:
    state_match = re.search(r"^\s*state:\s*(\S+)", raw, re.M)
    scan_match = re.search(r"^\s*scan:\s*(.*?)(?=^\s*(?:config:|errors:)|\Z)", raw, re.M | re.S)
    state = state_match[1] if state_match else "UNKNOWN"
    scan = " ".join(scan_match[1].split()) if scan_match else ""
    progress_match = re.search(r"([\d.]+)% done", scan)
    progress = float(progress_match[1]) if progress_match else None
    if "resilver in progress" in scan:
        result = "resilver"
    elif "scrub in progress" in scan:
        result = "running"
    elif "scrub paused" in scan:
        result = "paused"
    elif re.search(r"scrub cancel(?:ed|led)|scrub stopped", scan):
        result = "cancelled"
    elif re.search(r"scrub repaired .*? with \d+ errors", scan):
        count = int(re.search(r"with (\d+) errors", scan)[1])
        result = "passed" if count == 0 else "failed"
    elif not scan or "none requested" in scan:
        result = "none"
    else:
        result = "unknown"
    errors_match = re.search(r"^\s*errors:\s*(.*)", raw, re.M)
    has_errors = state != "ONLINE" or result == "failed" or errors_match is None
    if errors_match and errors_match[1].strip() != "No known data errors":
        has_errors = True
    for line in raw.splitlines():
        match = re.match(r"\s*\S+\s+(?:ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL|REMOVED|AVAIL)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s|$)", line)
        if match and any(int(value) for value in match.groups()):
            has_errors = True
    return ZfsStatus(state, scan, result, progress, has_errors, raw)


def read_status(pool: ZfsPool) -> ZfsStatus:
    current = next((p for p in list_pools() if p.guid == pool.guid), None)
    if current is None or current.name != pool.name:
        raise ValueError("ZFS pool changed / ZFS-Pool geändert. Refresh / Aktualisieren.")
    return parse_status(run_zpool(["status", "-P", "-p", current.name]))


def control_pool(action: str, guid: str) -> str:
    if action not in {"start", "stop"} or not guid.isdigit():
        raise ValueError("Invalid ZFS action / Ungültige ZFS-Aktion.")
    pool = next((p for p in list_pools() if p.guid == guid), None)
    if pool is None:
        raise ValueError("Pool not imported / Pool nicht importiert. Refresh / Aktualisieren.")
    status = read_status(pool)
    if action == "start":
        if status.scan_result in {"running", "resilver"}:
            raise ValueError("Scrub/resilver already running / Scrub oder Resilver läuft bereits.")
        if status.state not in {"ONLINE", "DEGRADED"}:
            raise ValueError("Pool unavailable / Pool nicht verfügbar. " + status.state)
        if run_zpool(["get", "-H", "-o", "value", "readonly", pool.name]).strip() != "off":
            raise ValueError("Read-only pool / Schreibgeschützter Pool. Scrub unavailable.")
        return run_zpool(["scrub", pool.name])
    if status.scan_result not in {"running", "paused"}:
        raise ValueError("No active scrub / Kein aktiver Scrub. Resilver cannot be stopped here.")
    return run_zpool(["scrub", "-s", pool.name])


def worker_command(action: str, guid: str) -> list[str]:
    program = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "hardwaretest"]
    return [*program, "--zfs-worker", action, "--guid", guid]


def worker_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--guid", required=True)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise PermissionError("Administrator authentication required / Administratorrechte erforderlich.")
        print(control_pool(args.action, args.guid), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
