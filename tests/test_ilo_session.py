import io
import json
import ssl
import subprocess
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

import pytest
from hardwaretest.core.ilo_session import IloError, IloSession, _NoRedirect
from hardwaretest.core import ipmi


class Response:
    def __init__(self, body=None, headers=None):
        self.raw = json.dumps(body or {}).encode()
        self.headers = headers or {}
    def read(self, limit): return self.raw[:limit]
    def __enter__(self): return self
    def __exit__(self, *args): pass


class Transport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.location = '/redfish/v1/SessionService/Sessions/99'
        self.next_link = '/redfish/v1/Systems/42/Logs/IML/Entries?$skip=1'
    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        query = urlsplit(request.full_url).query
        method = request.get_method()
        self.calls.append(request)
        if self.error:
            raise self.error
        if method == 'POST' and path.endswith('/Sessions'):
            return Response(headers={'X-Auth-Token':'session-secret', 'Location':self.location})
        if method == 'DELETE' or method == 'POST': return Response()
        data = {
            '/redfish/v1/': {'Systems': {'@odata.id':'/redfish/v1/Systems'},'Managers': {'@odata.id':'/redfish/v1/Managers'}},
            '/redfish/v1/Systems': {'Members':[{'@odata.id':'/redfish/v1/Systems/42'}]},
            '/redfish/v1/Managers': {'Members':[{'@odata.id':'/redfish/v1/Managers/7'}]},
            '/redfish/v1/Systems/42': {'LogServices':{'@odata.id':'/redfish/v1/Systems/42/Logs'}},
            '/redfish/v1/Managers/7': {'LogServices':{'@odata.id':'/redfish/v1/Managers/7/Logs'}},
            '/redfish/v1/Systems/42/Logs': {'Members':[{'@odata.id':'/redfish/v1/Systems/42/Logs/IML'}]},
            '/redfish/v1/Managers/7/Logs': {'Members':[{'@odata.id':'/redfish/v1/Managers/7/Logs/IEL'}]},
        }
        for kind, base in [('IML','/redfish/v1/Systems/42/Logs/IML'), ('IEL','/redfish/v1/Managers/7/Logs/IEL')]:
            data[base] = {'Id':kind,'Entries':{'@odata.id':base+'/Entries'},'Actions':{'#LogService.ClearLog':{'target':base+'/Clear'}}}
            data[base+'/Entries'] = {'Members':[{'Id':'1','Message':'test entry','Severity':'OK'}]}
        if path.endswith('/IML/Entries'):
            if query:
                return Response({'Members':[{'Id':'2','Message':'second entry','Severity':'Warning'}]})
            return Response({'Members':[{'Id':'1','Message':'test entry','Severity':'OK'}],'Members@odata.nextLink':self.next_link})
        return Response(data[path])


@pytest.fixture
def session():
    s = IloSession('https://ilo.example')
    transport = Transport()
    s._opener = transport
    return s, transport


def test_login_discovery_paginated_read_and_logout(session):
    s, transport = session
    s.login('reader', 'password-secret')
    assert s.connected and sorted(s.available_logs) == ['IEL','IML']
    login = transport.calls[0]
    assert login.full_url == 'https://ilo.example/redfish/v1/SessionService/Sessions'
    assert json.loads(login.data) == {'UserName':'reader','Password':'password-secret'}
    assert not hasattr(s,'password') and 'password-secret' not in repr(s.__dict__)
    entries = s.read_log('IML')['Members']
    assert [e['Id'] for e in entries] == ['1','2']
    assert all(r.get_header('X-auth-token') == 'session-secret' for r in transport.calls[1:])
    s.close()
    assert transport.calls[-1].get_method() == 'DELETE'
    assert not s.connected and s.available_logs == [] and s._location == ''


@pytest.mark.parametrize('address', ['http://ilo','https://u:p@ilo','https://ilo/a','https://ilo?x=1','https://ilo#x','https://ilo:invalid','https://bad host'])
def test_bad_addresses_rejected(address):
    with pytest.raises(IloError): IloSession(address)


@pytest.mark.parametrize('path', ['https://other/redfish/v1/Systems','http://ilo.example/redfish/v1/Systems','https://u:p@ilo.example/redfish/v1/Systems','/elsewhere','https://ilo.example:444/redfish/v1/Systems','//other/redfish/v1/Systems'])
def test_no_credentials_to_foreign_origin(session, path):
    s, transport = session
    s._token = 'secret'
    with pytest.raises(IloError): s._request('GET', path)
    assert not transport.calls


def test_session_location_must_be_session_resource(session):
    s, transport = session
    transport.location = '/redfish/v1/Systems/42/Logs/IML/Clear'
    with pytest.raises(IloError): s.login('reader','password-secret')
    assert not s.connected and len(transport.calls) == 1


def test_expired_session_requires_login_and_no_password_in_error(session):
    s, transport = session
    s.login('reader','password-secret')
    transport.error = HTTPError('https://ilo.example',401,'password-secret',{},io.BytesIO(b'password-secret'))
    with pytest.raises(IloError) as exc: s.read_log('IML')
    assert '401' in str(exc.value) and 'password-secret' not in str(exc.value)
    assert not s.connected


def test_logout_failure_discards_local_token(session):
    s, transport = session
    s.login('reader','password-secret')
    transport.error = URLError('failure secret')
    s.close()
    assert not s.connected and not s._location and not s._logs


def test_tls_error_is_actionable_and_does_not_leak_body(session):
    s, transport = session
    transport.error = URLError(ssl.SSLCertVerificationError('password-secret'))
    with pytest.raises(IloError) as exc: s.login('reader','password-secret')
    assert 'Zertifikat' in str(exc.value) and 'password-secret' not in str(exc.value)


def test_no_redirects_followed():
    assert _NoRedirect().redirect_request(None,None,302,'',{},'https://evil') is None


def test_pagination_cannot_loop_or_escape_origin(session):
    s, transport = session
    s.login('reader','password-secret')
    for link in ['/redfish/v1/Systems/42/Logs/IML/Entries','https://evil/redfish/v1/Entries']:
        transport.next_link = link
        with pytest.raises(IloError): s.read_log('IML')
        assert all('evil' not in r.full_url for r in transport.calls)


def test_adapter_uses_redfish_without_ilorest_and_preserves_other_commands(session, monkeypatch):
    s, transport = session
    s.login('reader','password-secret')
    seen = []
    def fallback(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, 'root data', '')
    runner = s.runner(fallback)
    monkeypatch.setattr(ipmi.shutil,'which',lambda *_:None)
    entries, error = ipmi.read_iml(runner)
    assert not error and len(entries) == 2
    runner(['dmidecode','-t','system'])
    assert seen == [['dmidecode','-t','system']]
    assert ipmi.clear_log(runner,'IML')[0]
    assert transport.calls[-1].get_method() == 'POST' and transport.calls[-1].full_url.endswith('/IML/Clear')
    assert all('Password' not in (r.data or b'').decode() for r in transport.calls[1:])


def test_iel_backup_failure_prevents_clear(session, monkeypatch, tmp_path):
    from hardwaretest.ui.widgets import proliant_panel
    s, transport = session
    s.login('reader','password-secret')
    monkeypatch.setattr(proliant_panel.hwreport,'report_path',lambda *a,**kw:tmp_path/'missing'/'backup.json')
    result = proliant_panel.clear_logs(s.runner(None),['IEL'])
    assert not result[0][1]
    assert not any(r.full_url.endswith('/IEL/Clear') for r in transport.calls)


def test_missing_logs_are_not_a_zero_error_result(monkeypatch):
    from hardwaretest.ui.widgets.proliant_panel import ProliantPanel
    data = {'time':datetime.now(),'bmc_error':'','sensors':[], 'events':None, 'iml':None,'ecc':('ok','ECC OK','')}
    state, text = ProliantPanel._summary(None,data)
    assert state == 'info' and 'IML: nicht geprüft' in text
    assert 'Ereignisprotokoll: nicht geprüft' in text
    assert '0 Fehler' not in text


def test_https_session_lifecycle_with_self_signed_server(tmp_path):
    """Exercise actual urllib/TLS POST, authenticated GET and session DELETE."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    cert, key = tmp_path/'cert.pem', tmp_path/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
                    '-out',str(cert),'-days','1','-subj','/CN=localhost'],check=True,capture_output=True)
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(('POST',self.path))
            assert data == {'UserName':'reader','Password':'private-password'}
            self.send_response(201)
            self.send_header('X-Auth-Token','private-token')
            self.send_header('Location','/redfish/v1/SessionService/Sessions/1')
            self.end_headers()
            self.wfile.write(b'{}')
        def do_GET(self):
            assert self.headers.get('X-Auth-Token') == 'private-token'
            calls.append(('GET',self.path))
            # Minimal server with an arbitrary system ID and one IML resource.
            data={
              '/redfish/v1/':{'Systems':{'@odata.id':'/redfish/v1/Systems'}},
              '/redfish/v1/Systems':{'Members':[{'@odata.id':'/redfish/v1/Systems/42'}]},
              '/redfish/v1/Systems/42':{'LogServices':{'@odata.id':'/redfish/v1/Systems/42/LogServices'}},
              '/redfish/v1/Systems/42/LogServices':{'Members':[{'@odata.id':'/redfish/v1/Systems/42/LogServices/IML'}]},
              '/redfish/v1/Systems/42/LogServices/IML':{'Id':'IML','Entries':{'@odata.id':'/redfish/v1/Systems/42/LogServices/IML/Entries'}},
              '/redfish/v1/Systems/42/LogServices/IML/Entries':{'Members':[{'Id':'9','Severity':'Critical','Message':'ECC error'}]},
            }
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(data[self.path]).encode())
        def do_DELETE(self):
            assert self.headers.get('X-Auth-Token') == 'private-token'
            calls.append(('DELETE',self.path)); self.send_response(204); self.end_headers()
    server=HTTPServer(('127.0.0.1',0),Handler)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert,key)
    server.socket=ctx.wrap_socket(server.socket,server_side=True)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    address=f'https://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(IloError,match='Zertifikat'):
            IloSession(address).login('reader','private-password')
        session=IloSession(address,verify=False)
        session.login('reader','private-password')
        entries,error=ipmi.read_iml(session.runner(None))
        assert not error and entries[0].severity=='error'
        session.close()
        assert calls[-1] == ('DELETE','/redfish/v1/SessionService/Sessions/1')
        assert not session.connected
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5)
