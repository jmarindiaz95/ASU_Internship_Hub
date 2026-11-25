import os
from pathlib import Path
import shutil
import subprocess

import pytest

import launch_hub


def test_launcher_opens_existing_app_without_starting_duplicate(monkeypatch):
    opened=[]
    monkeypatch.setattr(launch_hub,'healthy',lambda url:True)
    monkeypatch.setattr(launch_hub.webbrowser,'open',opened.append)
    def duplicate(*args,**kwargs):
        raise AssertionError('Already-running app must be reused')
    monkeypatch.setattr(launch_hub.subprocess,'Popen',duplicate)
    assert launch_hub.main()==0
    assert opened==[f'http://127.0.0.1:{launch_hub.PORT}']


def test_launcher_waits_for_new_app_before_opening(monkeypatch):
    checks=iter([False,True])
    opened=[]
    class Server:
        def poll(self): return None
        def wait(self): return 0
    monkeypatch.setattr(launch_hub,'healthy',lambda url:next(checks))
    monkeypatch.setattr(launch_hub.webbrowser,'open',opened.append)
    monkeypatch.setattr(launch_hub.subprocess,'Popen',lambda *args,**kwargs:Server())
    assert launch_hub.main()==0
    assert len(opened)==1


def test_live_server_entry_handles_running_and_stopped_app():
    node=os.environ.get('NODE_BINARY') or shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for offline JavaScript checks.')
    root=Path(__file__).resolve().parent.parent
    html=(root/'index.html').read_text()
    assert '{{' not in html and '{%' not in html
    assert './static/js/open-hub.js' in html and 'Start Hub.command' in html
    result=subprocess.run([node,str(root/'tests/launcher_entry.cjs'),str(root/'static/js/open-hub.js')],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stdout+result.stderr
