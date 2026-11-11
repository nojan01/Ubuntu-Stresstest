"""Regressionstests für die Härtung der Root-Sitzung (Befunde 1, 3, 4, 5 und 8)."""

import json
import os
import sqlite3
import stat
import subprocess
import threading
import time

import pytest

from hardwaretest.core import ras, root_broker
from hardwaretest.core.root_broker import RootAuthorizationError, RootBroker
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters

# Fake-fio gibt die Jobdatei aus, damit der Test den vom Root-Skript erzeugten
# Inhalt prüfen kann; "stress" hält fio am Leben, bis ein Signal kommt.
_FAKE_FIO = r'''
for a in "$@"; do last=$a; done
echo "ARGS: $*"
echo "JOBFILE-MODE: $(stat -c %a "$last")"
echo "JOBDIR-MODE: $(stat -c %a "$(dirname "$last")")"
echo "--- job ---"; cat "$last"; echo "--- end ---"
exit 0
'''

_FAKE_DCGMI = r'''
case "$1" in
  stress)
    # Kind, das SIGTERM ignoriert (wie ein haengender Treiberaufruf).
    sh -c 'trap "" TERM; while :; do sleep 0.1; done' &
    echo "CHILD $!"
    trap "exit 143" TERM
    while :; do sleep 0.1; done ;;
  *) echo "dcgm $*"; exit 0 ;;
esac
'''


def _install(tmp_path, name, body):
    tool = tmp_path / name
    tool.write_text("#!/bin/sh\n" + body + "\n")
    tool.chmod(0o755)
    return tool


def _broker(tmp_path, **kwargs):
    _install(tmp_path, "fio", _FAKE_FIO)
    _install(tmp_path, "dcgmi", _FAKE_DCGMI)
    fake = tmp_path / "fakepkexec"
    fake.write_text(f'#!/bin/sh\nPATH="{tmp_path}:$PATH" exec "$@"\n')
    fake.chmod(0o755)
    return RootBroker(str(fake), **kwargs)


# ----------------------------------------------------------------- Befund 1: fio-Jobdatei


def test_fio_job_is_written_by_root_script_from_checked_tokens(tmp_path):
    broker = _broker(tmp_path)
    tokens = ["--eta=always", "@hardwaretest", "filename=/dev/null", "size=64M", "rw=read",
              "bs=128k", "runtime=5", "time_based=1", "ioengine=psync", "iodepth=4",
              "numjobs=2", "direct=1", "group_reporting=1"]
    job = broker.submit_fio_job(tokens)
    completed = broker._run_job(job, ["fio"], timeout=10)
    broker.close()
    assert completed.returncode == 0
    out = completed.stdout
    body = out.split("--- job ---\n", 1)[1].split("--- end ---")[0]
    assert body.strip().splitlines() == [
        "[hardwaretest]", "filename=/dev/null", "size=64M", "rw=read", "bs=128k", "runtime=5",
        "time_based=1", "ioengine=psync", "iodepth=4", "numjobs=2", "direct=1",
        "group_reporting=1",
    ]
    args = out.splitlines()[0]
    assert args.startswith("ARGS: --eta=always ")
    assert "/jobs/" in args and args.endswith(".fio")
    # Jobdatei und Verzeichnis gehören dem Root-Skript und sind privat.
    assert "JOBFILE-MODE: 600" in out
    assert "JOBDIR-MODE: 700" in out


@pytest.mark.parametrize("bad", [
    ["@x", "filename=/dev/null", "exec_prerun=/bin/sh"],
    ["@x", "filename=/dev/null", "exec_postrun=id"],
    ["@x", "filename=/dev/null", "ioengine=exec"],
    ["@x", "filename=/dev/null", "ioengine=external:/tmp/evil.so"],
    ["@x", "filename=relative/path"],
    ["@x", "filename=/tmp/../etc/passwd"],
    ["@x", "filename=/dev//null"],
    ["@x", "filename=/dev/null", "write_iolog=/etc/x"],
    ["@x", "filename=/dev/null", "output=/etc/x"],
    ["@x", "filename=/dev/null", "--output=/etc/x"],
    ["@x", "filename=/dev/null", "--exec_prerun=id"],
    ["@x", "filename=/dev/null", "rw=write;id"],
    ["@x", "filename=/dev/null", "rw=trim"],
    ["@x", "filename=/dev/null", "size=10M$(id)"],
    ["filename=/dev/null"],  # key=value vor dem ersten Abschnitt
    ["@x"],  # keine Datei
    ["@x", "filename=/home/user/file", "rw=write"],  # Schreiben nur auf /dev/*
    ["@x", "filename=/home/user/file", "rw=randrw"],
    ["@x", "filename=/dev/null", "verify=crc32c", "verify_pattern=0xdeadbeef"],
])
def test_fio_job_rejects_dangerous_tokens(tmp_path, bad):
    broker = _broker(tmp_path)
    with pytest.raises(RuntimeError):
        broker.submit_fio_job(bad)
    # Die Sitzung bleibt nach einer Ablehnung benutzbar.
    job = broker.submit_fio_job(["@ok", "filename=/dev/null", "rw=read"])
    assert broker._run_job(job, ["fio"], timeout=10).returncode == 0
    broker.close()


def test_fio_read_from_regular_file_is_allowed_but_write_is_not(tmp_path):
    broker = _broker(tmp_path)
    path = str(tmp_path / "data.bin")
    job = broker.submit_fio_job(["@r", f"filename={path}", "rw=randread"])
    assert broker._run_job(job, ["fio"], timeout=10).returncode == 0
    with pytest.raises(RuntimeError):
        broker.submit_fio_job(["@w", f"filename={path}", "rw=randwrite"])
    broker.close()


def test_fio_is_not_accepted_as_plain_run_command(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(RuntimeError):
        broker.submit(["fio", "/dev/null"])
    with pytest.raises(RuntimeError):
        broker.submit(["fio", "--name=x", "--filename=/dev/null"])
    broker.close()


def test_run_resolves_binary_through_command_v(tmp_path):
    broker = _broker(tmp_path)
    broker.start()
    first = broker.run(["dcgmi", "diag", "-r", "1"], timeout=10)
    assert first.returncode == 0 and first.stdout.strip() == "dcgm diag -r 1"
    with pytest.raises(RuntimeError, match="nicht gefunden"):
        broker.run(["ipmitool", "sdr", "elist"], timeout=10)
    broker.close()


# ----------------------------------------------------------------- Befund 3: KILL der Gruppe


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_kill_terminates_whole_process_group_including_stubborn_child(tmp_path):
    broker = _broker(tmp_path)
    job = broker.submit_fio_job(["@x", "filename=/dev/null"])  # nur, um die Sitzung zu starten
    broker.done(job)
    job = broker.submit(["dcgmi", "stress"])
    relay = subprocess.Popen(broker.relay_command(job), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    line = relay.stdout.readline()
    assert line.startswith("CHILD ")
    child = int(line.split()[1])
    assert _pid_alive(child)
    started = time.monotonic()
    broker.kill(job, grace_seconds=1)
    elapsed = time.monotonic() - started
    # Nach KILL ist auch das Kind weg (SIGKILL-Nachlauf) und der Relay endet.
    deadline = time.monotonic() + 5
    while _pid_alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(child)
    assert relay.wait(timeout=10) == 143
    assert elapsed < 10
    broker.done(job)
    broker.close()


def test_kill_rejects_unknown_job_and_keeps_session(tmp_path):
    broker = _broker(tmp_path)
    broker.start()
    with broker._lock:
        reply = broker._request_locked("KILL /etc/passwd 1")
    assert reply.startswith("@@ERR")
    assert broker.is_active()
    broker.close()


class _StressRunner(BaseTestRunner):
    def build_command(self):
        return ["dcgmi", "stress"]


def test_runner_watchdog_kills_root_group_through_broker(tmp_path):
    broker = _broker(tmp_path)
    runner = _StressRunner(TestParameters(1))
    runner.check_ras = False
    runner.root_broker = broker
    runner.terminate_grace_seconds = 1
    child_pid = []
    runner._log = lambda msg: child_pid.append(int(msg.split()[1])) if msg.startswith("CHILD ") else None
    runner.start()
    assert runner.wait_finished(timeout=20)
    assert child_pid and not _pid_alive(child_pid[0])
    assert runner.get_result() is not None
    broker.close()


def test_close_terminates_outstanding_jobs(tmp_path):
    broker = _broker(tmp_path)
    job = broker.submit(["dcgmi", "stress"])
    relay = subprocess.Popen(broker.relay_command(job), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    child = int(relay.stdout.readline().split()[1])
    broker.close()
    deadline = time.monotonic() + 10
    while _pid_alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(child)
    relay.wait(timeout=10)


# ----------------------------------------------------------------- Befund 4: Timeouts


def test_authorization_timeout_reports_stderr(tmp_path):
    fake = _install(tmp_path, "slowpkexec", 'echo "polkit: waiting for agent" >&2; sleep 30')
    broker = RootBroker(str(fake), auth_timeout=0.5)
    started = time.monotonic()
    with pytest.raises(RootAuthorizationError) as info:
        broker.start()
    assert time.monotonic() - started < 10
    assert "keine Antwort" in str(info.value)
    assert "waiting for agent" in str(info.value)
    assert broker._proc is None and not broker.is_active()


def test_close_from_other_thread_aborts_pending_authorization(tmp_path):
    fake = _install(tmp_path, "slowpkexec", "sleep 30")
    broker = RootBroker(str(fake), auth_timeout=30)
    errors = []

    def starter():
        try:
            broker.start()
        except RootAuthorizationError as exc:
            errors.append(exc)

    thread = threading.Thread(target=starter)
    thread.start()
    time.sleep(0.3)
    started = time.monotonic()
    broker.close()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert time.monotonic() - started < 10
    assert errors and "geschlossen" in str(errors[0])


def test_reply_timeout_closes_session_with_stderr(tmp_path):
    # Antwortet mit READY, bleibt danach aber stumm.
    fake = _install(tmp_path, "mutepkexec",
                    'echo "@@HARDWARETEST-ROOT-READY@@"; echo "broker wedged" >&2; sleep 30')
    broker = RootBroker(str(fake), reply_timeout=0.5)
    broker.start()
    assert broker.is_active()
    started = time.monotonic()
    with pytest.raises(RootAuthorizationError) as info:
        broker.submit(["dcgmi", "diag"])
    assert time.monotonic() - started < 10
    assert "antwortet nicht" in str(info.value) and "broker wedged" in str(info.value)
    assert not broker.is_active() and broker._proc is None


def test_lock_is_not_held_during_authorization(tmp_path):
    fake = _install(tmp_path, "slowpkexec", "sleep 2; echo @@HARDWARETEST-ROOT-READY@@; cat >/dev/null")
    broker = RootBroker(str(fake), auth_timeout=10)
    thread = threading.Thread(target=broker.start)
    thread.start()
    time.sleep(0.3)
    assert broker._lock.acquire(timeout=1), "Sperre darf während der Autorisierung frei sein"
    broker._lock.release()
    thread.join(timeout=10)
    broker.close()


# ----------------------------------------------------------------- Befund 5: RAS über Broker


def _make_ras_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE mc_event (id INTEGER PRIMARY KEY, timestamp TEXT, err_count INTEGER, "
                "err_type TEXT, err_msg TEXT, label TEXT)")
    con.execute("INSERT INTO mc_event VALUES (1, '2024-01-01 10:00:00', 1, 'Corrected', 'old', 'DIMM1')")
    con.commit()
    con.close()


def _ras_broker(tmp_path, monkeypatch, db):
    monkeypatch.setattr(root_broker, "ROOT_BROKER_SCRIPT",
                        root_broker.ROOT_BROKER_SCRIPT.replace(ras.DB_PATH, str(db)))
    return _broker(tmp_path)


def test_ras_query_runs_fixed_script_through_broker(tmp_path, monkeypatch):
    db = tmp_path / "ras.db"
    _make_ras_db(db)
    broker = _ras_broker(tmp_path, monkeypatch, db)
    baseline = broker.ras_query(None)
    assert baseline == {"ids": {"mc_event": 1}, "events": []}
    con = sqlite3.connect(db)
    con.execute("INSERT INTO mc_event VALUES (2, '2024-01-01 10:05:00', 3, 'Uncorrected', 'new', 'DIMM2')")
    con.commit()
    con.close()
    data = broker.ras_query(baseline["ids"])
    assert data["ids"] == {"mc_event": 2}
    assert len(data["events"]) == 1 and data["events"][0]["uncorrected"] is True
    broker.close()


def test_ras_module_uses_broker_query_not_python_c(tmp_path, monkeypatch):
    db = tmp_path / "ras.db"
    _make_ras_db(db)
    broker = _ras_broker(tmp_path, monkeypatch, db)
    monkeypatch.setattr(ras, "rasdaemon_installed", lambda: True)
    # Lokal nicht lesbar erzwingen, damit der Root-Pfad genutzt wird.
    monkeypatch.setattr(ras.os, "access", lambda *a, **k: False)
    original_submit = broker.submit

    def guarded_submit(command):
        assert "python" not in command[0] and "-c" not in command
        return original_submit(command)

    broker.submit = guarded_submit
    snap = ras.snapshot(broker.run)
    assert snap == {"mc_event": 1}
    report = ras.new_events(snap, broker.run)
    assert report is not None and report.corrected == [] and report.uncorrected == []
    broker.close()


def test_ras_query_rejects_foreign_db_path_and_bad_baseline(tmp_path):
    broker = _broker(tmp_path)
    assert broker.ras_query(None, db_path=str(tmp_path / "x.db")) is None
    broker.start()
    with broker._lock:
        reply = broker._request_locked('RASQUERY {"a":1;id} 50')
    assert reply.startswith("@@ERR")
    assert broker.is_active()
    broker.close()


def test_ras_query_returns_none_when_db_missing(tmp_path, monkeypatch):
    broker = _ras_broker(tmp_path, monkeypatch, tmp_path / "missing.db")
    assert broker.ras_query(None) is None
    broker.close()


def test_baseline_token_has_no_whitespace():
    token = json.dumps({"mc_event": 5, "mce_record": 0}, separators=(",", ":"))
    assert " " not in token


# ----------------------------------------------------------------- Befund 8: Rechte


def test_job_directory_and_fifo_permissions(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    broker = _broker(tmp_path)
    job = broker.submit(["dcgmi", "stress"])
    try:
        work = os.path.dirname(job)
        # Arbeitsverzeichnis liegt im privaten Runtime-Verzeichnis mit zufälligem Namen.
        assert os.path.dirname(work) == str(runtime)
        assert os.path.basename(work).startswith("hardwaretest-root.")
        assert stat.S_IMODE(os.stat(work).st_mode) == 0o711
        assert stat.S_IMODE(os.stat(job).st_mode) == 0o711
        for name in ("out", "err", "status"):
            mode = os.stat(os.path.join(job, name)).st_mode
            assert stat.S_IMODE(mode) == 0o600, name
            assert os.stat(os.path.join(job, name)).st_uid == os.getuid()
        assert stat.S_ISFIFO(os.stat(os.path.join(job, "out")).st_mode)
        assert stat.S_IMODE(os.stat(os.path.join(work, "jobs")).st_mode) == 0o700
    finally:
        relay = subprocess.Popen(broker.relay_command(job), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        broker.kill(job, grace_seconds=1)
        relay.wait(timeout=10)
        broker.close()
    assert not os.path.exists(work), "EXIT-Trap muss das Arbeitsverzeichnis entfernen"


def test_runtime_dir_with_wrong_permissions_falls_back_to_tmp(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    broker = _broker(tmp_path)
    job = broker.submit_fio_job(["@x", "filename=/dev/null"])
    work = os.path.dirname(job)
    assert os.path.dirname(work) != str(runtime)
    broker._run_job(job, ["fio"], timeout=10)
    broker.close()


def test_script_uses_restrictive_umask_and_fixed_path_under_pkexec():
    script = root_broker.ROOT_BROKER_SCRIPT
    assert "umask 077" in script
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin" in script
    assert subprocess.run(["sh", "-n", "-c", script]).returncode == 0


# ----------------------------------------------------------------- smartctl für SATA/SAS/USB


def test_smartctl_is_only_allowed_read_only_for_disk_and_nvme_paths(tmp_path):
    _install(tmp_path, "smartctl", 'echo "smart $*"; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    assert broker.run(["smartctl", "-a", "-j", "/dev/sda"], timeout=10).stdout.strip() == "smart -a -j /dev/sda"
    assert broker.run(["smartctl", "-a", "-j", "/dev/nvme0"], timeout=10).returncode == 0
    for bad in (["smartctl", "-t", "long", "/dev/sda"], ["smartctl", "-a", "-j", "/dev/sda1"],
                ["smartctl", "-a", "-j", "/etc/passwd"], ["smartctl", "-s", "off", "/dev/sda"]):
        with pytest.raises(RuntimeError):
            broker.run(bad, timeout=10)
    broker.close()


# ----------------------------------------------------------------- zpool scrub (Gesamttest)


def test_zpool_is_only_allowed_to_start_or_stop_a_scrub(tmp_path):
    _install(tmp_path, "zpool", 'echo "zpool $*"; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    assert broker.run(["zpool", "scrub", "tank"], timeout=10).stdout.strip() == "zpool scrub tank"
    assert broker.run(["zpool", "scrub", "-s", "rpool-1.a:b_c"], timeout=10).returncode == 0
    for bad in (["zpool", "status"], ["zpool", "destroy", "tank"], ["zpool", "scrub"], ["zpool", "scrub", "-s"],
                ["zpool", "scrub", "-tank"], ["zpool", "scrub", "-w", "tank"], ["zpool", "scrub", "x" * 65],
                ["zpool", "scrub", "tank", "extra"], ["zpool", "scrub", "-s", "1tank"]):
        with pytest.raises(RuntimeError):
            broker.run(bad, timeout=10)
    broker.close()


# ------------------------------------------------------- swapoff/swapon (RAM-Fuelltest im Gesamttest)


def test_swapoff_and_swapon_are_limited_to_all_or_absolute_paths(tmp_path):
    _install(tmp_path, "swapoff", 'echo "swapoff $*"; exit 0')
    _install(tmp_path, "swapon", 'echo "swapon $*"; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    assert broker.run(["swapoff", "-a"], timeout=10).stdout.strip() == "swapoff -a"
    assert broker.run(["swapon", "-a"], timeout=10).stdout.strip() == "swapon -a"
    assert broker.run(["swapon", "/dev/nvme0n1p3"], timeout=10).returncode == 0
    assert broker.run(["swapon", "/swap.img"], timeout=10).returncode == 0
    for bad in (["swapoff"], ["swapoff", "/dev/sda2"], ["swapon"], ["swapon", "-a", "extra"],
                ["swapon", "swap.img"], ["swapon", "/tmp/../swap"], ["swapon", "-p", "10"], ["swapon", "/"]):
        with pytest.raises(RuntimeError):
            broker.run(bad, timeout=10)
    broker.close()


# ------------------------------------------------- Aufrufer-UID unter sudo -A statt pkexec


def _caller_snippet():
    """Die Zeilen des Root-Skripts, die den aufrufenden Benutzer bestimmen."""
    lines = root_broker._ROOT_BROKER_TEMPLATE.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("caller="))
    end = next(i for i, l in enumerate(lines) if l.startswith("case \"$caller\""))
    return "\n".join(lines[start:end + 1]) + "\necho \"$caller\"\n"


@pytest.mark.parametrize("env, expected", [
    ({"PKEXEC_UID": "1001"}, "1001"),
    ({"SUDO_UID": "1002"}, "1002"),
    ({"PKEXEC_UID": "1001", "SUDO_UID": "1002"}, "1001"),
    ({"SUDO_UID": "nicht-numerisch"}, str(os.getuid())),
    ({}, str(os.getuid())),
])
def test_caller_uid_from_pkexec_or_sudo(env, expected):
    # Ohne SUDO_UID gehörten out/err/status unter sudo -A root:0600 und die App
    # konnte sie nicht lesen ("cat: .../out: Keine Berechtigung").
    clean = {k: v for k, v in os.environ.items() if k not in {"PKEXEC_UID", "SUDO_UID"}}
    out = subprocess.run(["sh", "-c", _caller_snippet()], env={**clean, **env},
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == expected


# ------------------------------------------------- Hersteller-NIC-Werkzeuge (core/nic_vendor.py)


def test_vendor_nic_tools_allow_only_read_only_queries(tmp_path):
    for name in ("niccli", "bnxtnvm", "epct64e", "qlnxcli", "mlxfwmanager", "mlxconfig", "mlxlink",
                 "sfupdate", "sfboot", "sfreport", "QCScli"):
        _install(tmp_path, name, f'echo "{name} $#:$*"; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    good = [
        ["niccli", "--list"], ["niccli", "-i", "1", "show"], ["niccli", "-i", "2", "show", "--pkg_ver"],
        ["niccli", "-i", "1", "link", "-s"], ["niccli", "-i", "1", "cable", "-m", "--show"],
        ["bnxtnvm", "-dev=ens1f0", "device_info"], ["epct64e", "-devices"], ["epct64e", "-nic=1", "-get"],
        ["qlnxcli", "-i"], ["mlxfwmanager", "--query", "-d", "0000:37:00.0"],
        ["mlxconfig", "-d", "37:00.0", "query"], ["mlxlink", "-d", "0000:37:00.0", "--show_module", "--show_counters"],
        ["sfupdate"], ["sfboot"], ["sfreport", "-m", "-"],
    ]
    for cmd in good:
        out = broker.run(cmd, timeout=10).stdout.strip()
        assert out == f"{cmd[0]} {len(cmd) - 1}:{' '.join(cmd[1:])}", cmd
    bad = [
        ["niccli", "-i", "1", "nvm", "--install"], ["niccli", "-i", "x", "show"], ["niccli", "--list", "extra"],
        ["bnxtnvm", "-dev=ens1f0", "install"], ["bnxtnvm", "-dev=../x", "device_info"],
        ["epct64e", "-nic=1", "-set"], ["qlnxcli", "-u"], ["mlxfwmanager", "-d", "0000:37:00.0", "--update"],
        ["mlxconfig", "-d", "37:00.0", "set"], ["mlxconfig", "-d", "/dev/mst/x", "query"],
        ["mlxlink", "-d", "0000:37:00.0", "--port_state", "down"], ["sfupdate", "--write"], ["sfreport"],
    ]
    for cmd in bad:
        with pytest.raises(RuntimeError, match="nicht erlaubt"):
            broker.run(cmd, timeout=10)
    broker.close()


def test_qcscli_command_string_is_rejoined_into_one_argument(tmp_path):
    _install(tmp_path, "QCScli", 'for a in "$@"; do echo "[$a]"; done; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    out = broker.run(["QCScli", "-protocol", "local", "list -f BDF NDIS"], timeout=10).stdout
    assert out.split("\n")[:3] == ["[-protocol]", "[local]", "[list -f BDF NDIS]"]
    mac = "9643dbf00084"
    out = broker.run(["QCScli", "-protocol", "local", "-t", "NDIS", "-f", "MAC", "-i", mac, "stats"], timeout=10).stdout
    assert "[stats]" in out and f"[{mac}]" in out
    for bad in (["QCScli", "-protocol", "local", "list"], ["QCScli", "-protocol", "local", "-t", "NDIS", "-f", "MAC", "-i", "zz", "info"],
                ["QCScli", "-protocol", "local", "-t", "NDIS", "-f", "MAC", "-i", mac, "update"]):
        with pytest.raises(RuntimeError, match="nicht erlaubt"):
            broker.run(bad, timeout=10)
    broker.close()


def _vendor_path_snippet():
    lines = root_broker._ROOT_BROKER_TEMPLATE.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("vendor_prefix_ok()"))
    end = next(i for i, l in enumerate(lines) if i > start and l == "}")
    return "\n".join(lines[start:end + 1]) + "\nvendor_prefix_ok() { return 0; }\n"


def _vendor_path_ok(path, caller):
    script = _vendor_path_snippet() + f'caller={caller}\nvendor_path_ok "$1" && echo OK || echo NO\n'
    return subprocess.run(["sh", "-c", script, "x", str(path)], capture_output=True, text=True).stdout.strip()


def test_vendor_tool_with_path_must_not_be_replaceable_by_caller(tmp_path):
    other = 65534                      # fremde UID (nobody) als Aufrufer
    tool = _install(tmp_path, "niccli", 'echo niccli')
    link = tmp_path / "link"
    link.symlink_to(tool)
    assert _vendor_path_ok(tool, other) == "OK"
    assert _vendor_path_ok(link, other) == "OK"
    assert _vendor_path_ok(tool, os.getuid()) == "NO"           # gehört dem Aufrufer selbst
    tool.chmod(0o777)
    assert _vendor_path_ok(tool, other) == "NO"                 # weltbeschreibbar
    tool.chmod(0o775)
    assert _vendor_path_ok(tool, other) == "OK"                 # gruppenbeschreibbar, Aufrufer nicht in Gruppe
    tool.chmod(0o644)
    assert _vendor_path_ok(tool, other) == "NO"                 # nicht ausführbar
    tool.chmod(0o755)
    tmp_path.chmod(0o777)
    assert _vendor_path_ok(tool, other) == "NO"                 # Ordner weltbeschreibbar
    tmp_path.chmod(0o755)
    assert _vendor_path_ok(tmp_path / "missing", other) == "NO"
    # Pfadpräfix: nur /usr und /opt
    script = _vendor_path_snippet().replace("vendor_prefix_ok() { return 0; }\n", "")
    for path, expect in (("/opt/niccli/niccli", "OK"), ("/usr/local/sbin/x", "OK"), ("/home/u/niccli", "NO"), ("/tmp/x", "NO")):
        out = subprocess.run(["sh", "-c", script + 'vendor_prefix_ok "$1" && echo OK || echo NO', "x", path],
                             capture_output=True, text=True).stdout.strip()
        assert out == expect, path


def test_vendor_tool_with_untrusted_path_is_rejected_by_broker(tmp_path):
    tool = _install(tmp_path, "niccli", 'echo "niccli $*"; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    # Testdatei gehört dem Aufrufer und liegt nicht unter /usr|/opt -> abgelehnt
    with pytest.raises(RuntimeError, match="nicht vertrauensw"):
        broker.run([str(tool), "--list"], timeout=10)
    assert broker.run(["niccli", "--list"], timeout=10).stdout.strip() == "niccli --list"
    broker.close()


def test_nvmupdate_runs_in_its_program_directory(tmp_path):
    sub = tmp_path / "intel"
    sub.mkdir()
    tool = sub / "nvmupdate64e"
    tool.write_text('#!/bin/sh\necho "$(pwd) $*"\n')
    tool.chmod(0o755)
    (tmp_path / "nvmupdate64e").symlink_to(tool)   # Symlink im PATH (wie /usr/local/sbin)
    broker = _broker(tmp_path)
    broker.start()
    assert broker.run(["nvmupdate64e", "-i"], timeout=10).stdout.strip() == f"{sub.resolve()} -i"
    with pytest.raises(RuntimeError, match="nicht erlaubt"):
        broker.run(["nvmupdate64e", "-u"], timeout=10)
    broker.close()


# ------------------------------------------------- Protokolle löschen (ProLiant Info)


def test_log_clear_commands_are_limited_to_the_fixed_forms(tmp_path):
    _install(tmp_path, "ipmitool", 'echo "ipmitool $*"; exit 0')
    _install(tmp_path, "ilorest", 'echo "ilorest $*"; exit 0')
    broker = _broker(tmp_path)
    broker.start()
    from hardwaretest.core import ipmi
    for command in (*ipmi.CLEAR_LOG_COMMANDS.values(), ipmi.ILOREST_IEL_CMD, ipmi.ILOREST_IML_CMD):
        assert broker.run(command, timeout=10).stdout.strip() == " ".join(command)
    for bad in (["ipmitool", "sel", "clear", "extra"], ["ipmitool", "sel", "delete", "1"],
                ["ilorest", "--nologo", "serverlogs", "--selectlog=AHS", "--clearlog"],
                ["ilorest", "--nologo", "serverlogs", "--selectlog=IML", "--clearlog", "--url=10.0.0.1"],
                ["ilorest", "serverlogs", "--selectlog=IML", "--clearlog"], ["ilorest", "factorydefaults"]):
        with pytest.raises(RuntimeError, match="nicht erlaubt"):
            broker.run(bad, timeout=10)
    broker.close()
