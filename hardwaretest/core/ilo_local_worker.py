"""Fixed privileged iLOrest operations; input arrives through an anonymous pipe.

The private iLOrest session cache lives only in /dev/shm and is removed with
its root broker. No caller-supplied executable, path or CLI options are accepted.
"""

WORKER_SCRIPT = r'''
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

OPERATIONS = {
    'iml_read': ['serverlogs', '--selectlog=IML', '--json'],
    'iel_read': ['serverlogs', '--selectlog=IEL', '--json'],
    'iml_clear': ['serverlogs', '--selectlog=IML', '--clearlog'],
    'iel_clear': ['serverlogs', '--selectlog=IEL', '--clearlog'],
    'drive_read': ['get', '--select=Drive.', '--json'],
    'logout': ['logout'],
}

def clear_cache(private):
    # Keep the private parent directory to prevent replacement between logins.
    for entry in os.scandir(private):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.unlink(entry.path)

def main():
    private = Path(sys.argv[1])
    cache = private / "session"
    payload = sys.stdin.buffer.read(16385)
    if len(payload) > 16384:
        return 2
    request = json.loads(base64.b64decode(payload, validate=True))
    if not isinstance(request, dict):
        return 2
    operation = request.get('operation')
    if operation == 'login':
        if set(request) != {'operation', 'username', 'password'}:
            return 2
        user, password = request['username'], request['password']
        if (not isinstance(user, str) or not isinstance(password, str)
                or not user.strip() or not password or len(user)>256 or len(password)>4096
                or any(c in user+password for c in '\r\n\0')):
            return 2
        # getpass falls back to stdin because this process has no controlling TTY.
        # Only the username is a CLI argument; the password is sent via a pipe.
        args = ['login', '--no_app_account', '--user='+user]
        stdin = password+'\n'
    elif operation in OPERATIONS and set(request) == {'operation'}:
        args = OPERATIONS[operation]
        stdin = ''
    else:
        return 2
    cache.mkdir(mode=0o700, exist_ok=True)
    logs = private / 'logs'
    logs.mkdir(mode=0o700, exist_ok=True)
    config = private / 'redfish.conf'
    config.write_text('[redfish]\n')
    try:
        result = subprocess.run(['ilorest', '--nologo', '--noinfolog', '--config='+str(config), '--logdir='+str(logs), '--cache-dir='+str(cache), *args],
                                input=stdin, capture_output=True, text=True,
                                timeout=90 if operation != 'logout' else 10,
                                start_new_session=False)
    except (OSError, subprocess.SubprocessError):
        print('Lokaler iLOrest-Aufruf fehlgeschlagen oder Zeitlimit erreicht / Local iLOrest call failed or timed out', file=sys.stderr)
        return 1
    finally:
        request.clear()
        stdin = ''
        if operation == 'login':
            password = ''
        if operation == 'logout':
            clear_cache(private)
    if operation == 'login':
        # Do not forward vendor diagnostics which might echo credentials.
        if result.returncode:
            clear_cache(private)
            print('iLO7-Anmeldung fehlgeschlagen. iLO-Benutzer/Passwort und lokalen Zugriff prüfen. / iLO7 login failed; check credentials and local access.', file=sys.stderr)
        else:
            print('OK')
    else:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result.returncode

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, TypeError, KeyError):
        print('Ungültiger iLO-Auftrag / Invalid iLO request', file=sys.stderr)
        raise SystemExit(2)
'''
