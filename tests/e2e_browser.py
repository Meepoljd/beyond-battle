"""Real Chromium, ephemeral loopback server and DB; no publication or existing service."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='battle-e2e-') as tmp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'BATTLE_DB':str(Path(tmp)/'battle.sqlite3')}
        server=subprocess.Popen([sys.executable,'-m','uvicorn','server.api:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base+'/readyz',timeout=1);break
                except Exception:time.sleep(.05)
            else:raise RuntimeError('local server failed to start')
            with sync_playwright() as p:
                executable=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
                browser=p.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
                for width,height in [(390,844),(1440,1000)]:
                    context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<500,has_touch=width<500)
                    page=context.new_page();errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' and 'Content Security Policy' in msg.text else None)
                    page.goto(base);page.wait_for_selector('.roster-card')
                    assert page.locator('.roster-card').count()==3
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    screenshot_dir=Path(os.environ.get('BATTLE_SCREENSHOTS','/tmp/beyond-battle-screenshots'));screenshot_dir.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(screenshot_dir/f'prepare-{width}.png'),full_page=True)
                    page.click('#create');page.wait_for_selector('#game:not([hidden])')
                    mid=page.evaluate("localStorage.getItem('battle-match')")
                    assert page.request.get(base+f'/api/matches/{mid}/replay').status==409
                    page.select_option('#unit','blue-3');page.select_option('#action','SUPPORT');page.fill('#threshold','3')
                    page.fill('#order-text','<img src=x onerror=alert(1)> 守住桥头')
                    page.check('#direct');page.click('#send-order');page.wait_for_selector('.order-item')
                    assert page.locator('#order-history img').count()==0
                    assert '传输中' in page.locator('#order-history').inner_text()
                    # Lose the response after a successful server commit. Reload must retry same idempotency key.
                    def lose_response(route):
                        route.fetch();route.abort('connectionfailed')
                    page.route('**/advance',lose_response,times=1)
                    page.click('#advance');page.wait_for_selector('#notice button')
                    assert page.request.get(base+f'/api/matches/{mid}').json()['tick']==1
                    page.reload();expect(page.locator('#tick')).to_have_text('01')
                    assert page.evaluate("localStorage.getItem('battle-pending')") is None
                    assert page.request.get(base+f'/api/matches/{mid}').json()['tick']==1
                    # Offline user flow preserves the action and retries exactly once after reconnection.
                    context.set_offline(True);page.click('#advance');page.wait_for_selector('#notice button')
                    context.set_offline(False);page.click('#notice button')
                    expect(page.locator('#tick')).to_have_text('02')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(screenshot_dir/f'command-{width}.png'),full_page=True)
                    while page.request.get(base+f'/api/matches/{mid}').json()['status']=='active':
                        tick=page.request.get(base+f'/api/matches/{mid}').json()['tick']
                        page.click('#advance');expect(page.locator('#tick')).to_have_text(f'{tick+1:02d}')
                    assert '守桥成功' in page.locator('#outcome').inner_text()
                    page.click('#show-review');page.wait_for_selector('#review:not([hidden])')
                    assert '校验通过' in page.locator('#replay-proof').inner_text()
                    page.locator('#timeline').fill('4');page.locator('#timeline').dispatch_event('input')
                    assert 'T4' in page.locator('#review-tick').inner_text()
                    assert page.locator('input[name=memory]').count()==1
                    page.click('#save-memories');expect(page.locator('#memory-result')).to_contain_text('已经确认')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(screenshot_dir/f'review-{width}.png'),full_page=True)
                    page.click('#new-match');page.wait_for_selector('#prepare:not([hidden])')
                    assert sum(len(c['memories']) for c in page.request.get(base+'/api/commanders').json())>=1
                    page.click('#create');page.wait_for_selector('#game:not([hidden])')
                    new_mid=page.evaluate("localStorage.getItem('battle-match')")
                    assert new_mid!=mid
                    assert all(u['personnel']==15 for u in page.request.get(base+f'/api/matches/{new_mid}').json()['known_units'])
                    page.reload();page.wait_for_selector('#game:not([hidden])')
                    assert page.evaluate("localStorage.getItem('battle-match')")==new_mid
                    page.on('dialog',lambda d:d.accept())
                    page.click('#end');page.wait_for_selector('#outcome:not([hidden])')
                    assert '中止' in page.locator('#outcome').inner_text()
                    assert not errors,errors
                    print(json.dumps({'viewport':[width,height],'result':'PASS','flows':['prepare','order','lost-response retry','offline retry','whole-match win','replay','memory review','second match','refresh','abort','no horizontal overflow','no XSS or JS/CSP errors']},ensure_ascii=False))
                    context.close()
                browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()


if __name__=='__main__':main()
