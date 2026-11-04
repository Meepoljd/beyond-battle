"""Re-run committed inputs using saved decisions, never call a model."""
import copy
from .domain import digest
from .simulation import advance, event, submit_order


def verify_simulation(events):
    state = None
    pending = []
    commits = 0
    for e in events:
        if e['type'] != 'STATE_COMMITTED':
            pending.append({k: copy.deepcopy(e[k]) for k in ('type','tick','actor_id','payload')})
            continue
        target = copy.deepcopy(state or {})
        target.update(copy.deepcopy(e['payload']['delta']))
        if state is None:
            state = target
        elif target['tick'] > state['tick']:
            recorded = {d['agent_id']:d['proposal'] for d in target['last_decisions']}
            records = {d['agent_id']: d for d in target['last_decisions']}
            state, reproduced, _ = advance(state, recorded_decisions=recorded, recorded_records=records)
            if reproduced != pending:
                raise ValueError('REPLAY_EVENT_MISMATCH')
        elif pending and pending[0]['type'] == 'ORDER_SUBMITTED':
            reproduced = submit_order(state, pending[0]['payload'])
            if reproduced != pending:
                raise ValueError('REPLAY_ORDER_MISMATCH')
        elif pending and pending[0]['type'] == 'MATCH_ABORTED':
            state.update(status='aborted', outcome='玩家中止演习（不计胜利）')
        else:
            raise ValueError('REPLAY_UNKNOWN_OPERATION')
        if digest(state) != e['payload']['state_hash'] or state != target:
            raise ValueError('REPLAY_SIMULATION_MISMATCH')
        pending = []
        commits += 1
    if pending:
        raise ValueError('REPLAY_UNCOMMITTED_EVENTS')
    return {'verified': True, 'commits': commits, 'state_hash': digest(state)}
