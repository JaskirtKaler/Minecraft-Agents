"""Check actions against frozen spatial outcomes; never select a build plan.

Ownership is deliberately absent here. Only the world executor can authorize
repair from its verified, session-local placement ledger.
"""
from agent.regions import compile_goal, position_key
from agent.action_effects import observed_tool_effect


def verified_final_cell_change(goal, trace):
    """Proof of a real change to this final cell, even if its baseline matches.

    An owned mistake can be placed then removed during one objective. Its
    final air equals the original baseline, but a verified removal still
    proves a change. Neither skipped rows nor global success suffice.
    """
    p = goal['position']
    for entry in trace:
        action, result = entry.get('action', {}), entry.get('result', {})
        if not isinstance(action, dict) or not isinstance(result, dict):
            continue
        data, name, args = result.get('data', {}), action.get('name'), action.get('args', {})
        if not isinstance(data, dict) or not isinstance(args, dict):
            continue
        proof_action = action
        if name == 'place':
            before, after = data.get('inventory_before'), data.get('inventory_after')
            if (result.get('success') is True and result.get('verified') is True
                    and args.get('position') == p and data.get('position') == p and data.get('name') == goal['block']
                    and data.get('already_correct') is not True and data.get('inventory_delta', -1) == -1
                    and type(before) is int and type(after) is int and before > 0 and after >= 0 and before - after == 1):
                return True
            # Structural single-place shares the backend batch executor. This
            # normalization is proof inspection only, never an issued action.
            proof_action = {'name': 'place_batch', 'args': {'positions': [args.get('position')]}}
        if not observed_tool_effect(proof_action, result):
            continue
        if name == 'dig' and data.get('position') == p:
            if data.get('replacement', data.get('name')) == goal['block']:
                return True
        proof_name = proof_action.get('name')
        if proof_name in {'place_batch', 'dig_batch', 'repair_batch'}:
            proof = data.get('partial', data)
            row_key = 'placements' if proof_name == 'place_batch' else 'cleared'
            for row in proof.get(row_key, []):
                if (isinstance(row, dict) and row.get('verified') is True and row.get('position') == p
                        and row.get('after') == goal['block'] and row.get('before') != row.get('after')
                        and observed_tool_effect(proof_action, {'success': False, 'data': {'partial': {row_key: [row]}}})):
                    return True
    return False


def spatial_cells(goals):
    cells = {}
    for goal in goals:
        if goal['kind'] == 'regions_match':
            targets = compile_goal(goal)
        elif goal['kind'] == 'block_is':
            p = goal['position']
            targets = {(p['x'], p['y'], p['z']): goal['block']}
        else:
            continue
        for p, block in targets.items():
            if p in cells and cells[p] != block:
                raise ValueError('Spatial goals require conflicting final blocks at ' + position_key(p))
            cells[p] = block
    if len(cells) > 4096:
        raise ValueError('One construction contract supports at most 4096 distinct final cells.')
    return cells


def construction_contract(goals, world, task_id):
    return {'task_id': task_id, 'world': dict(world),
            'desired_cells': {position_key(p): block for p, block in spatial_cells(goals).items()}}


def action_conflict(action, goals, evidence):
    """Reject a contradictory batch, without rewriting/filtering its positions.

    Single placement can involve seed→crop mappings or a prerequisite station;
    that registry-grounded check belongs to the executor. Digging a currently
    correct final solid is always contradictory. Fresh backend checks repeat
    these constraints immediately before mutation.
    """
    if not any(g['kind'] == 'regions_match' for g in goals):
        return None
    name, args = action.get('name'), action.get('args', {})
    if name not in {'place_batch', 'dig', 'dig_batch'}:
        return None
    desired = spatial_cells(goals)
    positions = args.get('positions') if name.endswith('_batch') else [args.get('position')]
    if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
        return None  # Ordinary argument validation owns malformed input.
    observed = {}
    for entry in evidence:
        goal = entry['goal']
        if goal['kind'] == 'regions_match' and isinstance(entry.get('observed'), dict):
            observed.update(entry['observed'])
        elif goal['kind'] == 'block_is':
            p = goal['position']
            observed[position_key((p['x'], p['y'], p['z']))] = entry.get('observed')
    for p in positions:
        if not isinstance(p, dict) or not all(type(p.get(a)) is int for a in ('x', 'y', 'z')):
            continue
        key = (p['x'], p['y'], p['z'])
        final = desired.get(key)
        if name == 'place_batch' and (final is None or final != args.get('item')):
            return {'position': dict(p), 'desired': final, 'proposed': args.get('item'),
                    'reason': 'Permanent batch placement must match a covered final cell, including required air.'}
        if name in {'dig', 'dig_batch'} and final not in {None, 'air', 'cave_air', 'void_air'}:
            current = observed.get(position_key(key))
            if isinstance(current, dict):
                current = current.get('name')
            if current == final:
                return {'position': dict(p), 'desired': final, 'observed': current,
                        'reason': 'Retain already-correct final construction; it is not an obstruction.'}
    return None


def rejected_action(conflict):
    return {'success': False, 'verified': False, 'status': 'failed',
            'message': 'Action contradicts the accepted construction goal; no cells were changed.',
            'data': {'error_code': 'CONSTRUCTION_GOAL_CONFLICT', **conflict}}
