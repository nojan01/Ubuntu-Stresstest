"""NVENC/NVDEC controls embedded in the NVIDIA tab."""
from datetime import datetime
import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox,QFormLayout,QGroupBox,QHBoxLayout,QLabel,
                              QMessageBox,QPlainTextEdit,QPushButton,QSpinBox,QVBoxLayout)
from hardwaretest.core.report import Report
from hardwaretest.core.test_runner import TestParameters
from hardwaretest.tests.video_codec_runner import VideoCodecRunner, vaapi_devices
from hardwaretest.ui.widgets.report_bar import ReportBar


class VideoCodecPanel(QGroupBox):
    def __init__(self, gpu_provider, can_start, parent=None, *, vaapi=False):
        self.backend = 'VA-API' if vaapi else 'NVENC / NVDEC'
        super().__init__(f'Video-Encoder / Decoder ({self.backend})', parent)
        self.gpu_provider, self.can_start = gpu_provider, can_start
        self.device = QComboBox() if vaapi else None
        self.refresh_btn = QPushButton('GPUs suchen') if vaapi else None
        if self.device is not None:
            self.refresh_btn.clicked.connect(self._refresh_devices)
            self._refresh_devices()
            self.gpu_provider = self._selected_devices
        self._runner = None
        self.started = None
        self.mode = QComboBox()
        for title,value in [('Encoder und Decoder','both'),('Nur Encoder','encode'),('Nur Decoder','decode')]:
            self.mode.addItem(title,value)
        self.codec = QComboBox()
        for title,value in [('Alle (H.264 / HEVC / AV1)','all'),('H.264','h264'),('HEVC','hevc'),('AV1','av1')]:
            self.codec.addItem(title,value)
        self.duration = QSpinBox();self.duration.setRange(0,120);self.duration.setValue(0)
        self.duration.setSpecialValueText('Nur Funktionsprüfung');self.duration.setSuffix(' min / GPU')
        self.streams = QSpinBox();self.streams.setRange(1,4);self.streams.setValue(2)
        self.start_btn = QPushButton('Videotest starten');self.stop_btn=QPushButton('Stoppen');self.stop_btn.setEnabled(False)
        self.status = QLabel('Verwendet die oben ausgewählten GPUs. Nicht unterstützte Codecs werden separat ausgewiesen.');self.status.setWordWrap(True)
        hint=QLabel('Funktionsprüfung: 720p, 30 Bilder je Codec. Optional anschließend parallele Laststreams; GPUs nacheinander. '
                    'Ein Verarbeitungstest ersetzt keine vollständige Hardwarediagnose.');hint.setWordWrap(True)
        form=QFormLayout();form.addRow('Prüfung',self.mode);form.addRow('Codec',self.codec)
        form.addRow('Zusätzliche Lastdauer',self.duration);form.addRow('Parallele Streams / GPU',self.streams)
        if self.device is not None:
            form.insertRow(0, 'GPU', self.device)
            form.insertRow(1, '', self.refresh_btn)
            self.status.setText('Intel- und AMD-GPUs mit DRM-Rendergerät. Nicht unterstützte Codecs werden separat ausgewiesen.')
        buttons=QHBoxLayout();buttons.addWidget(self.start_btn);buttons.addWidget(self.stop_btn)
        self.output=QPlainTextEdit();self.output.setReadOnly(True);self.output.setMaximumBlockCount(1000);self.output.setMinimumHeight(110)
        self.report_bar=ReportBar()
        layout=QVBoxLayout(self);layout.addWidget(hint);layout.addLayout(form);layout.addLayout(buttons)
        layout.addWidget(self.status);layout.addWidget(self.report_bar);layout.addWidget(self.output)
        self.timer=QTimer(self);self.timer.setInterval(300);self.timer.timeout.connect(self._poll)
        self.start_btn.clicked.connect(self._start);self.stop_btn.clicked.connect(self._stop)

    def _refresh_devices(self):
        self.device.clear()
        self.device.addItem('Alle Intel-/AMD-GPUs', None)
        for gpu in vaapi_devices():
            self.device.addItem(f'{gpu.model} ({gpu.uuid})', gpu.uuid)

    def _selected_devices(self):
        selected = self.device.currentData()
        return [gpu for gpu in vaapi_devices() if selected is None or gpu.uuid == selected]

    def _start(self):
        if self._runner is not None:
            return
        if not self.can_start():
            QMessageBox.warning(self,'GPU-Test aktiv','Bitte den laufenden GPU-Test zuerst beenden.');return
        self._runner=VideoCodecRunner(TestParameters(self.duration.value()*60),self.gpu_provider(),
                                      self.codec.currentData(),self.mode.currentData(),self.streams.value())
        try:
            self._runner.start()
        except Exception as exc:
            self._runner=None;QMessageBox.warning(self,'Videotest nicht gestartet',str(exc));return
        self._shown_events = 0
        self.started=datetime.now();self.start_btn.setEnabled(False);self.stop_btn.setEnabled(True)
        for widget in (self.codec,self.mode,self.duration,self.streams):widget.setEnabled(False)
        self.status.setText('Videotest läuft …');self.output.clear();self.timer.start()

    def _poll(self):
        runner=self._runner
        if runner is None:return
        new_events = runner.events[self._shown_events:]
        if new_events:
            bar = self.output.verticalScrollBar()
            position = bar.value()
            follow = position == bar.maximum()
            self.output.appendPlainText('\n'.join(json.dumps(e, ensure_ascii=False) for e in new_events))
            bar.setValue(bar.maximum() if follow else position)
            self._shown_events += len(new_events)
        if not runner.is_running() and runner.get_result() is not None:self._finish()

    def _stop(self):
        if self._runner is not None:
            self._runner.stop(aborted=True);self._finish(cancelled=True)

    def _finish(self,cancelled=False):
        runner=self._runner
        if runner is None:return
        self.timer.stop()
        statuses=[r.get('status') for r in runner.results]
        result=runner.get_result()
        if cancelled:status='cancelled'
        elif 'error' in statuses or runner.summary is None or (result and result.errors):status='failed'
        elif any(s!='passed' for s in statuses) or not statuses:status='warning'
        else:status='passed'
        labels={'passed':'Prüfungen bestanden','warning':'Nicht vollständig geprüft / nicht unterstützt',
                'failed':'Verarbeitungsfehler – Protokoll prüfen','cancelled':'Abgebrochen'}
        self.status.setText(labels[status])
        report=Report(test='vaapi-video' if self.device is not None else 'nvidia-video',title=self.backend,status=status,summary=labels[status],
                      started=self.started,finished=datetime.now(),
                      parameters=[('GPU',f'{g.model} / {g.uuid}') for g in runner.gpus]+
                                 [('Codec',runner.codec),('Prüfung',runner.mode),('Lastsekunden / GPU',str(runner.params.duration_seconds)),('Streams',str(runner.streams))],
                      raw_output='\n'.join(json.dumps(e,ensure_ascii=False) for e in runner.events))
        report.add_section('Einzelergebnisse','table',header=['GPU','Codec','Prüfung','Status','Details'],
            rows=[(r.get('gpu',''),r.get('codec',''),r.get('direction',''),r.get('status',''),r.get('detail','')) for r in runner.results])
        report.hints=['Nicht unterstützt und fehlende Voraussetzungen sind keine bestandenen Prüfungen.',
                      'SSIM-Grenze 0.95 für diese Testsequenzen; ein Fehler beweist keinen Hardwaredefekt.',
                      'GPU-Auslastung/Temperatur im Rohprotokoll, soweit verfügbar. Kein vollständiges Xid-Kernelprotokoll.']
        self.report_bar.publish(report)
        self._runner=None;self.start_btn.setEnabled(True);self.stop_btn.setEnabled(False)
        for widget in (self.codec,self.mode,self.duration,self.streams):widget.setEnabled(True)
