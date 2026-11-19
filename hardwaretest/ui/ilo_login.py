"""Local iLO7 login; privileged iLOrest work never blocks the GUI thread."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout
from hardwaretest.core.ilo_session import IloError
from hardwaretest.ui.i18n import language_manager


def _t(de, en):
    return en if language_manager.language == 'en' else de


class LoginWorker(QThread):
    result = Signal(object, str)

    def __init__(self, username, password, session_factory, parent=None):
        super().__init__(parent)
        self.username, self.password = username, password
        self.session_factory = session_factory

    def run(self):
        session = None
        try:
            session = self.session_factory()
            session.login(self.username, self.password)
            self.result.emit(session, '')
        except Exception as exc:
            if session is not None:
                session.close()
            message = str(exc) if isinstance(exc, IloError) else _t('Lokale iLO7-Anmeldung fehlgeschlagen. Root-Autorisierung und iLOrest prüfen.', 'Local iLO7 login failed; check root authorization and iLOrest.')
            self.result.emit(None, message)
        finally:
            self.username = self.password = ''


class IloLoginDialog(QDialog):
    def __init__(self, parent=None, session_factory=None):
        super().__init__(parent)
        self.session = None
        self._worker = None
        self._session_factory = session_factory
        self.setWindowTitle(_t('iLO 7 lokal anmelden', 'Local iLO 7 login'))
        self.resize(480, 240)
        layout = QVBoxLayout(self)
        hint = QLabel(_t('Für den lokalen iLO7-Zugriff bitte mit dem iLO-Benutzer und iLO-Passwort anmelden. '
                         'Der lokale Zugriff erfolgt über iLOrest; eine IP-Adresse ist nicht erforderlich.',
                         'For local iLO7 access, enter your iLO username and password. '
                         'Local access uses iLOrest; no IP address is required.'))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_t('iLO-Benutzer', 'iLO user'), self.username)
        form.addRow(_t('iLO-Passwort', 'iLO password'), self.password)
        layout.addLayout(form)
        note = QLabel(_t('Kein Linux-Passwort. Es wird kein Anwendungskonto angelegt. '
                         'Das Passwort wird nur für den Anmeldeversuch verwendet; die Sitzung bleibt bis zur Abmeldung aktiv.',
                         'Use the iLO password, not the Linux password. No application account is created. '
                         'The password is used only for login; the session remains active until logout.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_t('Anmelden', 'Log in'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_t('Abbrechen', 'Cancel'))
        self.buttons.accepted.connect(self._login)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _login(self):
        if self._worker is not None:
            return
        if not self.username.text().strip() or not self.password.text():
            self.status.setText(_t('Bitte iLO-Benutzer und Passwort eingeben.', 'Enter iLO username and password.'))
            return
        worker = LoginWorker(self.username.text(), self.password.text(), self._session_factory, self)
        self.password.clear()
        self._worker = worker
        for control in (self.username, self.password, self.buttons):
            control.setEnabled(False)
        self.status.setText(_t('Lokale iLO7-Anmeldung …', 'Logging into local iLO7 …'))
        worker.result.connect(self._result)
        worker.finished.connect(self._finished)
        worker.start()

    def _result(self, session, error):
        self.session = session
        self.status.setText(error)

    def _finished(self):
        worker, self._worker = self._worker, None
        worker.deleteLater()
        for control in (self.username, self.password, self.buttons):
            control.setEnabled(True)
        if self.session is not None:
            self.accept()
        else:
            self.password.setFocus()

    def reject(self):
        if self._worker is None:
            self.password.clear()
            super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            event.ignore()
        else:
            self.password.clear()
            super().closeEvent(event)
