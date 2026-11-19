"""Sitzungsprotokoll: Zeitachse, Vorhaltung, unsauberes Ende und Anbindung der Tests."""

from datetime import datetime, timedelta
import sys
import subprocess
import time

import pytest

from hardwaretest.core import report, session_log, test_plan
from hardwaretest.core.test_runner import BaseTestRunner, TestParameters


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _log(tmp_path, clock, **options):
    return session_log.SessionLog(tmp_path, clock=clock, sample_hardware=False, state_interval=3600, **options)


def test_lines_rotate_hourly_and_old_files_are_purged_after_retention(tmp_path):
    clock = Clock(datetime(2026, 11, 13, 10, 59, 58))
    log = _log(tmp_path, clock, retention=24)
    log.start()
    log.write("stress-ng", "erste Zeile\nzweite Zeile")
    clock.now += timedelta(seconds=5)  # 11:00:03 -> neue Stundendatei
    log.write("stress-ng", "nach dem Wechsel | mit Trenner", important=True)
    assert [p.name for p in session_log.log_files(tmp_path)] == [
        "hwtest-protokoll-20261113-10.log", "hwtest-protokoll-20261113-11.log"]
    lines = [line for line, _raw in session_log.read_lines(tmp_path, datetime(2026, 11, 13, 10, 0).astimezone())]
    assert [(line.source, line.text) for line in lines][1:] == [
        ("stress-ng", "erste Zeile"), ("stress-ng", "zweite Zeile"), ("stress-ng", "nach dem Wechsel | mit Trenner")]
    clock.now += timedelta(hours=24)  # 10-Uhr-Datei ist jetzt älter als 24 h, die 11-Uhr-Datei genau an der Grenze
    log.write("stress-ng", "ein Tag später")
    assert [p.name for p in session_log.log_files(tmp_path)] == [
        "hwtest-protokoll-20261113-11.log", "hwtest-protokoll-20261114-11.log"]
    log.stop()


def test_retention_is_never_below_24_hours(monkeypatch):
    monkeypatch.setenv("HARDWARETEST_LOG_HOURS", "2")
    assert session_log.retention_hours() == 24
    monkeypatch.setenv("HARDWARETEST_LOG_HOURS", "100")
    assert session_log.retention_hours() == 100


def test_unclean_end_reports_time_and_running_tests(tmp_path):
    clock = Clock(datetime(2026, 11, 13, 22, 15, 0))
    crashed = session_log.SessionLog(tmp_path, clock=clock, sample_hardware=False, state_interval=0.02)
    assert crashed.start() is None
    crashed.test_started("1", "stress-ng", "stress-ng --cpu 64")
    crashed.test_started("2", "Monitoring")
    deadline = time.monotonic() + 5
    while "aktiv=[Monitoring, stress-ng]" not in session_log.log_files(tmp_path)[0].read_text():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    clock.now += timedelta(minutes=3)
    crashed.write("stress-ng", "letzte Ausgabe vor dem Einfrieren")
    crashed._stop.set()  # Prozess „stirbt“: kein SITZUNG | ENDE

    clock.now += timedelta(minutes=30)
    following = _log(tmp_path, clock)
    unclean = following.start()
    assert unclean is not None
    assert unclean.last_time.replace(tzinfo=None) == datetime(2026, 11, 13, 22, 18, 0)
    assert unclean.active_tests == "Monitoring, stress-ng"
    assert unclean.last_lines[-1].endswith("letzte Ausgabe vor dem Einfrieren")
    following.stop()
    assert session_log.find_unclean(tmp_path) is None  # sauber beendet


class _FailingRunner(BaseTestRunner):
    def build_command(self):
        return [sys.executable, "-c", "print('lauf 1'); print('ECC error on DIMM 3'); raise SystemExit(2)"]


@pytest.fixture
def running_log(tmp_path):
    assert session_log.start(tmp_path, sample_hardware=False, state_interval=3600) is None
    try:
        yield tmp_path
    finally:
        session_log.stop()


def _texts(directory):
    return [f"{line.source}: {line.text}" for line, _raw in
            session_log.read_lines(directory, datetime.now().astimezone() - timedelta(minutes=5))]


def test_runner_output_start_end_and_errors_reach_the_log_once(running_log):
    seen = []
    plan = test_plan.TestPlanExecutor(test_plan.TestPlanConfig(output_dir=running_log), log_fn=seen.append)
    runner = _FailingRunner(TestParameters(30), log_fn=plan._log)
    runner.start()
    runner._process.wait(timeout=10)
    runner._stdout_thread.join(10)
    runner.stop()  # beendet auch den Watchdog
    plan._log("eigene Zeile des Gesamttests")
    texts = _texts(running_log)
    assert any(text.startswith("Failing: TEST START: ") for text in texts)
    assert texts.count("Failing: ECC error on DIMM 3") == 1  # nicht zusätzlich als Gesamttest-Zeile
    assert "Failing: FEHLER: ECC error on DIMM 3" in texts
    assert any(text.startswith("Failing: TEST ENDE: ") and "Exit-Code 2" in text for text in texts)
    assert "Gesamttest: eigene Zeile des Gesamttests" in texts
    assert "ECC error on DIMM 3" in seen and "eigene Zeile des Gesamttests" in seen  # Oberfläche bekommt weiter alles
    assert session_log._session.active_tests() == []


def test_failed_reports_embed_the_minutes_before_the_failure(running_log):
    session_log.write("KERNEL", "mce: [Hardware Error]: Machine check events logged", important=True)
    failed = report.render_html(report.Report(test="stress-ng", title="stress-ng", status=report.STATUS_FAILED))
    passed = report.render_html(report.Report(test="stress-ng", title="stress-ng", status=report.STATUS_PASSED))
    assert "Sitzungsprotokoll (10 Minuten vor dem Fehler)" in failed
    assert "Machine check events logged" in failed and str(running_log) in failed
    assert "Machine check events logged" not in passed


def test_without_started_log_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(session_log, "default_directory", lambda: tmp_path / "nie")
    session_log.write("x", "y")
    session_log.test_started("1", "x")
    assert session_log.excerpt() == "" and not (tmp_path / "nie").exists()


_NVIDIA = {"gpu": "NVIDIA A40", "vram_total": 48305799168, "vram_used": 1,
           "device": {"index": 1, "name": "NVIDIA A40", "mem_total": 48305799168, "mem_used": 12618563584,
                      "util_gpu": 97, "util_mem": 41, "temp": 71, "power_w": 280.5, "power_limit_w": 300,
                      "fan": 62, "gfx_clock": 1980, "pcie_gen": 4, "pcie_width": 16, "throttle": []},
           "processes": [{"name": "Xorg", "vram_bytes": None, "gpu_pct": 1.0},
                         {"name": "python", "vram_bytes": 11811160064, "gpu_pct": 96.4}]}


def test_gpu_snapshot_line_and_events_from_gputaskman(tmp_path):
    line, facts = session_log.format_gpu_snapshot(_NVIDIA)
    assert line == ("gpu1[NVIDIA A40] last=97% speicherlast=41% vram=12034/46068MiB temp=71C leistung=280.5/300W "
                    "fan=62% takt=1980MHz pcie=gen4x16 top=[python 11264MiB 96%; Xorg 1%]")
    assert facts == {"throttle": (), "link": "gen4x16"}
    sparse, _facts = session_log.format_gpu_snapshot(
        {"gpu": "Intel Graphics", "vram_used": 625434624, "device": {"index": 0, "temp": 45, "throttle": []}})
    assert sparse == "gpu0[Intel Graphics] vram=596MiB temp=45C"  # fehlende Werte erscheinen nicht als 0

    state = tmp_path / "state"
    tool = tmp_path / "gputaskman"
    tool.write_text(f"""#!/bin/sh
[ -z "$LD_LIBRARY_PATH" ] || exit 9
case "$*" in
  --list-gpus) echo "1: NVIDIA A40 (NVIDIA, LUID 0x1)";;
  "--json --no-system --gpu 1") cat "{state}";;
  *) exit 3;;
esac
""")
    tool.chmod(0o755)
    written = []
    sampler = session_log._GpuSampler(lambda source, text, important=False: written.append(text), str(tool))
    import json
    import os
    os.environ["LD_LIBRARY_PATH"] = "/opt/hardwaretest/_internal"
    try:
        for throttle, width in (([], 16), (["thermal"], 16), (["thermal"], 8), ([], 8)):
            snapshot = json.loads(json.dumps(_NVIDIA))
            snapshot["device"].update(throttle=throttle, pcie_width=width)
            state.write_text(json.dumps(snapshot))
            sampler.sample()
        state.write_text("kein JSON")
        sampler.sample()
        sampler.sample()
    finally:
        del os.environ["LD_LIBRARY_PATH"]
    events = [text for text in written if not text.startswith("gpu1[")]
    assert events == ["EREIGNIS: gpu1 gedrosselt: thermal",
                      "EREIGNIS: gpu1 PCIe-Verbindung geändert: gen4x16 -> gen4x8",
                      "EREIGNIS: gpu1 Drosselung beendet",
                      events[-1]]
    assert events[-1].startswith("gputaskman nicht auswertbar: ")  # nur einmal gemeldet
    assert sum(text.startswith("gpu1[") for text in written) == 4
    assert not sampler.due()


def test_sensor_details_list_every_temperature_and_fan(monkeypatch):
    from types import SimpleNamespace as S

    from hardwaretest.core import ipmi, monitoring

    monkeypatch.setattr(session_log.psutil, "sensors_temperatures", lambda: {
        "coretemp": [S(label="Package id 0", current=71.0), S(label="Core 0", current=69.4)],
        "nvme": [S(label="", current=38.0)]}, raising=False)
    monkeypatch.setattr(session_log.psutil, "sensors_fans", lambda: {"nct6798": [S(label="fan2", current=1180)]},
                        raising=False)
    assert session_log.collect_sensor_details() == (
        ["temp[coretemp.Package id 0]=71C", "temp[coretemp.Core 0]=69C", "temp[nvme.0]=38C"],
        ["fan[nct6798.fan2]=1180rpm"])

    # Ohne hwmon-Lüfter: ACPI-Stufen; ganz ohne Sensoren (VM) bleiben beide Listen leer.
    monkeypatch.setattr(session_log.psutil, "sensors_fans", lambda: {}, raising=False)
    monkeypatch.setattr(monitoring, "acpi_fan_metrics", lambda: {"fan.acpi.4.percent": 100})
    assert session_log.collect_sensor_details()[1] == ["fan[acpi.4]=100%"]
    monkeypatch.setattr(session_log.psutil, "sensors_temperatures", lambda: {}, raising=False)
    monkeypatch.setattr(monitoring, "acpi_fan_metrics", lambda: {})
    assert session_log.collect_sensor_details() == ([], [])

    # iLO/BMC: alle Temperaturen und Lüfter einzeln, auch mit Dezimalkomma (deutsche Locale).
    listing = ("01-Inlet Ambient | 01h | ok | 55.1 | 21 degrees C\n02-CPU 1 | 02h | ok | 3.1 | 40 degrees C\n"
               "Fan 1 | 01h | ok | 29.1 | 15,68 percent\nFan 2 | 02h | ns | 29.2 | No Reading\n"
               "Power Meter | 0Ah | ok | 7.1 | 180 Watts\n")
    monkeypatch.setattr(ipmi, "ipmi_available", lambda: True)
    monkeypatch.setattr(ipmi, "_noninteractive_runner",
                        lambda: lambda cmd, **kw: S(returncode=0, stdout=listing, stderr=""))
    bmc = session_log._IpmiSampler()
    bmc.sample()
    assert bmc.temperatures == ["bmc[01-Inlet Ambient]=21C", "bmc[02-CPU 1]=40C"]
    assert bmc.fans == ["bmc[Fan 1]=15.68%"] and bmc.other == ["bmc[Power Meter]=180W"]
    assert bmc.text == "bmc_fan[Fan 1]=15.68% bmc_temp_max[02-CPU 1]=40C"


def test_background_gpu_sampling_selects_direct_binary_never_pkexec_wrapper(monkeypatch):
    from pathlib import Path
    paths={'/opt/gputaskman-qt6/collector/gputaskman-collector','/opt/gputaskman/gputaskman'}
    monkeypatch.setattr(Path,'is_file',lambda self:str(self) in paths)
    monkeypatch.setattr(session_log.os,'access',lambda *args:True)
    monkeypatch.setattr(session_log.shutil,'which',lambda name:pytest.fail('must not use an elevation wrapper'))
    assert session_log._gpu_sampler_binary()=='/opt/gputaskman-qt6/collector/gputaskman-collector'
    paths.remove('/opt/gputaskman-qt6/collector/gputaskman-collector')
    assert session_log._gpu_sampler_binary()=='/opt/gputaskman/gputaskman'
    paths.clear()
    assert session_log._gpu_sampler_binary() is None


def test_qt_background_sampler_once_for_all_gpus_no_auth_and_stable_id(tmp_path):
    import json
    import copy
    from unittest.mock import patch
    tool=tmp_path/'gputaskman-collector'
    tool.write_text('#!/bin/sh\nexit 0\n');tool.chmod(0o755)
    written=[]
    sampler=session_log._GpuSampler(lambda source,text,important=False:written.append(text),str(tool))
    first=copy.deepcopy(_NVIDIA)
    first.update(gpu_id='uuid:A',gpu_identity={'id':'uuid:A','index':0})
    second=copy.deepcopy(_NVIDIA)
    second.update(gpu_id='uuid:B',gpu_identity={'id':'uuid:B','index':1})
    commands=[]
    def run(command,**kwargs):
        commands.append(command)
        assert command==[str(tool),'--once']
        assert kwargs['stdin']==subprocess.DEVNULL
        return subprocess.CompletedProcess(command,0,json.dumps({'protocol':2,'snapshots':[first,second]}),'')
    with patch.object(session_log.subprocess,'run',side_effect=run):
        sampler.sample()
        first['gpu_identity']['index']=3
        first['device']['throttle']=['thermal']
        sampler.sample()
    assert len(commands)==2
    assert any(line.startswith('gpu0[NVIDIA A40]') for line in written)
    assert any(line.startswith('gpu1[NVIDIA A40]') for line in written)
    assert 'EREIGNIS: gpu3 gedrosselt: thermal' in written
    assert set(sampler._previous)=={'uuid:A','uuid:B'}
