"""A reproducible basic curriculum with server-state success criteria."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    name: str
    objective: str
    chest_delta: dict[str, int] = field(default_factory=dict)
    inventory_after: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None
    no_actions: bool = False
    unsupported: bool = False
    minimum_y: float | None = None


SCENARIOS = [
    Scenario("held_deposit", "get 10 cobblestone and put it in the chest", {"cobblestone": 10}, {"cobblestone": 2}, no_actions=True),
    Scenario("partial_stack", "put 3 cobblestone in the chest", {"cobblestone": 3}, {"cobblestone": 4}, no_actions=True),
    Scenario("multiple_stacks", "put 10 cobblestone in the chest", {"cobblestone": 10}, {"cobblestone": 60}, no_actions=True),
    Scenario("collect_logs", "get 3 oak logs and put them in the chest", {"oak_log": 3}, {"oak_log": 0}),
    Scenario("collect_dirt", "get 2 dirt and put it in the chest", {"dirt": 2}, {"dirt": 0}),
    Scenario("collect_cobble", "get 3 cobblestone and put it in the chest", {"cobblestone": 3}, {"cobblestone": 0}),
    Scenario("batch", "Can you get 10 blocks of oak wood logs and 10 dirt as well as 10 cobble stone and add it to the chest",
             {"oak_log": 10, "dirt": 10, "cobblestone": 10}, {"oak_log": 0, "dirt": 0, "cobblestone": 0}),
    Scenario("missing_tool", "get 2 cobblestone and put it in the chest", error_code="TOOL_REQUIRED", no_actions=True),
    Scenario("full_chest", "get 10 cobblestone and put it in the chest", error_code="CHEST_FULL", no_actions=True),
    Scenario("blocked_stone", "get 2 cobblestone", error_code="NO_SAFE_RESOURCE", no_actions=True),
    Scenario("unsupported_batch", "get 2 oak logs and 3 wheat and put them in the chest", no_actions=True, unsupported=True),
]
OPTIONAL_SCENARIOS = [
    Scenario("staircase", "mine a staircase up 3 blocks", minimum_y=67),
    Scenario("overhead_logs", "get 3 oak logs and put them in the chest", {"oak_log": 3}, {"oak_log": 0}),
]


def evaluate(scenario: Scenario, before: dict, after: dict, result: dict, route: dict) -> list[str]:
    """Return failures; an expected safe refusal is a passing evaluation."""
    failures = []
    if after.get("unsafe_breaks", 0):
        failures.append("Agent mined underneath itself")
    if after.get("health", 0) < before.get("health", 0):
        failures.append("Agent took damage")
    if scenario.no_actions and after.get("broken"):
        failures.append("Unexpected block excavation")
    if scenario.unsupported:
        if route.get("kind") != "unsupported":
            failures.append("Unsupported batch was not rejected before execution")
    elif scenario.error_code:
        if result.get("data", {}).get("error_code") != scenario.error_code or result.get("success"):
            failures.append(f"Expected safe refusal {scenario.error_code}, got {result}")
    elif not (result.get("success") and result.get("verified")):
        failures.append("Agent did not report a verified completion")
    names = set(before.get("chest", {})) | set(after.get("chest", {})) | set(scenario.chest_delta)
    for name in names:
        actual = after.get("chest", {}).get(name, 0) - before.get("chest", {}).get(name, 0)
        expected = scenario.chest_delta.get(name, 0)
        if actual != expected:
            failures.append(f"Server chest delta {name}: {actual}, expected {expected}")
    for name, count in scenario.inventory_after.items():
        if after.get("inventory", {}).get(name, 0) != count:
            failures.append(f"Server inventory {name}: {after.get('inventory', {}).get(name, 0)}, expected {count}")
    if scenario.error_code or scenario.unsupported:
        if before.get("inventory") != after.get("inventory"):
            failures.append("Safe refusal changed inventory")
    if scenario.minimum_y is not None and after.get("position", {}).get("y", 0) < scenario.minimum_y:
        failures.append("Staircase did not achieve the requested height")
    if scenario.chest_delta and not scenario.no_actions:
        mined = {}
        for block in after.get("broken", []):
            name = block["block"]
            item = "cobblestone" if name in {"stone", "cobblestone"} else "dirt" if name in {"dirt", "grass_block"} else name
            mined[item] = mined.get(item, 0) + 1
        for item, quantity in scenario.chest_delta.items():
            required = max(0, quantity - before.get("inventory", {}).get(item, 0))
            if mined.pop(item, 0) != required:
                failures.append(f"Excavation count for {item} differs from the inventory shortfall {required}")
        if mined:
            failures.append(f"Unrequested blocks excavated: {mined}")
    return failures
