"""ZFS-Scrub als Teiltest des Gesamttests (mit Fakes, ohne echten Pool)."""
from types import SimpleNamespace

from hardwaretest.core import test_plan, zfs

POOL = zfs.ZfsPool("tank", "123", "ONLINE", 10 * 1024**3, 2 * 1024**3, 8 * 1024**3)


def status(result, progress=None, errors=False, state="ONLINE"):
    return zfs.ZfsStatus(state, f"scan {result}", result, progress, errors, f"raw {result}")


def executor(tmp_path, **overrides):
    config = dict(output_dir=tmp_path, cpu_enabled=False, ram_enabled=False, nvme_enabled=False,
                  nvidia_enabled=False, zfs_enabled=True)
    config.update(overrides)
    plan = test_plan.TestPlanExecutor(test_plan.TestPlanConfig(**config))
    plan._zfs_poll_seconds = 0
    return plan


def fake_zfs(monkeypatch, plan, statuses, pools=(POOL,), readonly="off", scrub_rc=0):
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/sbin/zpool")
    monkeypatch.setattr(zfs, "list_pools", lambda: list(pools))
    monkeypatch.setattr(zfs, "run_zpool", lambda args: readonly + "\n")
    queue = iter(statuses)
    monkeypatch.setattr(zfs, "read_status", lambda pool: next(queue))
    commands = []
    def run(command, **_kw):
        commands.append(command)
        return SimpleNamespace(returncode=scrub_rc, stdout="", stderr="permission denied")
    plan._nvme_run = run
    return commands


def test_zfs_step_is_selected_and_requires_root(tmp_path):
    plan = executor(tmp_path)
    assert plan.selected_step_keys() == ["zfs_scrub"]
    import inspect
    assert '"zfs_scrub" in keys and shutil.which("zpool")' in inspect.getsource(test_plan)


def test_zfs_scrub_passes_and_reports_progress(tmp_path, monkeypatch):
    plan = executor(tmp_path, language="en")
    percents = []
    plan._step_progress = percents.append
    commands = fake_zfs(monkeypatch, plan, [status("none"), status("running", 40.0), status("passed")])
    result = plan._run_zfs_scrub()
    assert result.status == "passed"
    assert commands == [["zpool", "scrub", "tank"]]
    assert percents == [40, 99]
    assert result.summary == "1/1 pools scrubbed"


def test_zfs_scrub_fails_on_errors(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    fake_zfs(monkeypatch, plan, [status("none"), status("failed", errors=True, state="DEGRADED")])
    result = plan._run_zfs_scrub()
    assert result.status == "failed"
    assert any("ZFS meldet Fehler" in line for line in result.details)
    assert any("raw failed" in line for line in result.details)


def test_zfs_scrub_skips_readonly_and_resilvering_pools(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    other = zfs.ZfsPool("backup", "456", "ONLINE", 1, 0, 1)
    commands = fake_zfs(monkeypatch, plan, [status("resilver"), status("none")], pools=(POOL, other), readonly="on")
    result = plan._run_zfs_scrub()
    assert result.status == "skipped" and commands == []
    assert any("Resilver" in line for line in result.details)
    assert any("schreibgeschützt" in line for line in result.details)


def test_zfs_scrub_is_stopped_on_cancel(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    commands = fake_zfs(monkeypatch, plan, [status("none"), status("running", 10.0)])
    original_wait = plan._cancel.wait
    def wait(timeout=None):
        plan._cancel.set()
        return original_wait(0)
    plan._cancel.wait = wait
    result = plan._run_zfs_scrub()
    assert result.status == "cancelled"
    assert commands == [["zpool", "scrub", "tank"], ["zpool", "scrub", "-s", "tank"]]


def test_zfs_scrub_start_failure_is_reported(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    fake_zfs(monkeypatch, plan, [status("none")], scrub_rc=1)
    result = plan._run_zfs_scrub()
    assert result.status == "failed" and any("permission denied" in line for line in result.details)


def test_zfs_step_skipped_without_tools_or_pools(tmp_path, monkeypatch):
    plan = executor(tmp_path)
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: None)
    assert plan._run_zfs_scrub().status == "skipped"
    monkeypatch.setattr(test_plan.shutil, "which", lambda _: "/sbin/zpool")
    monkeypatch.setattr(zfs, "list_pools", lambda: [])
    assert plan._run_zfs_scrub().status == "skipped"
