"""Check actions against frozen spatial outcomes; never select a build plan.

Ownership is deliberately absent here. Only the world executor can authorize
repair from its verified, session-local placement ledger.
"""
from agent.regions import compile_goal, position_key
from agent.action_effects import observed_tool_effect
from agent.construction_knowledge import construction_knowledge, current_spatial_observations


_AIR = frozenset({'air', 'cave_air', 'void_air'})


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
    """Reject contradictory or known no-op work without selecting an action.

    Single placement can involve seed→crop mappings or a prerequisite station;
    that registry-grounded check belongs to the executor. A same-name current
    block, or a currently correct solid final cell, already satisfies placement
    geometry without inferring an item→block mapping. Digging a currently
    correct final solid is contradictory, and digging known air cannot help.
    One bad/no-op cell rejects the WHOLE batch; no filtering/reordering occurs.
    Unknown observations defer to fresh backend checks before mutation.
    """
    if not any(g['kind'] in {'regions_match', 'block_is'} for g in goals):
        return None
    if not isinstance(action, dict) or not isinstance(action.get('args', {}), dict):
        return None
    name, args = action.get('name'), action.get('args', {})
    if name not in {'place', 'place_batch', 'dig', 'dig_batch'}:
        return None
    desired = spatial_cells(goals)
    positions = args.get('positions') if name.endswith('_batch') else [args.get('position')]
    if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
        return None  # Ordinary argument validation owns malformed input.
    if any(not isinstance(p, dict) or not all(type(p.get(a)) is int for a in ('x', 'y', 'z')) for p in positions):
        return None  # Ordinary argument validation owns malformed input.
    observed = current_spatial_observations(goals, evidence)
    # Preserve contradiction precedence: a mixed batch containing both an
    # already-correct cell and a wrong permanent target is still contradictory.
    for p in positions:
        key = (p['x'], p['y'], p['z'])
        final = desired.get(key)
        if name == 'place_batch' and (final is None or final != args.get('item')):
            return {'position': dict(p), 'desired': final, 'proposed': args.get('item'),
                    'reason': 'Permanent batch placement must match a covered final cell, including required air.'}
        if name in {'dig', 'dig_batch'} and final is not None and final not in _AIR:
            current = observed.get(key, 'unknown')
            if current == final:
                return {'position': dict(p), 'desired': final, 'observed': current,
                        'reason': 'Retain already-correct final construction; it is not an obstruction.'}
    for p in positions:
        key = (p['x'], p['y'], p['z'])
        final, current = desired.get(key), observed.get(key, 'unknown')
        if current == 'unknown':
            continue
        if name in {'place', 'place_batch'}:
            same_item = isinstance(args.get('item'), str) and args['item'] not in _AIR and current == args['item']
            correct_solid = final is not None and final not in _AIR and current == final
            if not same_item and not correct_solid:
                continue
            reason = (('This exact cell already contains the proposed same-name block; placement has no observed effect. '
                       if same_item else 'This exact cell already matches its accepted final solid block; retain the correct cell. ')
                      + 'Choose genuinely missing coordinates from current construction facts.')
        elif current in _AIR:
            reason = ('This exact cell is observed air; digging air cannot clear an obstruction. '
                      'Choose genuinely occupied required-air cells or missing placement cells from current facts.')
        else:
            continue
        packet = construction_knowledge(goals, evidence, {}, max_samples=8)
        remaining = {category: {'count': facts['count'], 'samples': facts['samples'],
                               'omitted_sample_count': facts['omitted_sample_count']}
                     for category, facts in packet['categories'].items()
                     if not category.startswith('matched') and facts['count']}
        return {'error_code': 'CONSTRUCTION_NO_OP', 'position': dict(p), 'desired': final,
                'observed': current, 'proposed': args.get('item') if name in {'place', 'place_batch'} else 'dig',
                'no_op_kind': ('placement_already_present' if name in {'place', 'place_batch'} else 'digging_empty_cell'),
                'known_no_op': True, 'whole_batch_rejected': True,
                'reason': reason, 'remaining_facts': {'progress': packet['progress_facts'], 'categories': remaining},
                'next_decision': 'Choose your own new action from remaining observed cells; no replacement action or target was issued.'}
    return None


def rejected_action(conflict):
    no_op = conflict.get('error_code') == 'CONSTRUCTION_NO_OP'
    return {'success': False, 'verified': False, 'status': 'failed',
            'message': ('Known no-op construction action rejected as a whole; no cells were changed.' if no_op else
                        'Action contradicts the accepted construction goal; no cells were changed.'),
            'data': {'error_code': 'CONSTRUCTION_GOAL_CONFLICT', **conflict}}
