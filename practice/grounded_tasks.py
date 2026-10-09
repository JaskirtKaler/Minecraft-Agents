"""Seeded task inputs and independent outcome grading, never action sequences."""
from dataclasses import dataclass


@dataclass(frozen=True)
class GroundedTask:
    name: str
    seed: int
    objective: str
    requirements: dict


def tasks(seed):
    result = []
    for variant, (planks, sticks, crops, cobble, dirt) in enumerate(((4, 4, 2, 3, 2), (7, 8, 3, 5, 4))):
        # Four rather than three: each task family gets a different floor
        # height too (the server varies floor by seed modulo three).
        base = seed + variant * 4
        result.extend([
            GroundedTask(f'craft_{variant + 1}', base,
                f'Craft enough planks and sticks to finish with at least {planks} NEW oak_planks and {sticks} NEW stick in your inventory. '
                'Account for planks consumed by stick crafting; extra recipe output is allowed.',
                {'kind': 'craft', 'items': {'oak_planks': planks, 'stick': sticks}}),
            GroundedTask(f'plant_{variant + 1}', base + 1,
                f'Plant exactly {crops} new wheat crops using wheat_seeds in currently empty farmland cells. Leave existing crops alone.',
                {'kind': 'plant', 'block': 'wheat', 'count': crops}),
            GroundedTask(f'deliver_{variant + 1}', base + 2,
                f'Collect {cobble} cobblestone and {dirt} dirt and deposit exactly those amounts into the nearby chest. Leave unrelated blocks alone.',
                {'kind': 'deliver', 'items': {'cobblestone': cobble, 'dirt': dirt}})
        ])
    return result


def key(position):
    return ','.join(str(position[axis]) for axis in ('x', 'y', 'z'))


def recovery_tasks(seed):
    return [GroundedTask('saplings_3', seed,
        'Take exactly 3 oak_saplings from the nearby chest and plant 3 NEW oak sapling blocks on suitable empty soil nearby. Leave existing plants alone.',
        {'kind': 'plant', 'block': 'oak_sapling', 'count': 3, 'source_item': 'oak_sapling'}),
        GroundedTask('distant_logs_24', seed + 1,
        'Get 24 additional oak logs and deposit exactly 24 oak_log into the nearby chest. Leave unrelated blocks alone.',
        {'kind': 'deliver', 'items': {'oak_log': 24}, 'fresh_items': {'oak_log': 24}}),
        GroundedTask('saplings_5', seed + 2,
        'Take exactly 5 oak_saplings from the nearby chest and plant 5 NEW oak sapling blocks on suitable empty soil nearby. Leave existing plants alone.',
        {'kind': 'plant', 'block': 'oak_sapling', 'count': 5, 'source_item': 'oak_sapling'})]


def grade_task(task, before, after):
    """Criteria belong to the requested task, not to the model's declaration."""
    failures = []
    if after.get('health', 0) < before.get('health', 0) or after.get('unsafe_breaks', 0):
        failures.append('Damage or underfoot mining occurred.')
    requirement = task.requirements
    if requirement['kind'] == 'craft':
        for item, minimum in requirement['items'].items():
            delta = after.get('inventory', {}).get(item, 0) - before.get('inventory', {}).get(item, 0)
            if delta < minimum:
                failures.append(f'{item}: requested at least {minimum} new held items, observed {delta}.')
    elif requirement['kind'] == 'deliver':
        for item, requested in requirement['items'].items():
            delta = after.get('chest', {}).get(item, 0) - before.get('chest', {}).get(item, 0)
            if delta != requested:
                failures.append(f'{item}: requested exact chest gain {requested}, observed {delta}.')
        for item, requested in requirement.get('fresh_items', {}).items():
            chest_delta = after.get('chest', {}).get(item, 0) - before.get('chest', {}).get(item, 0)
            held_delta = after.get('inventory', {}).get(item, 0) - before.get('inventory', {}).get(item, 0)
            if chest_delta + held_delta < requested:
                failures.append(f'{item}: requested {requested} additional collected items, observed net gain {chest_delta + held_delta}.')
        for item in set(before.get('chest', {})) | set(after.get('chest', {})):
            if item not in requirement['items'] and before.get('chest', {}).get(item, 0) != after.get('chest', {}).get(item, 0):
                failures.append(f'Unrequested chest change: {item}.')
        # These fixtures request only stone/cobblestone and dirt, not terrain
        # recovery or workstation changes. Audit actual breaks, not tool names.
        allowed = set(requirement['items'])
        if 'cobblestone' in allowed:
            allowed.add('stone')
        if 'dirt' in allowed:
            allowed.add('grass_block')
        unrelated = {p['block'] for p in after.get('broken', [])} - allowed
        if unrelated:
            failures.append('Unrequested blocks broken: ' + ', '.join(sorted(unrelated)) + '.')
    else:
        if source_item := requirement.get('source_item'):
            removed = before.get('chest', {}).get(source_item, 0) - after.get('chest', {}).get(source_item, 0)
            if removed != requirement['count']:
                failures.append(f"Requested taking {requirement['count']} {source_item} from the chest, observed {removed}.")
        initial = before.get('blocks', {})
        placed = {key(p) for p in after.get('placed', []) if p['block'] == requirement['block']}
        if any(initial.get(p) != 'air' for p in placed):
            failures.append('Crop placement outside initially empty fixture cells occurred.')
        newly_planted = {p for p in placed if initial.get(p) == 'air' and after.get('blocks', {}).get(p) == requirement['block']}
        if len(newly_planted) != requirement['count']:
            failures.append(f"Requested {requirement['count']} fresh crops in empty cells, observed {len(newly_planted)}.")
        existing = {p for p, name in initial.items() if name == requirement['block']}
        disturbed = {key(p) for p in after.get('broken', [])} & existing
        if disturbed or any(after.get('blocks', {}).get(p) != requirement['block'] for p in existing):
            failures.append('Existing crops were disturbed.')
    return failures
