"""Start the local app when needed, wait for its database, and open it."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from hub.config import PORT, ROOT


def healthy(base_url):
    try:
        with urllib.request.urlopen(base_url+'/api/health',timeout=2) as response:
            data=json.load(response)
            return data.get('status')=='ok' and data.get('database')=='connected'
    except (OSError,ValueError,urllib.error.URLError):
        return False


def main():
    base_url=f'http://127.0.0.1:{PORT}'
    if healthy(base_url):
        webbrowser.open(base_url)
        print('Opened the running hub: '+base_url)
        return 0
    server=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT)
    try:
        for _ in range(60):
            if server.poll() is not None:
                print('The hub could not start. Read the error above.')
                return server.returncode or 1
            if healthy(base_url):
                webbrowser.open(base_url)
                print('Hub opened. Keep this window open while using it. Press Control-C to stop.')
                return server.wait()
            time.sleep(.5)
        print('The database is taking longer to respond. Check the messages above, then open '+base_url)
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        return 0


if __name__=='__main__':
    raise SystemExit(main())
