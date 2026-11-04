"""Verify a temporary game server, then stop it; no real save data is touched."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='battle-startup-') as scratch:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = {**os.environ, 'BATTLE_DB': str(Path(scratch) / 'smoke.sqlite3')}
        with open(Path(scratch) / 'server.log', 'w+') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'server.api:app', '--host',
                 '127.0.0.1', '--port', str(port), '--workers', '1'],
                cwd=ROOT, env=env, stdout=log, stderr=log,
            )
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Temporary game server exited during startup')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/readyz', timeout=1) as response:
                            ready = json.load(response)
                        break
                    except (URLError, TimeoutError):
                        time.sleep(.05)
                else:
                    raise RuntimeError('Temporary game server did not become ready')
                assert ready == {'status': 'ready', 'schema_version': 1}, ready
                with urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
                    assert json.load(response)['status'] == 'ok'
                with urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
                    assert '战报之外' in response.read().decode('utf-8')
                print('Startup smoke: PASS (healthz, readyz, game page; isolated DB and loopback port)')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
