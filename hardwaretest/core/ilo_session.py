"""Ephemeral Redfish login for iLO 7/vNIC, independent of root and iLOrest.

Passwords are sent only in the HTTPS session-creation body and never retained.
No session cache, configuration, process arguments or diagnostic dumps are used.
"""
from __future__ import annotations

import json
import ssl
import subprocess
import threading
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError


class IloError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IloSession:
    TIMEOUT = 8
    MAX_BODY = 8 * 1024 * 1024

    def __init__(self, address: str, verify: bool = True):
        address = address.strip()
        if '://' not in address:
            address = 'https://' + address
        parsed = urlsplit(address)
        try:
            port = parsed.port
        except ValueError:
            raise IloError('Ungültige iLO-Adresse / Invalid iLO address') from None
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or any(c.isspace() for c in address)):
            raise IloError('HTTPS-iLO-Adresse ohne Zugangsdaten oder Pfad erforderlich / HTTPS iLO address without credentials or path required')
        self.address = 'https://' + parsed.netloc
        self._origin = (parsed.hostname.lower(), port or 443)
        context = ssl.create_default_context() if verify else ssl._create_unverified_context()
        self._opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
        self._token = ''
        self._location = ''
        self._logs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._deadline = None

    @property
    def connected(self):
        return bool(self._token)

    def _url(self, path):
        if not isinstance(path, str) or not path or any(c.isspace() for c in path):
            raise IloError('Ungültiger Redfish-Link / Invalid Redfish link')
        url = urljoin(self.address + '/', path)
        parsed = urlsplit(url)
        try:
            origin = (parsed.hostname.lower() if parsed.hostname else '', parsed.port or 443)
        except ValueError:
            raise IloError('Ungültiger Redfish-Link / Invalid Redfish link') from None
        if (parsed.scheme != 'https' or origin != self._origin
                or parsed.username is not None or parsed.password is not None or parsed.fragment
                or not parsed.path.startswith('/redfish/v1/')):
            raise IloError('Unsicherer Redfish-Link abgewiesen / Unsafe Redfish link rejected')
        return url

    def _request(self, method, path, data=None):
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise IloError('Zeitlimit für iLO-Abfrage erreicht / iLO operation timed out')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if self._token:
            headers['X-Auth-Token'] = self._token
        payload = json.dumps(data).encode('utf-8') if data is not None else None
        req = Request(self._url(path), data=payload, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.TIMEOUT) as response:
                raw = response.read(self.MAX_BODY + 1)
                if len(raw) > self.MAX_BODY:
                    raise IloError('Redfish-Antwort zu groß / Redfish response too large')
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    raise IloError('Ungültige Redfish-Antwort / Invalid Redfish response')
                return body, response.headers
        except HTTPError as exc:
            exc.close()
            if exc.code == 401:
                self._token = ''
                raise IloError('iLO-Anmeldung fehlgeschlagen oder Sitzung abgelaufen (HTTP 401) / iLO login failed or session expired') from None
            if exc.code == 403:
                raise IloError('iLO-Benutzer hat keine Berechtigung (HTTP 403) / iLO user lacks permission') from None
            raise IloError(f'iLO HTTP {exc.code}') from None
        except (URLError, OSError) as exc:
            reason = getattr(exc, 'reason', exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise IloError('iLO-Zertifikat nicht vertrauenswürdig oder Adresse passt nicht. Zertifikatprüfung im Dialog beachten. / iLO certificate not trusted or address mismatch; check the dialog TLS setting.') from None
            raise IloError('iLO nicht erreichbar oder TLS-Verbindung fehlgeschlagen / iLO unreachable or TLS connection failed') from None
        except (ValueError, UnicodeError):
            raise IloError('Ungültige Redfish-JSON-Antwort / Invalid Redfish JSON response') from None

    def login(self, username, password):
        with self._lock:
            if self._token:
                raise IloError('Bereits angemeldet / Already logged in')
            if not username.strip() or not password:
                raise IloError('Benutzername und Passwort erforderlich / Username and password required')
            self._deadline = time.monotonic() + 60
            body, headers = self._request('POST', '/redfish/v1/SessionService/Sessions',
                                          {'UserName': username.strip(), 'Password': password})
            token = headers.get('X-Auth-Token', '')
            location = headers.get('Location') or body.get('@odata.id', '')
            if not token or not location:
                raise IloError('iLO liefert keine vollständige Redfish-Sitzung / iLO returned no complete Redfish session')
            if not urlsplit(self._url(location)).path.startswith('/redfish/v1/SessionService/Sessions/'):
                raise IloError('Ungültiger Redfish-Sitzungslink / Invalid Redfish session link')
            self._url(location)  # Validate before using the token in any later request.
            self._token, self._location = token, location
            try:
                self._discover_logs()
                if 'IML' not in self._logs:
                    raise IloError('IML-Ressource nicht gefunden / IML resource not found')
            except Exception:
                self.close()
                raise
            finally:
                self._deadline = None

    def _members(self, path):
        members, visited = [], set()
        while path:
            url = self._url(path)
            if url in visited or len(visited) >= 64:
                raise IloError('Redfish-Paginierung ungültig oder zu groß / Invalid or excessive Redfish pagination')
            visited.add(url)
            body, _ = self._request('GET', path)
            batch = body.get('Members', [])
            if not isinstance(batch, list) or len(members) + len(batch) > 10000:
                raise IloError('Redfish-Sammlung zu groß oder ungültig / Invalid or excessive Redfish collection')
            members.extend(m for m in batch if isinstance(m, dict))
            next_link = body.get('Members@odata.nextLink') or body.get('@odata.nextLink')
            path = urljoin(url, next_link) if isinstance(next_link, str) and next_link else None
        return members

    def _discover_logs(self):
        root, _ = self._request('GET', '/redfish/v1/')
        self._logs = {}
        for kind in ('Systems', 'Managers'):
            collection = root.get(kind, {}).get('@odata.id')
            if not collection:
                continue
            for member in self._members(collection)[:32]:
                resource, _ = self._request('GET', member['@odata.id'])
                logs = resource.get('LogServices', {}).get('@odata.id')
                if not logs:
                    continue
                for entry in self._members(logs)[:32]:
                    service, _ = self._request('GET', entry['@odata.id'])
                    identity = (str(service.get('Id', '')) + ' ' + str(service.get('Name', ''))).casefold()
                    if str(service.get('Id', '')).upper() == 'IML' or 'integrated management log' in identity:
                        self._logs.setdefault('IML', service)
                    elif str(service.get('Id', '')).upper() == 'IEL' or 'ilo event log' in identity:
                        self._logs.setdefault('IEL', service)

    def read_log(self, kind):
        with self._lock:
            if not self.connected:
                raise IloError('iLO nicht angemeldet / iLO not logged in')
            self._deadline = time.monotonic() + 60
            path = self._logs.get(kind, {}).get('Entries', {}).get('@odata.id')
            if not path:
                raise IloError(f'{kind}: nicht verfügbar / unavailable')
            entries = []
            for member in self._members(path):
                if 'Message' not in member and member.get('@odata.id'):
                    member, _ = self._request('GET', member['@odata.id'])
                entries.append(member)
            return {'Members': entries}

    def clear_log(self, kind):
        with self._lock:
            if not self.connected:
                raise IloError('iLO nicht angemeldet / iLO not logged in')
            self._deadline = time.monotonic() + 20
            target = self._logs.get(kind, {}).get('Actions', {}).get('#LogService.ClearLog', {}).get('target')
            if not target:
                raise IloError(f'{kind}: Löschen nicht unterstützt / clearing unsupported')
            self._request('POST', target, {})
            return 'OK'

    def runner(self, fallback):
        """Adapt only the existing four fixed log commands, never other iLOrest calls."""
        def run(command, **kwargs):
            from hardwaretest.core import ipmi
            allowed = {'IML': (ipmi.ILOREST_IML_CMD, ipmi.CLEAR_LOG_COMMANDS['IML']),
                       'IEL': (ipmi.ILOREST_IEL_CMD, ipmi.CLEAR_LOG_COMMANDS['IEL'])}
            for kind, (read, clear) in allowed.items():
                if list(command) in (read, clear):
                    try:
                        out = json.dumps(self.read_log(kind)) if list(command) == read else self.clear_log(kind)
                        return subprocess.CompletedProcess(command, 0, out, '')
                    except IloError as exc:
                        return subprocess.CompletedProcess(command, 1, '', str(exc))
            if fallback is None:
                return subprocess.CompletedProcess(command, 1, '', 'Keine Root-Rechte / No root privileges')
            return fallback(command, **kwargs)
        run.supports_ilo_logs = True
        return run

    @property
    def available_logs(self):
        return list(self._logs) if self.connected else []

    def close(self):
        with self._lock:
            self._deadline = None
            try:
                if self._token and self._location:
                    self._request('DELETE', self._location)
            except IloError:
                pass  # Session expires server-side if the server cannot be reached.
            finally:
                self._token = self._location = ''
                self._logs.clear()
