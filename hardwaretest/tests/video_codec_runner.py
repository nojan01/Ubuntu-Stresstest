"""Lifecycle adapter for the isolated video-codec worker."""
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

from hardwaretest.core.paths import resource_path
from hardwaretest.core.test_runner import BaseTestRunner, TestExecutionError, TestResult


def vaapi_devices():
    devices = []
    vendors = {'0x8086': 'Intel', '0x1002': 'AMD'}
    for node in sorted(Path('/dev/dri').glob('renderD*')):
        device = Path('/sys/class/drm') / node.name / 'device'
        try:
            vendor = vendors.get((device / 'vendor').read_text().strip())
            pci = device.resolve(strict=True).name
        except OSError:
            continue
        if vendor:
            devices.append(SimpleNamespace(uuid=str(node), model=f'{vendor} · {pci}'))
    return devices


class VideoCodecResult(TestResult):
    @property
    def status_text(self):
        if not self.passed and not self.errors:
            return 'NICHT VOLLSTÄNDIG GEPRÜFT'
        return super().status_text


class VideoCodecRunner(BaseTestRunner):
    _use_watchdog = False  # Worker times each command and the wall-clock load phase.
    stop_grace_seconds = 3

    def __init__(self, params, gpus, codec='all', mode='both', streams=2):
        super().__init__(params)
        self.gpus = gpus
        self.codec, self.mode, self.streams = codec, mode, streams
        self.results = []
        from hardwaretest.core.gpu_comparison import VideoComparison
        self.comparison = VideoComparison()
        self.events = []
        self.summary = None

    def build_command(self):
        if not self.gpus:
            raise TestExecutionError('Bitte mindestens eine GPU auswählen.')
        if not shutil.which('ffmpeg', path='/usr/bin:/bin'):
            raise TestExecutionError('FFmpeg fehlt. Bitte das Paket ffmpeg installieren.')
        script = resource_path('hardwaretest','tests','video_codec_script.py')
        if getattr(sys,'frozen',False):
            python = shutil.which('python3',path='/usr/bin:/bin')
            if python and script.is_file():
                cmd = ['/usr/bin/env','-u','LD_LIBRARY_PATH','-u','PYTHONHOME','-u','PYTHONPATH',
                       python,'-I',str(script)]
            else:
                cmd = [sys.executable,'--video-codec-worker']
        else:
            cmd = [sys.executable,str(script)]
        return cmd + ['--gpus',','.join(gpu.uuid for gpu in self.gpus),'--codec',self.codec,
                      '--mode',self.mode,'--seconds',str(self.params.duration_seconds),
                      '--streams',str(self.streams),'--vectors',str(resource_path('assets','video-codec'))]

    def _check_line_for_errors(self, line):
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event,dict):
            return
        if event.get('event') == 'telemetry':
            self.comparison.add(event)
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        if event.get('event') == 'result':
            self.results.append(event)
            if event.get('status') == 'error':
                self._remember_error(str(event.get('detail','Videotest fehlgeschlagen')))
        if event.get('event') == 'summary':
            self.summary = event

    def _build_result(self):
        super()._build_result()
        result = self._result
        if (result is not None and result.exit_code == 2 and self.summary
                and self.summary.get('errors') == 0 and self.summary.get('incomplete',0) > 0):
            # Exit 2 means incomplete capability/environment checks, not success
            # and not a hardware failure. Keep any actual RAS/processing errors.
            result.errors[:] = [e for e in result.errors if e != 'Prozess beendet mit Exit-Code 2']
            result.passed = False
            result.warnings.append('Videotest nicht vollständig geprüft / nicht unterstützt.')
            self._result = VideoCodecResult(**vars(result))
