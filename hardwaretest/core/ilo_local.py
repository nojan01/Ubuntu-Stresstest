"""Local iLO7 session using the exact iLOrest login verified on the server."""
import re
import subprocess
from hardwaretest.core.ilo_session import IloError


class LocalIloSession:
    address = ''  # Compatibility with the optional HTTPS session interface.

    def __init__(self, broker):
        self._broker = broker
        self.connected = False

    @property
    def available_logs(self):
        return ['IML','IEL'] if self.connected else []

    def login(self, username, password):
        result = self._broker.ilo_local('login',username,password)
        if result.returncode:
            raise IloError('iLO7-Anmeldung fehlgeschlagen. Bitte iLO-Zugangsdaten und lokalen Zugriff prüfen. / iLO7 login failed; check credentials and local access.')
        self.connected = True

    def drives(self):
        import json
        result = self._broker.ilo_local('drive_read')
        if result.returncode:
            raise IloError('iLO-Laufwerksinventar nicht verfügbar')
        def records(value):
            if isinstance(value, list):
                for item in value:
                    yield from records(item)
            elif isinstance(value, dict):
                if value.get('SerialNumber') or str(value.get('@odata.type', '')).startswith('#Drive.'):
                    yield value
                else:
                    for item in value.values():
                        yield from records(item)
        return list(records(json.loads(result.stdout)))

    def runner(self, fallback):
        def run(command, **kwargs):
            from hardwaretest.core import ipmi
            mapping = [(ipmi.ILOREST_IML_CMD,'iml_read'),(ipmi.ILOREST_IEL_CMD,'iel_read'),
                       (ipmi.CLEAR_LOG_COMMANDS['IML'],'iml_clear'),(ipmi.CLEAR_LOG_COMMANDS['IEL'],'iel_clear')]
            for expected, operation in mapping:
                if list(command) == expected:
                    if not self.connected:
                        return subprocess.CompletedProcess(command,1,'','iLO7 nicht angemeldet / not logged in')
                    result = self._broker.ilo_local(operation)
                    text = result.stderr+' '+result.stdout
                    if result.returncode and any(x in text.lower() for x in ('unauthorized','401','app account','--no_app_account','not logged')):
                        self.connected = False
                    return result
            if fallback is None:
                return subprocess.CompletedProcess(command,1,'','Keine Root-Rechte / No root privileges')
            return fallback(command,**kwargs)
        run.supports_ilo_logs = True
        return run

    def close(self):
        try:
            if self.connected:
                self._broker.ilo_local('logout')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
        finally:
            self.connected = False


def is_ilo7(data):
    """Use explicit iLO identity, HPE Gen12 DMI, or the observed iLO7 auth error."""
    if not data.get('proliant'):
        return False
    identity = data.get('identity') or {}
    bmc = data.get('bmc') or {}
    explicit = ' '.join(str(v) for v in bmc.values())
    error = data.get('iml_error','')
    return bool(re.search(r'\biLO\s*7\b', explicit, re.I)
                or re.search(r'\bGen\s*12\b', identity.get('product',''), re.I)
                or '--no_app_account' in error or 'app account' in error.lower())
