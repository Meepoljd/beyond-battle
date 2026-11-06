"""Browser regression for asynchronous OpenCode turns; local protocol fixture only."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_opencode import FakeOpenCode

ROOT = Path(__file__).resolve().parents[1]


def main():
    remote = FakeOpenCode()
    remote.mode = 'blocked'
    try:
        with tempfile.TemporaryDirectory(prefix='battle-model-ui-') as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, BATTLE_DB=str(Path(tmp)/'game.sqlite3'),
                       BATTLE_OPENCODE_URL=remote.url, BATTLE_OPENCODE_MODEL='test/model',
                       BATTLE_OPENCODE_MAX_CALLS='3', BATTLE_OPENCODE_TIMEOUT='10')
            with open(Path(tmp)/'server.log', 'w+') as log:
                process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.api:app',
                    '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
                try:
                    base = f'http://127.0.0.1:{port}'
                    for _ in range(100):
                        try:
                            urllib.request.urlopen(base+'/readyz', timeout=1).close()
                            break
                        except OSError:
                            time.sleep(.05)
                    else:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    with sync_playwright() as p:
                        browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
                        page = browser.new_page(viewport={'width': 390, 'height': 844})
                        errors = []
                        page.on('pageerror', lambda e: errors.append(str(e)))
                        page.goto(base)
                        page.wait_for_selector('.roster-card')
                        page.select_option('#provider-mode', 'opencode')
                        expect(page.locator('#provider-help')).to_contain_text('3 次')
                        page.click('#create')
                        page.wait_for_selector('#game:not([hidden])')
                        expect(page.locator('#mode-label')).to_contain_text('OpenCode')
                        page.click('#advance')
                        expect(page.locator('#notice')).to_contain_text('正在决策')
                        page.reload()
                        expect(page.locator('#notice')).to_contain_text('正在决策')
                        remote.release.set()
                        expect(page.locator('#tick')).to_have_text('01', timeout=15000)
                        assert len(remote.calls) == 3
                        assert page.evaluate("localStorage.getItem('battle-pending')") is None
                        page.click('#advance')
                        expect(page.locator('#tick')).to_have_text('02')
                        expect(page.locator('#provider-status')).to_contain_text('规则回退')
                        assert len(remote.calls) == 3
                        page.on('dialog', lambda d: d.accept())
                        page.click('#end')
                        page.wait_for_selector('#outcome:not([hidden])')
                        page.click('#show-review')
                        expect(page.locator('#replay-proof')).to_contain_text('校验通过')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert not errors, errors
                        browser.close()
                        print('OpenCode browser: PASS (selection, async progress, reload/idempotency, budget fallback, replay, mobile layout)')
                finally:
                    remote.release.set()
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        remote.close()


if __name__ == '__main__':
    main()
