"""Exercise the real HTTP adapter against a local, deterministic OpenCode protocol fixture."""
import copy
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient
from server.agents import RuleProvider
from server.api import create_app
from server.domain import initial, observation
from server.opencode import DENY_ALL, OpenCodeProvider, OpenCodeSettings
from server.storage import Store


class FakeOpenCode:
    def __init__(self):
        self.requests, self.calls = [], []
        self.mode = 'valid'
        self.entered = threading.Event()
        self.release = threading.Event()
        self.permissions = copy.deepcopy(DENY_ALL)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                size = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(size)) if size else None
                fixture.requests.append((self.command, self.path, body))
                status = 200
                if self.path == '/agent':
                    result = [{'name': 'battle-unit', 'steps': 1, 'permission': fixture.permissions}]
                elif self.path == '/session':
                    result = {'id': f'ses_{len(fixture.requests)}', 'permission': body['permission']}
                elif self.path.endswith('/message'):
                    fixture.calls.append(body)
                    fixture.entered.set()
                    if fixture.mode in ('blocked', 'timeout'):
                        fixture.release.wait(3)
                    o = json.loads(body['parts'][0]['text'])['observation']
                    proposal = RuleProvider().decide(o)
                    if fixture.mode == 'invalid':
                        proposal['target'] = 'unknown-zone'
                    if fixture.mode == 'constraint':
                        proposal.update(action='REST', target=o['unit']['zone'])
                    if fixture.mode == 'error':
                        status, result = 401, {'secret': 'do-not-persist-upstream-secrets'}
                    else:
                        result = {'info': {'structured': proposal, 'cost': .001,
                                           'tokens': {'input': 60, 'output': 30}}}
                else:
                    result = True
                raw = json.dumps(result).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = do_DELETE = handle_request

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = FakeOpenCode()
        self.settings = OpenCodeSettings(self.remote.url, 'test/model', 4, 1)
        self.app = create_app(Path(self.tmp.name)/'game.sqlite3', self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.counter = 0
        self.mid = self.post('/api/matches', {'provider_mode': 'opencode'}).json()['id']

    def tearDown(self):
        self.remote.release.set()
        self.client.__exit__(None, None, None)
        self.remote.close()
        self.tmp.cleanup()

    def post(self, path, body, key=None):
        self.counter += 1
        return self.client.post(path, json=body, headers={'Idempotency-Key': key or f'model-test-{self.counter}'})

    def view(self):
        return self.client.get('/api/matches/'+self.mid).json()

    def finish_job(self, jid):
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            job = self.client.get('/api/jobs/'+jid).json()
            if job['state'] not in ('pending', 'running'):
                self.assertEqual(job['state'], 'completed', job)
                return
            time.sleep(.01)
        self.fail('job did not finish')

    def turn(self, key=None):
        r = self.post(f'/api/matches/{self.mid}/advance', {'match_version': self.view()['version']}, key)
        self.assertEqual(r.status_code, 202, r.text)
        self.finish_job(r.json()['job_id'])
        return r.json()

    def test_observation_isolation_permissions_and_cleanup(self):
        self.turn()
        self.assertEqual(len(self.remote.calls), 3)
        seen = []
        for body in self.remote.calls:
            self.assertEqual(body['agent'], 'battle-unit')
            self.assertEqual(body['format']['retryCount'], 0)
            self.assertEqual(body['model'], {'providerID': 'test', 'modelID': 'model'})
            message = json.loads(body['parts'][0]['text'])
            o = message['observation']
            seen.append(o['unit']['id'])
            self.assertEqual(set(message), {'persona', 'observation'})
            self.assertNotIn('seed', o)
            self.assertNotIn('units', o)
            self.assertNotIn('red-1', json.dumps(o))
        self.assertEqual(sorted(seen), ['blue-1', 'blue-2', 'blue-3'])
        creates = [body for method, path, body in self.remote.requests if path == '/session']
        self.assertEqual(len(creates), 3)
        self.assertTrue(all(c['permission'] == DENY_ALL for c in creates))
        self.assertEqual(sum(m == 'DELETE' for m, _, _ in self.remote.requests), 3)
        state, _ = self.app.state.store.get(self.mid)
        self.assertEqual({d['provider'] for d in state['last_decisions']}, {'rules-v1', 'opencode-v1'})
        self.assertEqual(self.view()['provider_fallbacks'], 0)

    def test_persistent_budget_and_idempotency(self):
        first = self.turn('same-turn-key')
        replay = self.post(f'/api/matches/{self.mid}/advance', {'match_version': 0}, 'same-turn-key')
        self.assertEqual(replay.json(), first)
        self.assertEqual(len(self.remote.calls), 3)
        self.turn()
        self.assertEqual(len(self.remote.calls), 4)
        self.assertEqual(self.view()['provider_fallbacks'], 2)
        reopened = Store(self.app.state.store.path, self.settings)
        self.assertEqual(reopened.provider_usage(self.mid), 4)
        reopened.advance(self.mid, {'match_version': self.view()['version']}, 'after-restart')
        self.assertEqual(len(self.remote.calls), 4)
        self.assertEqual(self.view()['provider_fallbacks'], 3)

    def test_async_job_reads_and_writes_remain_available(self):
        self.remote.mode = 'blocked'
        r = self.post(f'/api/matches/{self.mid}/advance', {'match_version': 0}, 'pending-turn')
        self.assertEqual(r.status_code, 202)
        self.assertTrue(self.remote.entered.wait(2))
        self.assertEqual(self.view()['tick'], 0)
        self.assertEqual(self.client.get('/api/jobs/'+r.json()['job_id']).json()['state'], 'running')
        self.assertEqual(self.post('/api/matches', {'provider_mode': 'rules'}).status_code, 201)
        again = self.post(f'/api/matches/{self.mid}/advance', {'match_version': 0}, 'pending-turn')
        self.assertEqual(again.json(), r.json())
        conflicting = self.post(f'/api/matches/{self.mid}/end', {'match_version': 0})
        self.assertEqual(conflicting.json()['error'], 'TURN_IN_PROGRESS')
        self.remote.release.set()
        self.finish_job(r.json()['job_id'])
        self.assertEqual(len(self.remote.calls), 3)

    def test_invalid_output_falls_back_and_replay_never_calls_remote(self):
        self.remote.mode = 'invalid'
        self.turn()
        self.assertEqual(self.view()['provider_fallbacks'], 3)
        self.post(f'/api/matches/{self.mid}/end', {'match_version': self.view()['version']})
        before = len(self.remote.calls)
        replay = self.client.get(f'/api/matches/{self.mid}/replay')
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()['verified'])
        self.assertEqual(len(self.remote.calls), before)

    def test_auth_failure_redaction_and_no_automatic_retries(self):
        self.remote.mode = 'error'
        self.turn()
        self.assertEqual(len(self.remote.calls), 3)
        state, _ = self.app.state.store.get(self.mid)
        self.assertNotIn('do-not-persist', json.dumps(state))
        self.assertEqual({d['fallback_reason'] for d in state['last_decisions'] if d['provider'] == 'opencode-v1'}, {'OPENCODE_AUTH_FAILED'})

    def test_timeout_aborts_and_falls_back(self):
        self.remote.mode = 'timeout'
        self.turn()
        state, _ = self.app.state.store.get(self.mid)
        self.assertTrue(any(d['fallback_reason'] == 'OPENCODE_TIMEOUT' for d in state['last_decisions']))
        self.assertTrue(any(path.endswith('/abort') for _, path, _ in self.remote.requests))
        self.assertEqual(self.view()['tick'], 1)

    def test_tool_capable_agent_is_rejected_before_model_call(self):
        self.remote.permissions.append({'permission': 'bash', 'pattern': '*', 'action': 'allow'})
        self.turn()
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.view()['provider_fallbacks'], 3)

    def test_opencode_default_rules_are_overridden_by_deny_all(self):
        self.remote.permissions.insert(0, {'permission': '*', 'pattern': '*', 'action': 'allow'})
        self.remote.permissions.append({'permission': 'external_directory', 'pattern': '/tmp/tool-output/*', 'action': 'allow'})
        self.turn()
        self.assertEqual(len(self.remote.calls), 3)
        self.assertEqual(self.view()['provider_fallbacks'], 0)

    def test_interrupted_reservations_are_not_refunded(self):
        store = self.app.state.store
        job, _ = store.start_turn(self.mid, {'match_version': 0}, 'interrupted-key')
        store.recover()
        self.assertEqual(store.job(job['job_id'])['state'], 'interrupted')
        self.turn()
        self.assertEqual(len(self.remote.calls), 1)
        self.assertEqual(store.provider_usage(self.mid), 4)

    def test_disabled_configuration_does_not_create_model_match(self):
        with TestClient(create_app(Path(self.tmp.name)/'disabled.sqlite3', OpenCodeSettings())) as client:
            r = client.post('/api/matches', json={'provider_mode': 'opencode'}, headers={'Idempotency-Key': 'disabled-model'})
            self.assertEqual(r.status_code, 503)
            self.assertFalse(client.get('/api/providers').json()['opencode']['available'])
        self.assertEqual(self.remote.calls, [])

    def test_no_autonomy_order_constraint(self):
        o = observation(initial(42, ['lin'], []), 'blue-1')
        o['orders'] = [dict(id='o1', action='HOLD', target='bridge', retreat_threshold=7,
                            autonomy=False, report_before_withdraw=True)]
        self.remote.mode = 'constraint'
        provider = OpenCodeProvider(self.settings, self.settings.profile(), ['blue-1'], {})
        with self.assertRaisesRegex(Exception, 'OPENCODE_ORDER_CONSTRAINT'):
            provider.decide(o)

    def test_credentials_never_enter_profile_or_public_config(self):
        settings = OpenCodeSettings(self.remote.url, 'test/model', 4, password='private-password')
        self.assertNotIn('private-password', repr(settings))
        self.assertNotIn('private-password', json.dumps(settings.profile()))
        self.assertNotIn(self.remote.url, self.client.get('/api/providers').text)
        for url in ('http://remote.example', 'https://user:pass@example.com', 'https://example.com?token=x'):
            with self.assertRaises(ValueError):
                OpenCodeSettings(url=url)


if __name__ == '__main__':
    unittest.main()
