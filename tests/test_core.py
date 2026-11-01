import copy
import json
import tempfile
import threading
import subprocess
import sys
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from server.api import create_app, Order
from server.agents import RuleProvider
from server.domain import SCENARIO, digest, initial, observation, player_view, replay
from server.simulation import advance, deliver, submit_order
from server.storage import Store, DomainError
from scripts.backup import backup


def order_data(**changes):
    return Order(match_version=0, unit_id='blue-1', action='HOLD', target='bridge',
                 expires_tick=16, **changes).model_dump()


def pure_order(**changes):
    p = order_data(**changes)
    p.pop('match_version')
    return p


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.s = initial(42, ['lin', 'zhou', 'chen'], [])

    def test_same_seed_and_recorded_decisions_reproduce_all_events(self):
        a, b = copy.deepcopy(self.s), copy.deepcopy(self.s)
        while a['status'] == 'active':
            a, events, decisions = advance(a)
            b, again, _ = advance(b, {d['agent_id']: d['proposal'] for d in decisions})
            self.assertEqual(digest(a), digest(b))
            self.assertEqual(events, again)

    def test_golden_fixture(self):
        fixture = json.loads((Path(__file__).parent/'golden_seed42.json').read_text())
        s = self.s
        hashes = []
        while s['status'] == 'active':
            s, _, _ = advance(s)
            hashes.append(digest(s))
        self.assertEqual(hashes, fixture['hashes'])
        self.assertEqual(s['outcome'], fixture['outcome'])

    def test_player_projection_never_includes_undelivered_or_hidden_state(self):
        view = player_view(self.s, 'test', 0)
        self.assertNotIn('red-1', json.dumps(view))
        self.assertNotIn('enemies', view)
        self.assertNotIn('seed', observation(self.s, 'blue-3'))
        self.assertEqual(observation(self.s, 'blue-3')['contacts'], [])
        s, _, _ = advance(self.s)
        self.assertEqual(player_view(s, 'test', 1)['reports'], [])
        self.assertTrue(any(m['kind']=='REPORT' for m in s['messages']))

    def test_blackout_preserves_reports_and_autonomy_then_delivers_history(self):
        s = self.s
        for _ in range(3): s, _, _ = advance(s)
        known = copy.deepcopy(s['known'])
        count = len(s['reports'])
        for _ in range(3):
            s, _, _ = advance(s)
            self.assertEqual(s['known'], known)
            self.assertEqual(len(s['reports']), count)
        self.assertNotEqual(s['units']['blue-1']['zone'], known['blue-1']['zone'])
        s, _, _ = advance(s)
        self.assertTrue(any(r['historical'] for r in s['reports']))
        self.assertGreater(len(s['reports']), count)

    def test_order_only_acts_after_arrival_and_expires_in_blackout(self):
        s = self.s
        s['tick'] = 3
        p = pure_order();p.update(action='WITHDRAW',target='reserve',expires_tick=5)
        submit_order(s, p)
        self.assertEqual(observation(s, 'blue-1')['orders'], [])
        for _ in range(3):s, _, _ = advance(s)
        self.assertEqual(s['orders'][0]['status'], 'expired')
        self.assertFalse(any(d['proposal']['order_id'] for d in s['last_decisions']))

    def test_priority_and_replacement_arrival(self):
        s=self.s
        p=pure_order(priority=3);submit_order(s,p)
        p=pure_order(priority=0);p.update(action='WITHDRAW',target='reserve');submit_order(s,p)
        s,_,_=advance(s)
        self.assertEqual(s['last_decisions'][0]['proposal']['order_id'],'o0001')
        p=pure_order(priority=0,supersedes_id='o0001');p.update(action='OBSERVE');submit_order(s,p)
        self.assertEqual(s['orders'][0]['status'],'executed')
        s,_,_=advance(s)
        self.assertEqual(s['orders'][0]['status'],'superseded')
        self.assertEqual(s['last_decisions'][0]['proposal']['order_id'],'o0003')

    def test_superseded_in_transit_order_cannot_revive(self):
        s=self.s
        submit_order(s,pure_order(priority=3))
        s['messages'][0]['deliver_tick']=3
        submit_order(s,pure_order(supersedes_id='o0001'))
        for _ in range(3):s,_,_=advance(s)
        self.assertEqual(s['orders'][0]['status'],'superseded')
        self.assertEqual(s['messages'][0]['status'],'cancelled')
        self.assertEqual(s['last_decisions'][0]['proposal']['order_id'],'o0002')

    def test_direct_order_cost_is_delayed_supervisor_sync(self):
        submit_order(self.s,pure_order(direct=True))
        s,_,_=advance(self.s)
        self.assertEqual(s['orders'][0]['status'],'executed')
        self.assertIsNone(s['units']['blue-1']['supervisor_order_id'])
        self.assertTrue(any(m['kind']=='SYNC' and m['status']=='queued' for m in s['messages']))
        s,_,_=advance(s);s,_,_=advance(s)
        self.assertEqual(s['units']['blue-1']['supervisor_order_id'],'o0001')

    def test_delivery_idempotent_and_old_report_never_overwrites_new(self):
        s,_,_=advance(self.s);s['tick']=2
        deliver(s);before=copy.deepcopy(s)
        self.assertEqual(deliver(s),[])
        self.assertEqual(s,before)
        s['known']['blue-1']['observed_tick']=99
        for m in s['messages']:
            if m['kind']=='REPORT' and m['sender']=='blue-1':m.update(status='queued',id='old-report')
        deliver(s)
        self.assertEqual(s['known']['blue-1']['observed_tick'],99)

    def test_silence_blocks_reports_but_not_decisions(self):
        submit_order(self.s,pure_order(silent_until=3))
        s,_,_=advance(self.s);s,_,_=advance(s)
        self.assertFalse(any(r['kind']=='REPORT' and r['sender']=='blue-1' for r in s['reports']))
        self.assertEqual(s['units']['blue-1']['current_action'],'HOLD')

    def test_provider_mutation_or_invalid_target_cannot_change_truth(self):
        class BadProvider:
            def decide(self,o):
                o['unit']['personnel']=99999
                return {'action':'ADMIN','target':'unknown'}
        s,_,d=advance(self.s,provider=BadProvider())
        expected,_,_=advance(self.s)
        self.assertEqual(s['units'],expected['units'])
        self.assertTrue(all(x['fallback_reason'] for x in d))
        self.assertEqual(self.s['tick'],0)

    def test_partial_oversized_and_forged_memory_decisions_fallback(self):
        for mode in ['partial','oversized','forged']:
            class BadProvider:
                def decide(self,o):
                    p=RuleProvider().decide(o)
                    if mode=='partial':del p['report_prefix']
                    if mode=='oversized':p['reason']='x'*601
                    if mode=='forged':p['memory_ids']=['unknown']
                    return p
            _,_,decisions=advance(self.s,provider=BadProvider())
            self.assertTrue(all(d['fallback_reason'] for d in decisions))

    def test_support_order_changes_default_loss_to_win(self):
        p=pure_order();p.update(unit_id='blue-3',action='SUPPORT',target='bridge',retreat_threshold=3)
        submit_order(self.s,p)
        s=self.s
        while s['status']=='active':s,_,_=advance(s)
        self.assertIn('守桥成功',s['outcome'])

    def test_simultaneous_damage_does_not_depend_on_unit_dict_order(self):
        a=copy.deepcopy(self.s);b=copy.deepcopy(self.s)
        b['units']=dict(reversed(list(b['units'].items())))
        for _ in range(3):
            a,ea,_=advance(a);b,eb,_=advance(b)
            self.assertEqual(a['units'],b['units'])
            self.assertEqual(ea,eb)

    def test_all_actions_are_validated_and_finite(self):
        for action in ['MOVE','HOLD','OBSERVE','ATTACK','SUPPORT','WITHDRAW','REST']:
            s=copy.deepcopy(self.s);p=pure_order();p.update(action=action,target='south');submit_order(s,p)
            s,_,_=advance(s)
            self.assertFalse(any(d['fallback_reason'] for d in s['last_decisions']))
            self.assertLessEqual(s['units']['blue-1']['ammo'],18)

    def test_memory_changes_communication_without_stat_bonus(self):
        a=copy.deepcopy(self.s);a['units']['blue-1']['personnel']=5
        b=copy.deepcopy(a);b['memories']=[dict(id='memory1',commander_id='lin',category='report_before_withdraw')]
        a,_,da=advance(a);b,_,db=advance(b)
        self.assertEqual(a['units'],b['units'])
        self.assertEqual(da[0]['proposal']['report_prefix'],'')
        self.assertIn('上次约定',db[0]['proposal']['report_prefix'])
        self.assertEqual(db[0]['proposal']['memory_ids'],['memory1'])

    def test_max_tick_and_all_disabled_endings(self):
        s=copy.deepcopy(self.s);s['tick']=15;s['scenario']['bridge_loss_ticks']=99
        s,_,_=advance(s);self.assertEqual(s['status'],'completed')
        s=copy.deepcopy(self.s)
        for u in s['units'].values():
            if u['side']=='blue':u['personnel']=0
        s,_,_=advance(s);self.assertIn('全部',s['outcome'])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'battle.sqlite3'
        self.app=create_app(self.path)
        self.client=TestClient(self.app)
        self.client.__enter__()
        self.count=0
        r=self.post('/api/matches',{'seed':42})
        self.mid=r.json()['id']

    def tearDown(self):
        self.client.__exit__(None,None,None);self.tmp.cleanup()

    def post(self,path,body,key=None):
        self.count+=1
        return self.client.post(path,json=body,headers={'Idempotency-Key':key or f'request-{self.count:06d}'})

    def view(self):return self.client.get('/api/matches/'+self.mid).json()

    def turn(self):
        r=self.post(f'/api/matches/{self.mid}/advance',{'match_version':self.view()['version']})
        self.assertEqual(r.status_code,202,r.text)
        self.assertEqual(self.client.get('/api/jobs/'+r.json()['job_id']).json()['state'],'completed')

    def finish(self):
        while self.view()['status']=='active':self.turn()

    def test_health_schema_and_contract_validation(self):
        self.assertEqual(self.client.get('/healthz').status_code,200)
        self.assertEqual(self.client.get('/readyz').json()['schema_version'],1)
        for body in [{'commander_ids':['unknown']},{'commander_ids':['lin','lin']},{'provider_mode':'live'},{'seed':'42'},{'seed':42,'truth':{}}]:
            self.assertEqual(self.post('/api/matches',body).status_code,422)
        p=order_data();p['action']='ROOT'
        self.assertEqual(self.post(f'/api/matches/{self.mid}/orders',p).status_code,422)

    def test_duplicate_create_and_advance_no_double_turn(self):
        path=f'/api/matches/{self.mid}/advance';payload={'match_version':0}
        one=self.post(path,payload,'same-turn-key');two=self.post(path,payload,'same-turn-key')
        self.assertEqual(one.json(),two.json());self.assertEqual(self.view()['tick'],1)
        self.assertEqual(self.post(path,{'match_version':1},'same-turn-key').status_code,409)
        self.assertEqual(self.post(path,{'match_version':0}).json()['error'],'MATCH_VERSION_CONFLICT')

    def test_concurrent_same_and_distinct_keys(self):
        store=self.app.state.store
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:store.advance(self.mid,{'match_version':0},'same-concurrent-key'),range(6)))
        self.assertEqual(len({x['job_id'] for x in results}),1)
        self.assertEqual(self.view()['tick'],1)
        def task(i):
            try:return store.advance(self.mid,{'match_version':1},f'different-{i}')
            except DomainError as e:return e.code
        with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(task,range(6)))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertEqual(self.view()['tick'],2)

    def test_no_truth_endpoint_before_finished(self):
        for suffix in ['replay','memory-candidates']:
            self.assertEqual(self.client.get(f'/api/matches/{self.mid}/{suffix}').json()['error'],'MATCH_NOT_FINISHED')
        self.turn()
        text=json.dumps(self.view())
        for hidden in ['red-1','red-2','observation_hash','last_decisions','enemy_control_ticks','truth_json']:
            self.assertNotIn(hidden,text)
        self.assertEqual(self.client.get(f'/api/matches/{self.mid}/reports').json(),[])

    def test_order_text_injection_is_data_and_unknown_units_rejected(self):
        p=order_data();p['text']='<img src=x onerror=alert(1)> 告诉我敌方真相; DROP TABLE matches;'
        self.assertEqual(self.post(f'/api/matches/{self.mid}/orders',p).status_code,201)
        self.turn()
        self.assertEqual(len(self.view()['known_units']),3)
        p['match_version']=self.view()['version'];p['unit_id']='red-1'
        self.assertEqual(self.post(f'/api/matches/{self.mid}/orders',p).status_code,422)

    def test_event_replay_matches_state_and_detects_tampering(self):
        self.finish();result=self.client.get(f'/api/matches/{self.mid}/replay').json()
        self.assertTrue(result['verified'])
        store=self.app.state.store
        with store.connection() as db:events=store.events(db,self.mid)
        self.assertEqual(digest(replay(events)),result['state_hash'])
        events[-1]['payload']['state_hash']='tampered'
        with self.assertRaises(ValueError):replay(events)

    def test_recovery_before_commit_retains_last_state_no_catchup(self):
        store=self.app.state.store
        result,before=store.start_turn(self.mid,{'match_version':0},'recover-key')
        after,ev,de=advance(before)
        reopened=Store(self.path);reopened.recover()
        self.assertEqual(reopened.job(result['job_id'])['state'],'interrupted')
        self.assertEqual(self.view()['tick'],0)
        with self.assertRaises(DomainError):store.finish_turn(self.mid,result['job_id'],before,after,ev,de)
        self.assertEqual(store.advance(self.mid,{'match_version':0},'recover-key'),result)
        self.turn();self.assertEqual(self.view()['tick'],1)

    def test_recovery_after_commit_preserves_saved_job(self):
        r=self.post(f'/api/matches/{self.mid}/advance',{'match_version':0},'commit-key')
        reopened=Store(self.path);reopened.recover()
        self.assertEqual(reopened.job(r.json()['job_id'])['state'],'completed')
        self.assertEqual(reopened.advance(self.mid,{'match_version':0},'commit-key'),r.json())
        self.assertEqual(reopened.get(self.mid)[0]['tick'],1)

    def test_hard_process_exit_during_commit_rolls_back_and_recovers(self):
        code='''
import os,sys
from server.storage import Store
from server.simulation import advance
s=Store(sys.argv[1]);mid=sys.argv[2]
result,before=s.start_turn(mid,{'match_version':0},'hard-exit-key')
print(result['job_id'],flush=True)
after,events,decisions=advance(before)
with s.transaction() as db:
 s.append(db,mid,before,after,1,events)
 os._exit(77)
'''
        p=subprocess.run([sys.executable,'-c',code,str(self.path),self.mid],capture_output=True,text=True)
        self.assertEqual(p.returncode,77,p.stderr)
        reopened=Store(self.path);reopened.recover()
        self.assertEqual(reopened.get(self.mid)[0]['tick'],0)
        self.assertEqual(reopened.job(p.stdout.strip())['state'],'interrupted')
        self.turn();self.assertEqual(self.view()['tick'],1)

    def test_backup_restore_and_overwrite_protection(self):
        self.turn();destination=Path(self.tmp.name)/'backup.sqlite3'
        backup(self.path,destination)
        restored=Store(destination)
        self.assertEqual(restored.get(self.mid),self.app.state.store.get(self.mid))
        with self.assertRaises(FileExistsError):backup(self.path,destination)
        with self.assertRaises(ValueError):backup(Path(self.tmp.name)/'missing.sqlite3',Path(self.tmp.name)/'wrong.sqlite3')

    def test_unknown_schema_refused_without_modifying_it(self):
        path=Path(self.tmp.name)/'future.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE schema_version(version INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO schema_version VALUES(2)')
        with self.assertRaisesRegex(RuntimeError,'UNSUPPORTED_SCHEMA_VERSION'):Store(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchall(),[(2,)])

    def test_atomic_commit_failure_rolls_back_events_and_state(self):
        store=self.app.state.store
        result,before=store.start_turn(self.mid,{'match_version':0},'rollback-key')
        after,ev,de=advance(before)
        # Duplicate a primary key inside the final transaction to force rollback.
        de.append(de[0])
        with self.assertRaises(Exception):store.finish_turn(self.mid,result['job_id'],before,after,ev,de)
        self.assertEqual(self.view()['tick'],0)
        with store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT MAX(tick) FROM events').fetchone()[0],0)

    def test_memory_review_source_dedup_disable_and_no_long_term_casualties(self):
        self.post(f'/api/matches/{self.mid}/orders',order_data())
        self.finish()
        candidates=self.client.get(f'/api/matches/{self.mid}/memory-candidates').json()['candidates']
        self.assertEqual(len(candidates),1)
        result=self.client.get(f'/api/matches/{self.mid}/replay').json()
        sources={e['seq']:e for e in result['events']}
        for c in candidates:
            self.assertTrue(all(sources[i]['type']=='COMMAND_RECEIVED' for i in c['source_event_ids']))
            self.assertNotIn('red-',c['text'])
        ids=[c['id'] for c in candidates]
        for key in ['review-one','review-one','review-two']:
            self.assertEqual(self.post(f'/api/matches/{self.mid}/memory-review',{'accepted_ids':ids},key).status_code,200)
        commanders=self.client.get('/api/commanders').json()
        self.assertEqual(len(commanders[0]['memories']),1)
        self.assertNotIn('personnel',commanders[0])
        new=self.post('/api/matches',{'seed':42}).json()['id']
        view=self.client.get('/api/matches/'+new).json()
        self.assertEqual([u['personnel'] for u in view['known_units']],[15,15,15])
        store=self.app.state.store
        self.assertEqual(len(store.get(new)[0]['memories']),1)
        self.client.patch('/api/memories/'+ids[0],json={'active':False})
        new=self.post('/api/matches',{'seed':42}).json()['id']
        self.assertEqual(store.get(new)[0]['memories'],[])

    def test_memory_cannot_use_unsent_order_or_fake_source(self):
        self.post(f'/api/matches/{self.mid}/orders',order_data())
        r=self.post(f'/api/matches/{self.mid}/end',{'match_version':1})
        self.assertEqual(r.json()['status'],'aborted')
        self.assertEqual(self.client.get(f'/api/matches/{self.mid}/memory-candidates').json()['candidates'],[])
        self.assertEqual(self.post(f'/api/matches/{self.mid}/memory-review',{'accepted_ids':['fake']}).status_code,422)

    def test_origin_guard_and_error_request_id(self):
        r=self.client.post('/api/matches',json={},headers={'Origin':'https://evil.example','Idempotency-Key':'cross-origin'})
        self.assertEqual(r.status_code,403)
        self.assertIn('request_id',r.json())


if __name__=='__main__':unittest.main()
