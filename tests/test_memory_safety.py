import os
import subprocess
import sys
import time
import pytest
from hardwaretest.tests.memory_fill_policy import memory_budget, minimum_reserve_mb
from hardwaretest.tests import memory_fill_guard as gate
from hardwaretest.tests import memory_fill_script as mfs


def test_budget_accounts_for_workers_and_fork_page_tables():
    b = memory_budget(24976, 2048, 14)
    assert 19000 < b.target_mb < 22000
    assert b.target_mb + b.scratch_mb + b.page_tables_mb + 2048 <= 24976
    assert b.workers == 14


@pytest.mark.parametrize('available,reserve,cores', [(4096,2048,80),(25000,2048,14),(1000000,16384,160)])
def test_budget_safe_on_small_and_large_servers(available,reserve,cores):
    b=memory_budget(available,reserve,cores)
    assert 1<=b.workers<=32
    assert b.target_mb>0
    assert b.target_mb+b.scratch_mb+b.page_tables_mb+reserve<=available
    assert memory_budget(available,reserve,cores,requested_mb=16).target_mb<=16


def test_low_memory_does_not_guess_or_allocate():
    assert memory_budget(1000,2048,80).target_mb==0
    assert minimum_reserve_mb(32768)==2048
    assert minimum_reserve_mb(1024*1024)==16384


def test_ram_gate_releases_after_exceptions():
    assert not gate.ram_fill_active()
    with pytest.raises(ValueError):
        with gate.ram_fill_lock():
            assert gate.ram_fill_active()
            with pytest.raises(gate.RamFillBusy):
                with gate.ram_fill_lock():pass
            raise ValueError('test')
    assert not gate.ram_fill_active()


def test_ram_gate_rejects_symlink(tmp_path,monkeypatch):
    target=tmp_path/'untouched';target.write_text('keep')
    path=tmp_path/'link';path.symlink_to(target)
    monkeypatch.setattr(gate,'lock_path',lambda:path)
    with pytest.raises(OSError):
        with gate.ram_fill_lock():pass
    assert target.read_text()=='keep'


def test_parent_watch_detects_exit_without_pid_reuse():
    p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    watch=mfs._ParentWatch(p.pid)
    try:
        assert watch.alive()
        p.terminate();p.wait(timeout=5)
        assert not watch.alive()
    finally:
        if p.poll() is None:p.kill();p.wait()
        watch.close()


def test_dead_gui_aborts_before_allocation(monkeypatch):
    monkeypatch.setattr(mfs,'_stop',False)
    monkeypatch.setattr(mfs,'_allocate_chunks',lambda *_:pytest.fail('must not allocate'))
    p=subprocess.Popen([sys.executable,'-c','pass']);p.wait()
    assert mfs.run_cycles(10,parent_pid=p.pid)==2
    assert not gate.ram_fill_active()


def test_worker_monitoring_command_includes_gui_pid():
    from hardwaretest.tests.memory_fill import MemoryFillRunner
    from hardwaretest.core.test_runner import TestParameters
    cmd=MemoryFillRunner(TestParameters(10)).build_command()
    assert cmd[cmd.index('--parent-pid')+1]==str(os.getpid())


def test_other_test_start_is_rejected_before_launch():
    from hardwaretest.core.test_runner import BaseTestRunner, TestParameters, TestExecutionError
    class NeverLaunch(BaseTestRunner):
        def build_command(self):
            pytest.fail('must not build or execute another test')
    with gate.ram_fill_lock():
        with pytest.raises(TestExecutionError,match='RAM-Fülltest'):
            NeverLaunch(TestParameters(1)).start()


def test_ui_keeps_ram_stop_and_logs_accessible():
    from types import SimpleNamespace
    from hardwaretest.ui.main_window import MainWindow
    class Panel:
        _worker=None
        def setEnabled(self,value):self.enabled=value
        def is_busy(self):return False
    ram,other,plan,system,filesystem,zfs=(Panel() for _ in range(6))
    state=SimpleNamespace(_test_panels=[ram,other,plan],_ram_fill_panel=ram,
        _test_plan_panel=plan,_ram_start_lock=True,_filesystem_active=False,
        _temperature_lock=False,system_panel=system,filesystem_panel=filesystem,zfs_panel=zfs)
    MainWindow._update_test_lock(state)
    assert ram.enabled and not other.enabled and not plan.enabled and not system.enabled
    assert not filesystem.enabled and not zfs.enabled
    state._ram_start_lock=False
    MainWindow._update_test_lock(state)
    assert ram.enabled and other.enabled and plan.enabled and system.enabled


def test_running_worker_stops_when_gui_disappears(tmp_path):
    fake_gui=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    code='''import sys
from pathlib import Path
from hardwaretest.tests import memory_fill_script as mfs, memory_fill_guard as gate
gate.lock_path=lambda:Path(sys.argv[2])
mfs._usable_cpus=lambda:2
raise SystemExit(mfs.run_cycles(30,memory_mb=8,chunk_mb=4,quick=True,pause_seconds=.2,parent_pid=int(sys.argv[1])))
'''
    log_path=tmp_path/'worker.log'
    with log_path.open('w') as log:
        worker=subprocess.Popen([sys.executable,'-c',code,str(fake_gui.pid),str(gate.lock_path())],stdout=log,stderr=subprocess.STDOUT)
        try:
            end=time.monotonic()+10
            while 'Allokiere' not in log_path.read_text() and worker.poll() is None and time.monotonic()<end:
                time.sleep(.02)
            assert 'Allokiere' in log_path.read_text()
            fake_gui.terminate();fake_gui.wait(timeout=5)
            assert worker.wait(timeout=10)==2
            text=log_path.read_text()
            assert 'GUI' in text and 'abgebrochen' in text
            assert not gate.ram_fill_active()
        finally:
            for child in (fake_gui,worker):
                if child.poll() is None:child.kill();child.wait()
