"""SQLite event journal, atomic state commits, and recoverable rule turn jobs."""
import copy
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .domain import SCENARIO, canonical, digest, initial, replay, state_delta
from .simulation import advance, event, submit_order
from .replay import verify_simulation
from .opencode import OpenCodeProvider, OpenCodeSettings


class DomainError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, opencode_settings=None):
        self.path = str(path)
        self.opencode = opencode_settings or OpenCodeSettings()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone():
                versions = [r[0] for r in db.execute('SELECT version FROM schema_version')]
                if versions != [1]:
                    raise RuntimeError('UNSUPPORTED_SCHEMA_VERSION: use a matching application version; database was not migrated')
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
              INSERT OR IGNORE INTO schema_version VALUES(1);
              CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                state_json TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events(match_id TEXT, seq INTEGER, tick INTEGER,
                type TEXT, actor_id TEXT, payload_json TEXT, created_at TEXT,
                PRIMARY KEY(match_id,seq));
              CREATE TABLE IF NOT EXISTS snapshots(match_id TEXT, version INTEGER, tick INTEGER,
                state_json TEXT, state_hash TEXT, PRIMARY KEY(match_id,version));
              CREATE TABLE IF NOT EXISTS decisions(match_id TEXT, id TEXT, tick INTEGER,
                decision_json TEXT, PRIMARY KEY(match_id,id));
              CREATE TABLE IF NOT EXISTS requests(scope TEXT, key TEXT, fingerprint TEXT,
                response_json TEXT, PRIMARY KEY(scope,key));
              CREATE TABLE IF NOT EXISTS turn_jobs(id TEXT PRIMARY KEY, match_id TEXT,
                input_version INTEGER, state TEXT, error_code TEXT, created_at TEXT, updated_at TEXT);
              CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON turn_jobs(match_id)
                WHERE state IN ('pending','running');
              CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, commander_id TEXT,
                source_match_id TEXT, payload_json TEXT, active INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS exercise_reviews(match_id TEXT PRIMARY KEY,
                accepted_json TEXT, created_at TEXT);
              CREATE TABLE IF NOT EXISTS provider_reservations(job_id TEXT PRIMARY KEY,
                match_id TEXT NOT NULL, unit_ids_json TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def recover(self):
        # Single uvicorn worker only. Never silently resume or catch up offline time.
        with self.transaction() as db:
            db.execute("UPDATE turn_jobs SET state='interrupted', error_code='RESTART_BEFORE_COMMIT', updated_at=? WHERE state IN ('pending','running')", (now(),))

    def load(self, db, mid):
        row = db.execute('SELECT * FROM matches WHERE id=?', (mid,)).fetchone()
        if row is None:
            raise DomainError('MATCH_NOT_FOUND', 404)
        return json.loads(row['state_json']), row['version']

    def get(self, mid):
        with self.connection() as db:
            return self.load(db, mid)

    def cached(self, db, scope, key, payload):
        row = db.execute('SELECT * FROM requests WHERE scope=? AND key=?', (scope, key)).fetchone()
        if row:
            if row['fingerprint'] != digest(payload):
                raise DomainError('IDEMPOTENCY_KEY_REUSED')
            return json.loads(row['response_json'])

    def remember(self, db, scope, key, payload, result):
        db.execute('INSERT INTO requests VALUES(?,?,?,?)', (scope, key, digest(payload), canonical(result)))

    def guard(self, db, mid, state, version, expected):
        if db.execute("SELECT 1 FROM turn_jobs WHERE match_id=? AND state IN ('pending','running')", (mid,)).fetchone():
            raise DomainError('TURN_IN_PROGRESS')
        if expected != version:
            raise DomainError('MATCH_VERSION_CONFLICT')
        if state['status'] != 'active':
            raise DomainError('MATCH_FINISHED')

    def append(self, db, mid, before, after, version, events):
        seq = db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE match_id=?', (mid,)).fetchone()[0]
        events = events + [event('STATE_COMMITTED', after['tick'], 'system',
            {'delta': state_delta(before, after), 'state_hash': digest(after), 'version': version})]
        for e in events:
            seq += 1
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                (mid, seq, e['tick'], e['type'], e['actor_id'], canonical(e['payload']), now()))
        db.execute('UPDATE matches SET state_json=?, version=? WHERE id=?', (canonical(after), version, mid))
        db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?)', (mid, version, after['tick'], canonical(after), digest(after)))

    def create(self, payload, key):
        with self.transaction() as db:
            cached = self.cached(db, 'create', key, payload)
            if cached:
                return cached
            memories = self.list_memories(db, active_only=True) if payload['use_memories'] else []
            state = initial(payload['seed'], payload['commander_ids'], memories)
            if payload.get('provider_mode', 'rules') == 'opencode':
                if not self.opencode.enabled:
                    raise DomainError('OPENCODE_NOT_CONFIGURED', 503)
                state['provider_config'] = self.opencode.profile()
            mid = uuid.uuid4().hex
            db.execute('INSERT INTO matches VALUES(?,?,?,?)', (mid, 0, '{}', now()))
            self.append(db, mid, {}, state, 0, [event('MATCH_CREATED', 0, 'HQ', {'seed': payload['seed'], 'scenario_version': SCENARIO['version']})])
            result = {'id': mid, 'version': 0}
            self.remember(db, 'create', key, payload, result)
            return result

    def order(self, mid, payload, key):
        with self.transaction() as db:
            scope = mid + ':order'
            cached = self.cached(db, scope, key, payload)
            if cached:
                return cached
            state, version = self.load(db, mid)
            self.guard(db, mid, state, version, payload['match_version'])
            data = {k: v for k, v in payload.items() if k != 'match_version'}
            u = state['units'].get(data['unit_id'])
            zone_ids = {z['id'] for z in state['scenario']['zones']}
            if not u or u['side'] != 'blue' or data['target'] not in zone_ids:
                raise DomainError('INVALID_ORDER', 422)
            if not state['tick'] < data['expires_tick'] <= state['scenario']['max_ticks']:
                raise DomainError('INVALID_ORDER_EXPIRY', 422)
            if data['supersedes_id'] and not any(o['id'] == data['supersedes_id'] and o['unit_id'] == data['unit_id'] for o in state['orders']):
                raise DomainError('INVALID_SUPERSEDES', 422)
            before = copy.deepcopy(state)
            events = submit_order(state, data)
            self.append(db, mid, before, state, version+1, events)
            result = {'order_id': state['orders'][-1]['id'], 'version': version+1, 'status': '传输中'}
            self.remember(db, scope, key, payload, result)
            return result

    def start_turn(self, mid, payload, key):
        with self.transaction() as db:
            scope = mid + ':advance'
            cached = self.cached(db, scope, key, payload)
            if cached:
                return cached, None
            state, version = self.load(db, mid)
            self.guard(db, mid, state, version, payload['match_version'])
            jid = uuid.uuid4().hex
            db.execute('INSERT INTO turn_jobs VALUES(?,?,?,?,?,?,?)', (jid, mid, version, 'running', None, now(), now()))
            if state.get('provider_config', {}).get('mode') == 'opencode':
                used = sum(len(json.loads(r[0])) for r in db.execute(
                    'SELECT unit_ids_json FROM provider_reservations WHERE match_id=?', (mid,)))
                limit = min(state['provider_config']['max_calls'], self.opencode.max_calls)
                units = sorted(u['id'] for u in state['units'].values()
                               if u['side'] == 'blue' and u['personnel'] > 0)
                reserved = units[:max(0, limit-used)] if self.opencode.enabled else []
                db.execute('INSERT INTO provider_reservations VALUES(?,?,?,?)',
                           (jid, mid, canonical(reserved), now()))
            result = {'job_id': jid}
            self.remember(db, scope, key, payload, result)
            return result, state

    def finish_turn(self, mid, jid, before, after, events, decisions):
        with self.transaction() as db:
            job = db.execute('SELECT * FROM turn_jobs WHERE id=?', (jid,)).fetchone()
            _, version = self.load(db, mid)
            if job['state'] == 'completed':
                return
            if job['state'] != 'running' or version != job['input_version']:
                raise DomainError('TURN_COMMIT_CONFLICT')
            self.append(db, mid, before, after, version+1, events)
            for d in decisions:
                db.execute('INSERT INTO decisions VALUES(?,?,?,?)', (mid, d['id'], d['tick'], canonical(d)))
            db.execute("UPDATE turn_jobs SET state='completed',updated_at=? WHERE id=?", (now(), jid))

    def advance(self, mid, payload, key):
        result, before = self.start_turn(mid, payload, key)
        if before is None:
            return result
        self.run_turn(mid, result['job_id'], before)
        return result

    def run_turn(self, mid, jid, before):
        try:
            # Provider boundary intentionally outside any SQLite transaction.
            provider = None
            if before.get('provider_config', {}).get('mode') == 'opencode':
                with self.connection() as db:
                    row = db.execute('SELECT unit_ids_json FROM provider_reservations WHERE job_id=?', (jid,)).fetchone()
                personas = {c['id']: {k: c[k] for k in ('name', 'role', 'trait')}
                            for c in before['scenario']['commanders']}
                provider = OpenCodeProvider(self.opencode, before['provider_config'],
                                            json.loads(row[0]) if row else [], personas)
            after, events, decisions = advance(before, provider=provider)
            self.finish_turn(mid, jid, before, after, events, decisions)
        except Exception:
            with self.transaction() as db:
                db.execute("UPDATE turn_jobs SET state='failed',error_code='TURN_FAILED',updated_at=? WHERE id=? AND state='running'", (now(), jid))
            raise

    def provider_usage(self, mid):
        with self.connection() as db:
            return sum(len(json.loads(r[0])) for r in db.execute(
                'SELECT unit_ids_json FROM provider_reservations WHERE match_id=?', (mid,)))

    def job(self, jid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM turn_jobs WHERE id=?', (jid,)).fetchone()
            if not row:
                raise DomainError('JOB_NOT_FOUND', 404)
            return dict(row)

    def end(self, mid, payload, key):
        with self.transaction() as db:
            scope = mid + ':end'
            cached = self.cached(db, scope, key, payload)
            if cached:
                return cached
            s, v = self.load(db, mid)
            self.guard(db, mid, s, v, payload['match_version'])
            before = copy.deepcopy(s)
            s.update(status='aborted', outcome='玩家中止演习（不计胜利）')
            self.append(db, mid, before, s, v+1, [event('MATCH_ABORTED', s['tick'], 'HQ', {})])
            result = {'version': v+1, 'status': 'aborted'}
            self.remember(db, scope, key, payload, result)
            return result

    def events(self, db, mid):
        return [dict(seq=r['seq'], tick=r['tick'], type=r['type'], actor_id=r['actor_id'],
                     payload=json.loads(r['payload_json'])) for r in db.execute('SELECT * FROM events WHERE match_id=? ORDER BY seq', (mid,))]

    def get_replay(self, mid):
        with self.connection() as db:
            s, version = self.load(db, mid)
            if s['status'] == 'active':
                raise DomainError('MATCH_NOT_FINISHED')
            events = self.events(db, mid)
            rebuilt = replay(events)
            simulation = verify_simulation(events)
            return {'state_hash': digest(rebuilt), 'verified': digest(rebuilt) == digest(s) and simulation['verified'],
                    'simulation_verification': simulation,
                    'ruleset_version': s['scenario']['ruleset_version'], 'seed': s['seed'],
                    'truth': rebuilt['units'], 'events': [e for e in events if e['type'] != 'STATE_COMMITTED'],
                    'reports': s['reports'], 'messages': s['messages'], 'version': version}

    def candidates(self, db, mid):
        s, _ = self.load(db, mid)
        if s['status'] == 'active':
            raise DomainError('MATCH_NOT_FINISHED')
        candidates = {}
        for e in self.events(db, mid):
            if e['type'] != 'COMMAND_RECEIVED' or not e['payload']['report_before_withdraw']:
                continue
            commander = s['units'][e['actor_id']]['commander_id']
            candidates[commander] = dict(id=f'{mid}:{commander}:report_before_withdraw', commander_id=commander,
                source_match_id=mid, source_event_ids=[e['seq']], category='report_before_withdraw',
                text='这次演习收到玩家要求：自主撤退时先报告兵力与弹药。今后撤退报告沿用此格式。',
                confidence=1.0, exercise=True, extraction_version='rules-memory-v1', active=True)
        return list(candidates.values())

    def memory_candidates(self, mid):
        with self.connection() as db:
            candidates = self.candidates(db, mid)
            review = db.execute('SELECT * FROM exercise_reviews WHERE match_id=?', (mid,)).fetchone()
            return {'candidates': candidates, 'reviewed': bool(review),
                    'accepted_ids': json.loads(review['accepted_json']) if review else []}

    def review(self, mid, payload, key):
        with self.transaction() as db:
            scope = mid + ':review'
            cached = self.cached(db, scope, key, payload)
            if cached:
                return cached
            candidates = {c['id']: c for c in self.candidates(db, mid)}
            ids = sorted(set(payload['accepted_ids']))
            if set(ids) - candidates.keys():
                raise DomainError('INVALID_MEMORY_SOURCE', 422)
            previous = db.execute('SELECT * FROM exercise_reviews WHERE match_id=?', (mid,)).fetchone()
            if previous and json.loads(previous['accepted_json']) != ids:
                raise DomainError('REVIEW_ALREADY_COMPLETED')
            for cid in ids:
                c = candidates[cid]
                db.execute('INSERT OR IGNORE INTO memories VALUES(?,?,?,?,1)', (cid, c['commander_id'], mid, canonical(c)))
            db.execute('INSERT OR IGNORE INTO exercise_reviews VALUES(?,?,?)', (mid, canonical(ids), now()))
            result = {'accepted_ids': ids, 'reviewed': True}
            self.remember(db, scope, key, payload, result)
            return result

    def list_memories(self, db, active_only=False):
        rows = db.execute('SELECT * FROM memories ORDER BY rowid DESC').fetchall()
        # Injection capped at three distinct recent preferences per commander.
        result, counts, seen = [], {}, set()
        for r in rows:
            m = {**json.loads(r['payload_json']), 'active': bool(r['active'])}
            identity = (m['commander_id'], m['category'])
            if active_only and (not m['active'] or identity in seen or counts.get(m['commander_id'], 0) >= 3):
                continue
            result.append(m)
            seen.add(identity)
            counts[m['commander_id']] = counts.get(m['commander_id'], 0) + 1
        return result

    def commanders(self):
        with self.connection() as db:
            memories = self.list_memories(db)
        return [{**c, 'memories': [m for m in memories if m['commander_id'] == c['id']]} for c in SCENARIO['commanders']]

    def set_memory(self, mid, active):
        with self.transaction() as db:
            if db.execute('UPDATE memories SET active=? WHERE id=?', (int(active), mid)).rowcount == 0:
                raise DomainError('MEMORY_NOT_FOUND', 404)
        return {'active': active}
