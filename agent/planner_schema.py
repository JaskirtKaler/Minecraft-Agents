"""Output contracts, not Minecraft task plans. The model selects all actions."""


def object_schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


IDENTIFIER = {"type": "string", "minLength": 1}
COUNT = {"type": "integer", "minimum": 1, "maximum": 2304}
POSITION = object_schema({axis: {"type": "integer"} for axis in ("x", "y", "z")}, ("x", "y", "z"))
TOOL_COUNT = {"type": "integer", "minimum": 1, "maximum": 64}
REGION = object_schema({"min": POSITION, "max": POSITION, "block": IDENTIFIER,
                        "mode": {"enum": ["solid", "perimeter_xz"]}},
                       ("min", "max", "block", "mode"))
REGION_EXCEPTION = object_schema({"position": POSITION, "block": IDENTIFIER}, ("position", "block"))


def tool_schema(tool_names):
    """Match documented tool arguments, without selecting recipes or plans."""
    variants = []
    for name in sorted(tool_names):
        fields, required = {}, []
        if name == 'inspect':
            fields = {'item': IDENTIFIER, 'position': POSITION}
        elif name == 'inspect_region':
            fields = {'min': POSITION, 'max': POSITION}
            required = ['min', 'max']
        elif name in {'place_batch', 'dig_batch', 'repair_batch'}:
            fields = {'positions': {'type': 'array', 'items': POSITION, 'minItems': 1, 'maxItems': 64}}
            required = ['positions']
            if name == 'place_batch':
                fields['item'] = IDENTIFIER
                required.append('item')
            else:
                fields['tool'] = IDENTIFIER
        elif name == 'find_blocks':
            fields = {'names': {'type': 'array', 'items': IDENTIFIER, 'minItems': 1, 'maxItems': 16},
                      'radius': {'type': 'integer', 'minimum': 1, 'maximum': 64}}
            required = ['names']
        elif name == 'craft_budget':
            step = object_schema({'item': IDENTIFIER, 'count': TOOL_COUNT,
                                  'recipe_index': {'type': 'integer', 'minimum': 0}}, ('item', 'count'))
            fields = {'steps': {'type': 'array', 'items': step, 'minItems': 1, 'maxItems': 16}}
            required = ['steps']
        elif name == 'planting_sites':
            fields = {'item': IDENTIFIER, 'radius': {'type': 'integer', 'minimum': 1, 'maximum': 32}}
            required = ['item']
        elif name == 'escape_staircase':
            fields = {'rise': {'type': 'integer', 'minimum': 1, 'maximum': 8}}
            required = ['rise']
        else:
            if name in {'walk_to', 'dig', 'place', 'use_on_block', 'container', 'deposit', 'withdraw'}:
                fields['position'] = POSITION
                required.append('position')
            if name in {'recipes', 'equip', 'craft', 'place', 'use_on_block', 'deposit', 'withdraw',
                        'mine_logs', 'mine_resource', 'give_item', 'deposit_item'}:
                fields['item'] = IDENTIFIER
                required.append('item')
            if name in {'craft', 'deposit', 'withdraw', 'mine_logs', 'mine_resource', 'give_item', 'deposit_item'}:
                fields['count'] = TOOL_COUNT
                required.append('count')
            if name == 'recipes':
                fields['count'] = TOOL_COUNT
            if name == 'craft':
                fields['table'] = POSITION
            if name == 'walk_to':
                fields['adjacent'] = {'type': 'boolean'}
            if name == 'dig':
                fields.update(tool=IDENTIFIER, collect_item=IDENTIFIER)
            if name == 'give_item':
                fields['recipient'] = IDENTIFIER
                required.append('recipient')
            if name in {'mine_logs', 'mine_resource', 'deposit_item'}:
                fields['max_distance'] = {'type': 'integer', 'minimum': 1, 'maximum': 64}
            if name in {'mine_logs', 'mine_resource'}:
                fields['collection_mode'] = {'enum': ['ensure_inventory', 'additional']}
        args = object_schema(fields, required)
        if name == 'inspect':
            args = {'oneOf': [object_schema({'item': IDENTIFIER}, ('item',)),
                              object_schema({'position': POSITION}, ('position',))]}
        variants.append(object_schema({'name': {'const': name}, 'args': args}, ('name', 'args')))
    return {'oneOf': variants}


def decision_schema(tool_names, batch_size=4, include_goals=True):
    goals = []
    for kind in ("inventory_gain", "inventory_at_least", "container_gain", "container_loss", "item_dropped"):
        fields = {"kind": {"const": kind}, "item": IDENTIFIER, "count": COUNT}
        if kind in {"container_gain", "container_loss"}:
            fields["position"] = POSITION
        if kind == "item_dropped":
            fields["recipient"] = IDENTIFIER
        required = list(fields)
        if kind == 'inventory_gain':
            fields['comparison'] = {'enum': ['exact', 'at_least']}
        goals.append(object_schema(fields, required))
    goals += [object_schema({"kind": {"const": "block_is"}, "block": IDENTIFIER, "position": POSITION,
                            "must_change": {'type': 'boolean'}},
                            ("kind", "block", "position")),
              object_schema({"kind": {"const": "regions_match"},
                             "regions": {"type": "array", "items": REGION, "minItems": 1, "maxItems": 16},
                             "exceptions": {"type": "array", "items": REGION_EXCEPTION, "maxItems": 64},
                             "must_change": {"type": "boolean"}},
                            ("kind", "regions", "must_change")),
              object_schema({"kind": {"const": "position_near"}, "position": POSITION,
                             "radius": {"type": "number", "exclusiveMinimum": 0, "maximum": 8}},
                            ("kind", "position"))]
    action = tool_schema(tool_names)
    properties = {
        "type": {"const": "act"},
        "goals": {"type": "array", "items": {"oneOf": goals}, "minItems": 1, "maxItems": 16},
        "reason": {"type": "string", "maxLength": 240},
        "actions": {"type": "array", "items": action, "minItems": 1, "maxItems": batch_size},
    }
    if not include_goals:
        # Frozen goals are supplied by the controller, not regenerated every
        # response. This eliminates both wasted tokens and accidental rewrites.
        del properties['goals']
    # Full union branches, not a conditional alongside root properties:
    # Ollama's grammar conversion does not enforce every conditional sibling.
    branches = [object_schema(properties, ('type', 'actions'))]
    for kind in ('answer', 'blocked'):
        branches.append(object_schema({'type': {'const': kind}, 'message': {'type': 'string', 'maxLength': 600}},
                                      ('type', 'message')))
    done = {'type': {'const': 'done'}}
    if include_goals:
        done['goals'] = properties['goals']
    branches.append(object_schema(done, ('type',)))
    return {'oneOf': branches}


LESSON_SCHEMA = object_schema({"lesson": {"type": "string", "maxLength": 1500}}, ("lesson",))
CURRICULUM_SCHEMA = object_schema({"objective": {"type": "string", "maxLength": 600},
                                   "reason": {"type": "string", "maxLength": 240}}, ("objective", "reason"))
GOAL_REVIEW_SCHEMA = object_schema({'reason': {'type': 'string', 'minLength': 12, 'maxLength': 600},
    'missing_outcomes': {'type': 'array', 'items': {'type': 'string', 'maxLength': 240}, 'maxItems': 8},
    'covers_request': {'type': 'boolean'}},
    ('reason', 'missing_outcomes', 'covers_request'))
