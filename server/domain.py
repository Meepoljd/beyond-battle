"""Pure state construction, canonical hashing, observations and projections."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = json.loads((ROOT / 'scenarios/bridge_v1.json').read_text())
ACTIONS = {'MOVE', 'HOLD', 'OBSERVE', 'ATTACK', 'SUPPORT', 'WITHDRAW', 'REST'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def zones(s):
    return {z['id']: z for z in s['scenario']['zones']}


def initial(seed, selected, memories, scenario=None):
    cfg = copy.deepcopy(scenario or SCENARIO)
    s = dict(schema_version=1, seed=seed, scenario=cfg, tick=0, status='active',
             outcome=None, units={}, orders=[], messages=[], reports=[], known={},
             last_decisions=[], enemy_control_ticks=0, memories=copy.deepcopy(memories))
    for c in cfg['commanders']:
        if c['id'] not in selected:
            continue
        u = dict(id=c['unit_id'], commander_id=c['id'], side='blue', zone=c['zone'],
                 personnel=cfg['initial_personnel'], ammo=cfg['initial_ammo'], morale=100,
                 fatigue=0, suppression=0, current_action='HOLD', objective=c['zone'],
                 retreat_threshold=c['retreat_threshold'], supervisor_order_id=None)
        s['units'][u['id']] = u
        s['known'][u['id']] = {**copy.deepcopy(u), 'observed_tick': 0, 'received_tick': 0,
                                'source': '出发前编组', 'confirmed_order_id': None}
    for e in cfg['enemies']:
        s['units'][e['id']] = dict(id=e['id'], commander_id=e['id'], side='red', zone=e['zone'],
            personnel=e['personnel'], ammo=cfg['initial_ammo'], morale=100, fatigue=0,
            suppression=0, current_action='MOVE', objective='bridge', retreat_threshold=4,
            supervisor_order_id=None, route=e['route'])
    return s


def observation(s, unit_id):
    u = s['units'][unit_id]
    visible = {u['zone'], *zones(s)[u['zone']]['adjacent']}
    contacts = []
    for enemy in sorted(s['units'].values(), key=lambda x: x['id']):
        if enemy['side'] != u['side'] and enemy['personnel'] > 0 and enemy['zone'] in visible:
            # Estimates, never exact hidden personnel. Stable persona bias.
            estimate = max(5, round(enemy['personnel'] / 5) * 5)
            if u['commander_id'] == 'zhou':
                estimate = max(5, estimate - 5)
            contacts.append({'zone': enemy['zone'], 'estimated_personnel': estimate,
                             'confidence': '估计', 'observed_tick': s['tick']})
    orders = [copy.deepcopy(o) for o in s['orders'] if o['unit_id'] == unit_id
              and o['status'] in ('delivered', 'executed', 'adjusted')
              and o['expires_tick'] >= s['tick']]
    orders.sort(key=lambda o: (o['priority'], o['arrival_seq']), reverse=True)
    return dict(unit=copy.deepcopy(u), tick=s['tick'], contacts=contacts,
                orders=orders, map=copy.deepcopy(s['scenario']['zones']),
                retreat_zone=s['scenario']['retreat_zone'] if u['side'] == 'blue' else 'north',
                memories=[copy.deepcopy(m) for m in s['memories'] if m['commander_id'] == u['commander_id']][:3])


def player_view(s, match_id, version):
    tick = s['tick']
    cfg = s['scenario']
    channel = ('中断' if tick in cfg['communication']['blackout_ticks'] else
               '延迟' if tick in cfg['communication']['delayed_ticks'] else '正常')
    # Explicit allowlist: no raw truth, unsent reports, observations, or enemy policy.
    return dict(id=match_id, version=version, seed=s['seed'], tick=tick, status=s['status'],
        outcome=s['outcome'], provider_mode=s.get('provider_config', {}).get('mode', 'rules'),
        provider_fallbacks=sum(bool(d['fallback_reason']) for d in s['last_decisions']
            if d['agent_id'].startswith('blue-')), max_ticks=cfg['max_ticks'],
        minutes_per_tick=cfg['minutes_per_tick'], communication=channel,
        zones=copy.deepcopy(cfg['zones']), bridge=cfg['bridge'],
        known_units=copy.deepcopy(list(s['known'].values())),
        reports=copy.deepcopy(s['reports']),
        orders=[{k: copy.deepcopy(o[k]) for k in ('id', 'unit_id', 'action', 'target',
                'priority', 'direct', 'text', 'submitted_tick', 'expires_tick', 'public_status',
                'supersedes_id')} for o in s['orders']])


def state_delta(before, after):
    return {k: copy.deepcopy(v) for k, v in after.items() if k not in before or before[k] != v}


def replay(events):
    state = {}
    for e in events:
        if e['type'] == 'STATE_COMMITTED':
            state.update(copy.deepcopy(e['payload']['delta']))
            if digest(state) != e['payload']['state_hash']:
                raise ValueError('REPLAY_HASH_MISMATCH')
    return state
