import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest
from hardwaretest.core.ilo_local import LocalIloSession, is_ilo7
from hardwaretest.core.root_broker import RootBroker
from hardwaretest.core import ipmi


@pytest.mark.parametrize('data,expected', [
    ({'proliant':True,'identity':{'product':'Synergy 480 Gen12'}},True),
    ({'proliant':True,'identity':{'product':'ProLiant DL380 Gen12'}},True),
    ({'proliant':True,'bmc':{'Product Name':'iLO 7'}},True),
    ({'proliant':True,'iml_error':'The app account is not available; --no_app_account'},True),
    ({'proliant':True,'identity':{'product':'ProLiant DL380 Gen11'}},False),
    ({'proliant':True,'identity':{'product':'ProLiant DL380 Gen10'}},False),
    ({'proliant':False,'identity':{'product':'Intel Gen12'}},False),
])
def test_detect_ilo7_without_prompting_other_generations(data,expected):
    assert is_ilo7(data) is expected


def test_broker_local_login_read_clear_logout_and_secret_transport(tmp_path,monkeypatch):
    cli=tmp_path/'ilorest'
    cli.write_text(textwrap.dedent('''\
        #!/usr/bin/python3
        import getpass,json,os,sys,shutil
        from pathlib import Path
        args=sys.argv[1:]
        cache=Path(next(a.split('=',1)[1] for a in args if a.startswith('--cache-dir=')))
        assert str(cache).startswith('/dev/shm/hardwaretest-ilo.')
        assert cache.stat().st_mode & 0o777 == 0o700
        with open(os.environ['ILO_TEST_ARGS'],'a') as f:f.write(json.dumps(args)+'\\n')
        if 'login' in args:
            assert '--no_app_account' in args and '--user=reader' in args
            password=getpass.getpass()
            if password != 'private ! pass':
                print('password='+password,file=sys.stderr);sys.exit(1)
            (cache/'session').write_text('private-token')
            print('OK')
        elif 'logout' in args:
            shutil.rmtree(cache)
            print('OK')
        elif 'serverlogs' in args:
            if not (cache/'session').exists():
                print('HTTP 401',file=sys.stderr);sys.exit(1)
            if '--json' in args: print(json.dumps({'Members':[{'Id':'1','Message':'healthy','Severity':'OK'}]}))
            else: print('Cleared')
        else:sys.exit(2)
    '''))
    cli.chmod(0o755)
    args_file=tmp_path/'args.jsonl'
    monkeypatch.setenv('PATH',str(tmp_path)+':'+os.environ['PATH'])
    monkeypatch.setenv('ILO_TEST_ARGS',str(args_file))
    broker=RootBroker(prefix=[])
    cache=None
    try:
        session=LocalIloSession(broker)
        with pytest.raises(RuntimeError):session.login('reader','wrong-secret')
        # Vendor errors must never include the password in returned diagnostics.
        result=broker.ilo_local('login','reader','wrong-secret')
        assert 'wrong-secret' not in result.stdout+result.stderr
        session.login('reader','private ! pass')
        assert session.connected
        entries,error=ipmi.read_iml(session.runner(None))
        assert not error and len(entries)==1
        assert ipmi.clear_log(session.runner(None),'IML')[0]
        lines=[json.loads(line) for line in args_file.read_text().splitlines()]
        cache=Path(next(a.split('=',1)[1] for a in lines[-1] if a.startswith('--cache-dir='))).parent
        session.close()
        assert not session.connected and not list(cache.iterdir())
        assert 'private ! pass' not in args_file.read_text() and 'wrong-secret' not in args_file.read_text()
        assert broker.ilo_local('iml_read').returncode==1
        session.login('reader','private ! pass')
        assert session.connected
        session.close()
        for operation in ('factorydefaults','login --url=evil','run'):
            with pytest.raises(ValueError):broker.ilo_local(operation)
    finally:
        broker.close()
    assert cache is not None and not cache.exists()


def test_local_session_marks_expired_login_and_keeps_other_commands():
    class Broker:
        def ilo_local(self,op,*args):
            if op=='login':return subprocess.CompletedProcess([],0,'OK','')
            return subprocess.CompletedProcess([],1,'','HTTP 401 unauthorized')
    session=LocalIloSession(Broker());session.login('reader','private')
    runner=session.runner(lambda cmd,**kw:subprocess.CompletedProcess(cmd,0,'local',''))
    assert runner(['dmidecode','-t','system']).stdout=='local'
    assert runner(ipmi.ILOREST_IML_CMD).returncode==1
    assert not session.connected
