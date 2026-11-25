"""No NVIDIA hardware assumed: reference integrity, command targeting and failure semantics."""
import hashlib
import json
from pathlib import Path
import sys

import pytest
from hardwaretest.tests import video_codec_script as video


@pytest.mark.parametrize('detail,status',[
    ('Cannot load libnvcuvid.so.1','unavailable'),
    ('Driver does not support the required nvenc API version','unavailable'),
    ('Too many concurrent sessions','unavailable'),
    ('OpenEncodeSessionEx failed: unsupported device (2)','unsupported'),
    ('Codec not supported','unsupported'),
    ('[av1_nvenc @ 0x123] No capable devices found\nError opening encoder: Invalid argument','unsupported'),
    ('[av1_cuvid @ 0x123] Codec av1_cuvid is not supported.\nError opening decoder: Invalid argument','unsupported'),
    ('[av1_nvenc @ 0x123] Cannot load libcuda.so.1\n[av1_nvenc @ 0x123] No capable devices found','unavailable'),
    ('[av1_cuvid @ 0x123] Error opening decoder: Invalid argument','error'),
    ('[av1_vaapi] No usable encoding entrypoint found for profile VAProfileAV1Profile0','unsupported'),
    ('[av1] No support for codec av1 profile 0.','unsupported'),
    ('Failed to create decode configuration: 13 (the requested VAEntrypoint is not supported).','unsupported'),
    ('Failed to create decode configuration: 12 (the requested VAProfile is not supported).','unsupported'),
    ('Hardware does not offer any usable surface formats.','unsupported'),
    ('Failed to end picture decode issue: 23 (internal decoding error).','error'),
    ('Failed to initialise VAAPI connection: -1 (unknown libva error).','unavailable'),
    ('Frame-Prüfsummen oder Bildanzahl weichen ab','error'),
    ('Unbekannter Verarbeitungsfehler','error'),
])
def test_failure_classification(detail,status):
    assert video.classify_error(detail)==status


def test_hashes_require_frames_and_compare_image_data():
    with pytest.raises(video.CommandError):video.frame_hashes('# header only')
    assert video.frame_hashes('0, 0, 0, 1, 42, abc\n')==[(42,'abc')]
    assert video.frame_hashes('0, 1, 1, 1, 42, def\n')!=[(42,'abc')]


def test_missing_ssim_is_not_a_pass():
    with pytest.raises(video.CommandError):video.quality_score('processing ended')
    assert video.quality_score('SSIM Y:0.99 All:0.978 (16.5)')==.978


def test_bundled_vectors_are_complete_and_untampered():
    root=Path(__file__).resolve().parents[1]/'assets/video-codec'
    data=json.loads((root/'manifest.json').read_text())
    assert set(data)=={'h264','hevc','av1'}
    for info in data.values():
        assert info['frames']==30
        assert hashlib.sha256((root/info['file']).read_bytes()).hexdigest()==info['sha256']


@pytest.mark.parametrize('outcome,expected', [('ok',0),('corrupt',1),('missing',2),('unsupported',2)])
def test_worker_never_falls_back_to_software_or_hides_incomplete_checks(tmp_path,monkeypatch,capsys,outcome,expected):
    clip=tmp_path/'clip.mkv';clip.write_bytes(b'synthetic fixture')
    (tmp_path/'manifest.json').write_text(json.dumps({'h264':{'file':clip.name,'frames':1,
        'sha256':hashlib.sha256(clip.read_bytes()).hexdigest()}}))
    monkeypatch.setattr(video.shutil,'which',lambda *a,**k:'/usr/bin/ffmpeg')
    monkeypatch.setattr(video,'telemetry',lambda *a:{'available':False})
    calls=[]
    def invoke(command,env,timeout=90):
        calls.append((command,dict(env)))
        assert env['CUDA_VISIBLE_DEVICES'] in ('GPU-first', 'GPU-second')
        assert 'LD_LIBRARY_PATH' not in env
        gpu=any('_nvenc' in str(x) or '_cuvid' in str(x) for x in command)
        if gpu and outcome=='missing':raise video.CommandError('Cannot load libcuda.so.1')
        if gpu and outcome=='unsupported':raise video.CommandError('Codec not supported')
        if 'ssim' in ' '.join(command):return '', 'SSIM Y:1.0 All:0.99 (20)'
        if 'framemd5' in command:
            value='bad' if gpu and outcome=='corrupt' else 'abc'
            return f'0, 0, 0, 1, 42, {value}\n',''
        return '',''
    monkeypatch.setattr(video,'invoke',invoke)
    result=video.main(['--gpus','GPU-first,GPU-second','--vectors',str(tmp_path),'--codec','h264'])
    assert result==expected
    events=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(e['gpu'], e['direction']) for e in events if e['event'] == 'result'] == [
        (gpu, direction)
        for gpu in ('GPU-first', 'GPU-second')
        for direction in ('encode', 'decode')
    ]
    for encoder in ('h264_nvenc', 'h264_cuvid'):
        assert [env['CUDA_VISIBLE_DEVICES'] for cmd, env in calls if encoder in cmd] == [
            'GPU-first', 'GPU-second'
        ]
    assert any('h264_cuvid' in cmd and cmd[cmd.index('-gpu')+1]=='0' for cmd,_ in calls)
    assert any('h264_nvenc' in cmd and cmd[cmd.index('-gpu')+1]=='0' for cmd,_ in calls)
    if outcome in ('missing','unsupported'):
        assert events[-1]['passed']==0 and events[-1]['incomplete']==4


def test_cancellation_terminates_a_running_child(monkeypatch):
    monkeypatch.setattr(video,'STOP',True)
    with pytest.raises(video.Cancelled):
        video.invoke([sys.executable,'-c','import time; time.sleep(30)'],{},timeout=2)


def test_stress_rotates_all_cases_and_cleans_children(monkeypatch):
    clock=[0.0];commands=[];terminated=[]
    monkeypatch.setattr(video,'STOP',False)
    monkeypatch.setattr(video.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(video.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    monkeypatch.setattr(video,'telemetry',lambda *a:{'available':False})
    monkeypatch.setattr(video,'emit',lambda **kw:None)
    class Process:
        def __init__(self,cmd,**kw):commands.append(cmd)
        def poll(self):return None
    monkeypatch.setattr(video.subprocess,'Popen',Process)
    monkeypatch.setattr(video,'terminate',lambda proc:terminated.append(proc))
    monkeypatch.setattr(video.os, "pread", lambda *a: f"frame={int(clock[0]*10)+1}\n".encode())
    video.stress([("codec","decode",[f'case-{i}']) for i in range(6)],31,2,{},'GPU-example')
    assert {cmd[0] for cmd in commands}=={f'case-{i}' for i in range(6)}
    assert len(terminated)==len(commands)
    monkeypatch.setattr(video.os, "pread", lambda *a: b"frame=1\n")
    with pytest.raises(video.CommandError, match="kein Bildfortschritt"):
        video.stress([("h264", "decode", ["fake"])], 10, 1, {}, "GPU-example")


@pytest.mark.parametrize('status,summary,expected',[
    ('passed',{},'passed'),('passed',None,'failed'),
    ('unsupported',{},'warning'),('unavailable',{},'warning'),('error',{},'failed'),
])
def test_ui_does_not_turn_missing_or_partial_results_into_success(monkeypatch,status,summary,expected):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from hardwaretest.ui.widgets.video_codec_panel import VideoCodecPanel
    app=QApplication.instance() or QApplication([])
    panel=VideoCodecPanel(lambda:[],lambda:True)
    reports=[]
    monkeypatch.setattr(panel.report_bar,'publish',reports.append)
    panel._runner=SimpleNamespace(results=[{'status':status}],summary=summary,
        get_result=lambda:SimpleNamespace(errors=[]),gpus=[],codec='all',mode='both',
        params=SimpleNamespace(duration_seconds=0),streams=2,events=[],comparison=SimpleNamespace(rows=lambda models: []))
    panel._finish()
    assert reports[-1].status==expected
    panel.deleteLater();app.processEvents()


def test_incomplete_exit_keeps_ras_errors_but_is_never_passed():
    from types import SimpleNamespace
    from hardwaretest.core.test_runner import TestParameters
    from hardwaretest.tests.video_codec_runner import VideoCodecRunner
    for ras_errors in ([],['Neue ECC-Ereignisse']):
        runner=VideoCodecRunner(TestParameters(0),[])
        runner._process=SimpleNamespace(returncode=2,poll=lambda:2)
        runner.summary={'errors':0,'incomplete':6}
        runner._ras_errors=ras_errors
        runner._build_result()
        result=runner.get_result()
        assert not result.passed
        assert result.errors==ras_errors
        if not ras_errors:assert result.status_text=='NICHT VOLLSTÄNDIG GEPRÜFT'


def test_panel_appends_only_unseen_events():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from hardwaretest.ui.widgets.video_codec_panel import VideoCodecPanel

    app = QApplication.instance() or QApplication([])
    panel = VideoCodecPanel(lambda: [], lambda: True)
    panel.resize(500, 300)
    panel.show()
    events = [{"frame": n} for n in range(80)]
    panel._runner = SimpleNamespace(events=events, is_running=lambda: True)
    panel._shown_events = 0
    panel._poll()
    app.processEvents()
    bar = panel.output.verticalScrollBar()
    bar.setValue(0)
    revision = panel.output.document().revision()
    panel._poll()
    assert panel.output.document().revision() == revision
    events.append({"frame": 80})
    panel._poll()
    assert panel.output.blockCount() == 81
    assert bar.value() == 0
    panel.close()
