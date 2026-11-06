"""Providers only see observations. They cannot mutate world state or use tools."""
from typing import Protocol


class AgentProvider(Protocol):
    def decide(self, observation: dict) -> dict: ...


class ProviderFailure(Exception):
    """A stable, public-safe failure code; never include remote bodies or secrets."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def next_hop(start, goal, map_zones):
    graph = {z['id']: z['adjacent'] for z in map_zones}
    queue = [(start, [])]
    visited = {start}
    for node, path in queue:
        if node == goal:
            return path[0] if path else start
        for dest in sorted(graph[node]):
            if dest not in visited:
                visited.add(dest)
                queue.append((dest, path + [dest]))
    return start


class RuleProvider:
    name = 'rules-v1'

    def decide(self, o):
        u = o['unit']
        order = o['orders'][0] if o['orders'] else None
        action = order['action'] if order else 'HOLD'
        goal = order['target'] if order else u['objective']
        reason = '遵循已收到的任务' if order else '依初始任务自主守备'
        threshold = order['retreat_threshold'] if order else u['retreat_threshold']
        autonomy = order['autonomy'] if order else True
        if u['personnel'] <= threshold and autonomy:
            action, goal, reason = 'WITHDRAW', o['retreat_zone'], '兵力达到保全阈值，自主撤退'
        elif u['ammo'] == 0 and autonomy:
            action, reason = 'REST', '弹药耗尽，停止射击并恢复士气'
        elif not order and u['side'] == 'red':
            goal = u['route'] if u['zone'] == 'north' else 'bridge'
            action = 'MOVE' if u['zone'] != goal else 'ATTACK'
            reason = '依接近路线推进并争夺桥梁'
        elif not order and u['commander_id'] == 'zhou' and not o['contacts']:
            action, reason = 'OBSERVE', '侦察倾向：持续观察接近路线'
        if action in ('MOVE', 'WITHDRAW', 'SUPPORT', 'ATTACK') and goal != u['zone']:
            target = next_hop(u['zone'], goal, o['map'])
        else:
            target = u['zone']
        memory_ids = [m['id'] for m in o['memories'] if m['category'] == 'report_before_withdraw']
        report_prefix = '依上次约定，先报告兵力与弹药。' if memory_ids and action == 'WITHDRAW' else ''
        if not report_prefix and order and order['report_before_withdraw'] and action == 'WITHDRAW':
            report_prefix = '依本次命令，先报告兵力与弹药。'
        return dict(action=action, target=target, objective=goal, reason=reason,
                    order_id=order['id'] if order else None, report_prefix=report_prefix,
                    memory_ids=memory_ids if report_prefix else [])


def validate(proposal, o):
    from .domain import ACTIONS
    if not isinstance(proposal, dict) or proposal.get('action') not in ACTIONS:
        return False
    fields = {'action', 'target', 'objective', 'reason', 'order_id', 'report_prefix', 'memory_ids'}
    if set(proposal) != fields:
        return False
    if any(not isinstance(proposal[k], str) or len(proposal[k]) > 600
           for k in ('target', 'objective', 'reason', 'report_prefix')):
        return False
    if not isinstance(proposal['memory_ids'], list) or len(proposal['memory_ids']) > 3:
        return False
    allowed_memories = {m['id'] for m in o['memories']}
    if any(not isinstance(mid, str) or mid not in allowed_memories for mid in proposal['memory_ids']):
        return False
    graph = {z['id']: z['adjacent'] for z in o['map']}
    if proposal.get('objective') not in graph:
        return False
    start = o['unit']['zone']
    target = proposal.get('target')
    if target not in {start, *graph[start]}:
        return False
    if proposal['action'] in ('HOLD', 'OBSERVE', 'REST') and target != start:
        return False
    valid_ids = {x['id'] for x in o['orders']}
    return proposal.get('order_id') is None or proposal['order_id'] in valid_ids
