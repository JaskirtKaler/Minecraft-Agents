"""Conservative chat routing: questions/corrections are never physical orders."""
import re
from agent.intents import (
    parse_resource_task, parse_resource_plan, PlanError, resource_item, MINE_VERB, DELIVERY_VERB,
    DEPOSIT_VERB, COMPLEX_VERB, _count,
)


def classify_message(message, requester=None):
    text = " ".join(message.strip().split())
    text = re.sub(r"^(?:(?:hi|hey|hello)\s+)?(?:jarvis|@?ai_agent)[,:!]?\s+", "", text, flags=re.IGNORECASE)
    lower = text.lower().rstrip("?.!")
    if re.fullmatch(r"(?:please\s+)?(?:stop|cancel|abort)(?:\s+(?:the\s+)?(?:task|job|mining))?", lower):
        return {"kind": "cancel"}
    if lower in {"inventory", "show inventory", "show your inventory", "show me your inventory", "what are you carrying", "what's in your inventory", "what is in your inventory"}:
        return {"kind": "inventory"}
    if lower in {"memory", "memory status", "what do you remember"}:
        return {"kind": "memory", "status": lower == "memory status"}
    if (lower in {"status", "progress"} or re.search(
        r"\b(?:is something wrong|are you stuck|what are you doing|job is not finished|task is not finished|are you done)\b", lower
    )):
        return {"kind": "status"}
    if re.search(r"\b(?:don't|do not|never)\b", lower):
        return {"kind": "conversation", "message": "Understood; I haven't started that action. Give me a separate clear task when ready."}
    if lower in {"thanks", "thank you", "thanks jarvis", "thank you jarvis"} or re.fullmatch(
        r"i (?:verified|confirmed|checked) (?:the )?(?:deposit|delivery)(?:[,; ]+(?:thanks|thank you))?", lower
    ):
        return {"kind": "conversation", "message": "Thanks for checking. Your report doesn't overwrite the automatic verification record."}
    correction = re.match(r"when you mine (.+?) (?:it )?(?:becomes|drops|gives|turns into) (.+)", lower)
    if correction:
        return {"kind": "knowledge", "subject": correction.group(1), "correction": True}
    question = re.match(
        r"(?:what is|what are|tell me about|what do you know about|how (?:do|can) (?:i|you) (?:get|mine|craft|grow|farm))\s+(.+)", lower
    )
    if question:
        subject = re.sub(r"^(?:a|an|the|some)\s+", "", question.group(1))
        return {"kind": "knowledge", "subject": subject, "correction": False}
    try:
        task = parse_resource_plan(text, requester) or parse_resource_task(text, requester)
    except PlanError as exc:
        return {"kind": "unsupported", "message": str(exc)}
    if task:
        return {"kind": "task", "task": task}
    item = resource_item(text)
    action = MINE_VERB.search(text) or DELIVERY_VERB.search(text) or DEPOSIT_VERB.search(text)
    if item and action and not COMPLEX_VERB.search(text):
        if not _count(text):
            return {"kind": "clarify", "field": "count", "template": text,
                    "message": f"How many {item} (1–64)? Reply with a number; I haven't started yet."}
        return {"kind": "clarify", "field": "recipient", "template": text,
                "message": "Should I drop them to you or put them in a chest? Reply 'to me' or 'in the chest'."}
    if action or COMPLEX_VERB.search(text) or re.search(r"\b(?:farm|plant|harvest|staircase|escape)\b", lower):
        return {"kind": "unsupported", "message": (
            "I can collect logs/dirt/cobblestone, execute resource batches, deliver items, and make a controlled staircase out. "
            "Farming/building aren't verified skills yet; I won't improvise unsafe actions."
        )}
    return {"kind": "conversation", "message": "I'm here. Ask 'status', 'memory', or 'what is stone?', or give me a resource task."}


def quantity_reply(message):
    match = re.fullmatch(r"\s*(\d{1,3})(?:\s+(?:blocks|logs|please))?[.!]?\s*", message, re.IGNORECASE)
    return int(match.group(1)) if match and 1 <= int(match.group(1)) <= 64 else None


def inventory_reply(state):
    """A bounded, read-only rendering of a fresh bridge snapshot, not memory."""
    if state.get("ready") is not True:
        return ["Inventory is unavailable: Jarvis is not fully spawned. This is not an empty-inventory report."]
    heading = f"{state.get('username') or 'Jarvis'} inventory (live, read-only; {state.get('observedAt') or 'timestamp unavailable'})."
    lines = [heading[:220]]
    entries = [f"{item['name']} x{item['count']} [slot {item.get('slot', '?')}]"
               for item in state.get("inventory", []) if item.get("name") and item.get("count", 0) > 0]
    line = ""
    for entry in entries:
        if line and len(line) + len(entry) + 2 > 220:
            lines.append(line)
            line = ""
        line += (", " if line else "") + entry
    lines.append(line or "No carried items in this snapshot.")
    equipment = state.get("equipment") or {}
    held = equipment.get("held") or {}
    lines.append(f"Held: {held.get('name', 'none')} (already counted above). Armor: " +
                 ", ".join(f"{slot}={equipment.get(slot) or 'none'}" for slot in ("head", "torso", "legs", "feet")) +
                 f"; offhand={equipment.get('offhand') or 'none'}.")
    # All carried slots fit in a handful of lines; equipment can be long if named.
    return [chunk[offset:offset + 220] for chunk in lines for offset in range(0, len(chunk), 220)]


def knowledge_reply(facts):
    if not facts.get("found"):
        return f"I couldn't find '{facts.get('name', '')}' in this world's Minecraft registry."
    name = facts["name"]
    drops = []
    for drop in facts.get("dropCandidates", []):
        condition = " with Silk Touch" if drop.get("silkTouch") else " without Silk Touch" if drop.get("noSilkTouch") else ""
        if "blockAge" in drop:
            condition += f" at crop age {drop['blockAge']}"
        drops.append(f"{drop['item']}{condition}")
    parts = [f"{name} (Minecraft {facts['version']})."]
    if drops:
        parts.append("Possible drops: " + ", ".join(drops) + ".")
    if facts.get("requiredTools"):
        parts.append("Requires a suitable " + ("pickaxe" if all(t.endswith("_pickaxe") for t in facts["requiredTools"]) else "harvesting tool") + ".")
    if facts.get("sources"):
        parts.append("Sources without Silk Touch: " + ", ".join(facts["sources"][:4]) + ".")
    if facts.get("recipes"):
        recipe = facts["recipes"][0]
        parts.append("One recipe: " + ", ".join(f"{count} {item}" for item, count in recipe["ingredients"].items()) + ".")
    return " ".join(parts)
