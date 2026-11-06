"""Deterministic simultaneous turns and simulated radio; no I/O."""
import copy
import hashlib
from .domain import digest, observation, zones
from .agents import RuleProvider, validate


def event(kind, tick, actor, payload):
    return {'type': kind, 'tick': tick, 'actor_id': actor, 'payload': copy.deepcopy(payload)}


def radio_delay(s, direct=False):
    c = s['scenario']['communication']
    return (1 if direct else c['base_delay']) + (c['extra_delay'] if s['tick'] in c['delayed_ticks'] else 0)


def enqueue(s, kind, sender, recipient, payload, ttl=8, direct=False, silent_until=0):
    mid = f'm{len(s["messages"]) + 1:05d}'
    msg = dict(id=mid, kind=kind, sender=sender, recipient=recipient, payload=copy.deepcopy(payload),
               created_tick=s['tick'], deliver_tick=s['tick'] + radio_delay(s, direct),
               expires_tick=s['tick'] + ttl, status='queued', silent_until=silent_until)
    s['messages'].append(msg)
    return msg


def submit_order(s, payload):
    o = {**copy.deepcopy(payload), 'id': f'o{len(s["orders"])+1:04d}', 'submitted_tick': s['tick'],
         'status': 'submitted', 'public_status': '传输中', 'arrival_seq': 0}
    s['orders'].append(o)
    enqueue(s, 'ORDER', 'HQ', o['unit_id'], {'order_id': o['id']},
            ttl=o['expires_tick'] - s['tick'], direct=o['direct'])
    return [event('ORDER_SUBMITTED', s['tick'], 'HQ', o)]


def deliver(s):
    events = []
    tick = s['tick']
    blackout = tick in s['scenario']['communication']['blackout_ticks']
    # Iterate snapshot; responses cannot be delivered in same tick.
    for m in list(s['messages']):
        if m['status'] != 'queued':
            continue
        if m['kind'] == 'ORDER':
            existing = next(x for x in s['orders'] if x['id'] == m['payload']['order_id'])
            if existing['status'] == 'superseded':
                m['status'] = 'cancelled'
                continue
        if m['kind'] == 'ORDER' and tick > m['expires_tick']:
            m['status'] = 'expired'
            o = next(x for x in s['orders'] if x['id'] == m['payload']['order_id'])
            o['status'] = 'expired'
            # HQ can know its own order expiry without a remote observation.
            o['public_status'] = '已过期'
            events.append(event('ORDER_EXPIRED', tick, o['unit_id'], {'order_id': o['id']}))
            continue
        if blackout or m['deliver_tick'] > tick or m['silent_until'] >= tick:
            continue
        if m['kind'] == 'ORDER' and s['units'][m['recipient']]['personnel'] <= 0:
            continue
        m['status'] = 'delivered'
        m['received_tick'] = tick
        historical = tick > m['expires_tick']
        events.append(event('MESSAGE_DELIVERED', tick, m['recipient'],
                            {'message_id': m['id'], 'kind': m['kind'], 'historical': historical}))
        if m['kind'] == 'ORDER':
            o = next(x for x in s['orders'] if x['id'] == m['payload']['order_id'])
            o['status'], o['arrival_seq'] = 'delivered', len(events) + int(m['id'][1:]) * 10
            if o['supersedes_id']:
                old = next(x for x in s['orders'] if x['id'] == o['supersedes_id'])
                old['status'] = 'superseded'
                enqueue(s, 'ACK', o['unit_id'], 'HQ', {'order_id': old['id'], 'status': '已替换'})
            if not o['direct']:
                s['units'][o['unit_id']]['supervisor_order_id'] = o['id']
            else:
                sync = enqueue(s, 'SYNC', o['unit_id'], 'supervisor', {'order_id': o['id'], 'unit_id': o['unit_id']})
                sync['deliver_tick'] += 1
            enqueue(s, 'ACK', o['unit_id'], 'HQ', {'order_id': o['id'], 'status': '已收到'})
            events.append(event('COMMAND_RECEIVED', tick, o['unit_id'], o))
        elif m['kind'] == 'SYNC':
            s['units'][m['payload']['unit_id']]['supervisor_order_id'] = m['payload']['order_id']
            enqueue(s, 'ACK', m['payload']['unit_id'], 'HQ', {'order_id': m['payload']['order_id'],
                    'status': '越级命令已同步上级', 'sync_only': True})
        elif m['kind'] == 'ACK':
            o = next(x for x in s['orders'] if x['id'] == m['payload']['order_id'])
            if m['payload'].get('sync_only'):
                status_text = m['payload']['status']
            else:
                # A late receipt must not roll executed status back to received.
                rank = {'传输中': 0, '已收到': 1, '已执行': 2, '已调整': 2, '已替换': 3, '已过期': 3}
                new = m['payload']['status']
                if rank[new] >= rank.get(o['public_status'], 0):
                    o['public_status'] = new
                status_text = new
            s['reports'].append(dict(id=m['id'], seq=len(s['reports'])+1, kind='ACK',
                sender=m['sender'], observed_tick=m['created_tick'], received_tick=tick,
                historical=historical, text=f"{o['id']}：{status_text}", contacts=[]))
        elif m['kind'] == 'REPORT':
            p = copy.deepcopy(m['payload'])
            p.update(id=m['id'], seq=len(s['reports'])+1, kind='REPORT', sender=m['sender'],
                     received_tick=tick, historical=historical)
            s['reports'].append(p)
            uid = p['unit']['id']
            if p['observed_tick'] >= s['known'][uid]['observed_tick']:
                s['known'][uid] = {**p['unit'], 'observed_tick': p['observed_tick'],
                    'received_tick': tick, 'source': m['sender'], 'confirmed_order_id': p['order_id']}
    return events


def roll(s, unit_id, target_id):
    key = f"{s['seed']}:{s['tick']}:{unit_id}:{target_id}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 3


def advance(original, recorded_decisions=None, provider=None, recorded_records=None):
    s = copy.deepcopy(original)
    s['tick'] += 1
    tick = s['tick']
    events = deliver(s)
    provider = provider or RuleProvider()
    proposals, observations, decisions = {}, {}, []
    for uid, u in sorted(s['units'].items()):
        if u['personnel'] <= 0:
            continue
        o = observation(s, uid)
        observations[uid] = o
        fallback = None
        unit_provider = (RuleProvider() if getattr(provider, 'blue_only', False)
                         and u['side'] != 'blue' else provider)
        try:
            p = copy.deepcopy(recorded_decisions[uid]) if recorded_decisions is not None else unit_provider.decide(copy.deepcopy(o))
            if not validate(p, o):
                raise ValueError('INVALID_DECISION')
        except Exception as exc:
            if recorded_decisions is not None:
                raise ValueError('INVALID_RECORDED_DECISION')
            from .agents import ProviderFailure
            fallback = exc.code if isinstance(exc, ProviderFailure) else 'PROVIDER_INVALID_OR_FAILED'
            p = RuleProvider().decide(o)
        proposals[uid] = p
        record = dict(id=f'{tick}:{uid}', tick=tick, agent_id=uid,
            observation_hash=digest(o), observation=o, proposal=copy.deepcopy(p),
            provider=getattr(unit_provider, 'name', 'custom'), fallback_reason=fallback)
        if hasattr(unit_provider, 'metadata'):
            record['provider_metadata'] = copy.deepcopy(unit_provider.metadata.get(uid, {}))
        if recorded_records is not None:
            saved = recorded_records[uid]
            for field in ('id', 'tick', 'agent_id', 'observation_hash', 'observation', 'proposal'):
                if saved[field] != record[field]:
                    raise ValueError('REPLAY_DECISION_MISMATCH')
            record = copy.deepcopy(saved)
        decisions.append(record)
    # Stable movement reservations. Every proposal came from the same observation boundary.
    zmap = zones(s)
    moves = {}
    for uid, p in proposals.items():
        u = s['units'][uid]
        if p['action'] in ('MOVE', 'ATTACK', 'SUPPORT', 'WITHDRAW'):
            moves[uid] = p['target']
        else:
            moves[uid] = u['zone']
    # Reject over-capacity incoming moves until a fixed point; staying units retain space.
    while True:
        rejected = []
        for dest in zmap:
            occupants = [uid for uid, pos in moves.items() if pos == dest]
            excess = len(occupants) - zmap[dest]['capacity']
            incoming = sorted([uid for uid in occupants if s['units'][uid]['zone'] != dest], reverse=True)
            rejected += incoming[:max(0, excess)]
        if not rejected:
            break
        for uid in rejected:
            moves[uid] = s['units'][uid]['zone']
            events.append(event('MOVE_BLOCKED', tick, uid, {'reason': '区域容量已满'}))
    for uid, p in proposals.items():
        u = s['units'][uid]
        prev = u['zone']
        u.update(zone=moves[uid], current_action=p['action'], objective=p['objective'])
        u['fatigue'] = max(0, min(100, u['fatigue'] + (-8 if p['action'] == 'REST' else 2)))
        if p['action'] == 'REST':
            u['morale'] = min(100, u['morale'] + 8)
        if prev != u['zone']:
            events.append(event('MOVED', tick, uid, {'from': prev, 'to': u['zone']}))
        if p['order_id']:
            o = next(x for x in s['orders'] if x['id'] == p['order_id'])
            status = 'executed' if p['action'] == o['action'] else 'adjusted'
            if o['status'] != status:
                o['status'] = status
                enqueue(s, 'ACK', uid, 'HQ', {'order_id': o['id'], 'status': '已执行' if status == 'executed' else '已调整'})
        events.append(event('DECISION', tick, uid, {**p, 'observation_hash': digest(observations[uid])}))
    # All damage is computed before any casualties are applied: no iteration first-shot advantage.
    damage = {uid: 0 for uid in s['units']}
    for uid, p in proposals.items():
        u = s['units'][uid]
        if u['ammo'] <= 0 or p['action'] in ('REST', 'WITHDRAW', 'OBSERVE', 'MOVE'):
            continue
        visible = {u['zone'], *zmap[u['zone']]['adjacent']}
        targets = sorted([v for v in s['units'].values() if v['side'] != u['side'] and
                          v['personnel'] > 0 and v['zone'] in visible], key=lambda v: (v['zone'] != u['zone'], v['id']))
        if targets:
            target = targets[0]
            loss = max(0, u['personnel'] // 7 + roll(s, uid, target['id']) - zmap[target['zone']]['cover'])
            damage[target['id']] += loss
            u['ammo'] -= 1
            events.append(event('FIRED', tick, uid, {'target': target['id'], 'loss': loss}))
    for uid, loss in damage.items():
        u = s['units'][uid]
        u['personnel'] = max(0, u['personnel'] - loss)
        u['morale'] = max(0, u['morale'] - loss * 4)
        u['suppression'] = min(100, loss * 10)
    for uid, p in proposals.items():
        u = s['units'][uid]
        if u['side'] != 'blue' or u['personnel'] <= 0:
            continue
        o = observations[uid]
        order = next((x for x in s['orders'] if x['id'] == p['order_id']), None)
        text = f"{p['report_prefix']}{p['reason']}。{zmap[u['zone']]['name']}，人员 {u['personnel']}，弹药 {u['ammo']}。"
        if o['contacts']:
            text += '本回合行动前观察到接近方向敌军，人数为估计。'
        else:
            text += '本回合行动前未发现敌情（不代表其他区域安全）。'
        payload = dict(unit=copy.deepcopy(u), contacts=o['contacts'], text=text,
                       observed_tick=tick, order_id=p['order_id'], reason=p['reason'])
        m = enqueue(s, 'REPORT', uid, 'HQ', payload,
                    ttl=s['scenario']['communication']['report_ttl'],
                    silent_until=order['silent_until'] if order else 0)
        events.append(event('REPORT_CREATED', tick, uid, {'message_id': m['id'], **payload}))
    blue = [u for u in s['units'].values() if u['side'] == 'blue' and u['personnel'] > 0]
    red = [u for u in s['units'].values() if u['side'] == 'red' and u['personnel'] > 0]
    bridge = s['scenario']['bridge']
    red_bridge = any(u['zone'] == bridge for u in red)
    blue_bridge = any(u['zone'] == bridge for u in blue)
    s['enemy_control_ticks'] = s['enemy_control_ticks'] + 1 if red_bridge and not blue_bridge else 0
    outcome = None
    if not blue:
        outcome = '友军全部失去作战能力'
    elif s['enemy_control_ticks'] >= s['scenario']['bridge_loss_ticks']:
        outcome = '桥梁失守'
    elif not red:
        outcome = '守桥成功：对手失去作战能力'
    elif tick >= s['scenario']['max_ticks']:
        outcome = '守桥成功：演习时限结束' if blue_bridge and not red_bridge else '演习结束：桥梁仍有争夺或无人驻守'
    if outcome:
        s.update(status='completed', outcome=outcome)
        events.append(event('MATCH_FINISHED', tick, 'system', {'outcome': outcome}))
    s['last_decisions'] = decisions
    return s, events, decisions
