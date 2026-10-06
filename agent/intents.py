"""Small, conservative parser for deterministic Minecraft resource tasks.

This intentionally recognizes only requests whose success criteria we can verify
without asking an LLM to invent Mineflayer API calls. More complex chat requests
are reported as unsupported until they have a tested, typed tool plan.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


WOOD_ITEMS = {
    "oak": "oak_log",
    "birch": "birch_log",
    "spruce": "spruce_log",
    "jungle": "jungle_log",
    "acacia": "acacia_log",
    "dark oak": "dark_oak_log",
    "mangrove": "mangrove_log",
    "cherry": "cherry_log",
}

MINE_VERB = re.compile(r"\b(?:mine|gather|collect|chop|get|harvest)\b", re.IGNORECASE)
DELIVERY_VERB = re.compile(r"\b(?:drop|give|bring|toss|hand)\b", re.IGNORECASE)
DEPOSIT_VERB = re.compile(r"\b(?:deposit|put|store|add)\b", re.IGNORECASE)
CHEST_DESTINATION = re.compile(r"\b(?:in|into|to)\s+(?:(?:the|a|my|your|nearby|this|that)\s+)*chest\b", re.IGNORECASE)
COMPLEX_VERB = re.compile(r"\b(?:craft|make|build|place|smelt|farm|plant|fight|attack|kill)\b", re.IGNORECASE)
COUNT = re.compile(r"\b(\d{1,3})\b")
RECIPIENT = re.compile(r"\b(?:to|for)\s+(?:me|@?([A-Za-z0-9_]{1,16}))\b", re.IGNORECASE)


def _wood_item(text: str) -> Optional[str]:
    """Return a Minecraft log item only when the request explicitly names wood."""
    if not re.search(r"\b(?:logs?|longs?|wood)\b", text, re.IGNORECASE):
        return None

    normalized = re.sub(r"\s+", " ", text.lower())
    for species, item_name in WOOD_ITEMS.items():
        if re.search(rf"\b{re.escape(species)}\s+(?:logs?|longs?|wood)\b", normalized):
            return item_name
    return "oak_log"


def _count(text: str) -> Optional[int]:
    """Extract a bounded item count, avoiding accidental huge objectives."""
    match = COUNT.search(text)
    if not match:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 64 else None


def resource_item(text: str) -> Optional[str]:
    if re.search(r"\bcobble(?:\s*stone)?\b|\bcobblestone\b", text, re.IGNORECASE):
        return "cobblestone"
    if re.search(r"\bdirt\b", text, re.IGNORECASE):
        return "dirt"
    return _wood_item(text)


def _recipient(text: str, requester: Optional[str]) -> Optional[str]:
    # Infinitives such as "able to get" are not delivery destinations. Look
    # inside the delivery clause, not at the first arbitrary "to" in the chat.
    delivery = DELIVERY_VERB.search(text)
    clause = text[delivery.end():] if delivery else text
    for match in reversed(list(RECIPIENT.finditer(clause))):
        matched_recipient = match.group(0).rsplit(" ", 1)[-1].lower()
        if MINE_VERB.fullmatch(matched_recipient) or matched_recipient in {"the", "a", "my", "your", "it", "them"}:
            continue
        return requester if matched_recipient == "me" else match.group(1)
    return None


def parse_escape_task(text: str) -> Optional[Dict[str, Any]]:
    """Explicit excavation recovery, never an unbounded tunnelling command."""
    text = re.sub(r"^please\s+", "", text.strip(), flags=re.IGNORECASE).rstrip(".!?")
    text = re.sub(r"^(?:(?:can|could|would)\s+you(?:\s+please)?|are\s+you\s+able\s+to)\s+", "", text, flags=re.IGNORECASE)
    if re.fullmatch(r"(?:get|climb)\s+(?:(?:yourself|your way)\s+)?out(?:\s+of\s+(?:the|this)\s+(?:hole|pit))?", text, re.IGNORECASE) or re.fullmatch(
        r"(?:dig|mine)\s+(?:(?:your|my|a)\s+way|yourself)\s+out", text, re.IGNORECASE
    ):
        return {"name": "escape_staircase", "args": {"rise": 4}}
    match = re.fullmatch(r"(?:dig|mine)\s+(?:a\s+)?staircase\s+(?:up|out)(?:\s+(\d{1,2})\s+blocks?)?", text, re.IGNORECASE)
    if match:
        rise = int(match.group(1) or 4)
        if 1 <= rise <= 8:
            return {"name": "escape_staircase", "args": {"rise": rise}}
    return None


def parse_resource_task(message: str, requester: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse one safe mine/give request into the Node skill-runner contract.

    A request that combines this work with crafting, building, or placement is
    deliberately not partially executed: it needs a complete typed plan instead.
    """
    text = " ".join(message.strip().split())
    if not text:
        return None
    escape = parse_escape_task(text)
    if escape:
        return escape
    # Never reduce a batch to its first number / preferred resource. Batches
    # must be validated in full by parse_resource_plan before anything executes.
    if len(list(re.finditer(r"\b\d+\b", text))) > 1:
        return None
    distinct = sum(bool(check) for check in (
        _wood_item(text), re.search(r"\bcobble(?:\s*stone)?\b|\bcobblestone\b", text, re.IGNORECASE),
        re.search(r"\bdirt\b", text, re.IGNORECASE)
    ))
    if distinct > 1:
        return None
    delivery = DELIVERY_VERB.search(text) or DEPOSIT_VERB.search(text)
    source_clause = text[:delivery.start()] if delivery else text
    source_clause = re.sub(r"\b(?:and|then)\s*$", "", source_clause, flags=re.IGNORECASE)
    if re.search(r"\b(?:and|as well as|plus)\b", source_clause, re.IGNORECASE):
        return None

    is_mining = bool(MINE_VERB.search(text))
    is_delivery = bool(DELIVERY_VERB.search(text) or DEPOSIT_VERB.search(text))
    if not is_mining and not is_delivery:
        return None

    if COMPLEX_VERB.search(text):
        return None

    item = resource_item(text)
    count = _count(text)
    if not item or not count:
        return None

    recipient = _recipient(text, requester)
    args: Dict[str, Any] = {"item": item, "count": count, "max_distance": 48}
    # Fetching is an inventory goal; explicit mining requests collect new items.
    if is_mining and re.search(r"\bget\b", text, re.IGNORECASE) and not re.search(
        r"\b(?:min(?:e|ing)|chop(?:ping)?|harvest(?:ing)?|gather(?:ing)?|collect(?:ing)?)\b", text, re.IGNORECASE
    ):
        args["collection_mode"] = "ensure_inventory"

    if is_delivery and CHEST_DESTINATION.search(text):
        return {"name": "mine_and_deposit" if is_mining else "deposit_item", "args": args}
    if is_mining and is_delivery:
        if not recipient:
            return None
        args["recipient"] = recipient
        return {"name": "mine_and_give", "args": args}
    if is_mining:
        return {"name": "mine_resource" if item in {"cobblestone", "dirt"} else "mine_logs", "args": args}
    if is_delivery:
        if not recipient:
            return None
        args["recipient"] = recipient
        return {"name": "give_item", "args": args}
    return None


class PlanError(ValueError):
    """A batch is recognized but cannot be safely interpreted in full."""


def parse_resource_plan(message: str, requester: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Bounded shared-destination batches, expressed only as validated skills.

    This is a baseline planner, not model training. Its JSON contract can also
    be produced by a local or Token Factory planner and validated by the bot.
    """
    text = " ".join(message.strip().split())
    numbers = list(re.finditer(r"\b\d+\b", text))
    if len(numbers) < 2 or not MINE_VERB.search(text[:numbers[0].start()]):
        return None
    if COMPLEX_VERB.search(text) or re.search(r"\b(?:farm|plant|smelt)\b", text, re.IGNORECASE):
        raise PlanError("This batch includes an unverified crafting/building/farming action. I haven't started any part of it.")
    species = "|".join(re.escape(name) for name in sorted(WOOD_ITEMS, key=len, reverse=True))
    item_pattern = re.compile(
        rf"\s*(?:blocks?\s+(?:of\s+)?)?(?P<item>cobble\s*stone|cobblestone|cobble|dirt|"
        rf"(?:{species})\s+(?:wood(?:\s+logs?)?|logs?)|logs?|wood)\b", re.IGNORECASE
    )
    separator = re.compile(r"[\s,]*(?:(?:and|as well as|also|plus|then|get|mine|gather|collect|chop|harvest)\b[\s,]*)*", re.IGNORECASE)
    goals = {}
    for index, number in enumerate(numbers):
        count = int(number.group())
        if not 1 <= count <= 64:
            raise PlanError("Each resource quantity must be 1–64. I haven't started any part of this batch.")
        end = numbers[index + 1].start() if index + 1 < len(numbers) else len(text)
        clause = text[number.end():end]
        match = item_pattern.match(clause)
        if not match:
            raise PlanError(f"I can't safely identify every resource in this batch (near '{clause[:60]}'). Nothing has started.")
        item = resource_item(match.group("item"))
        tail = clause[match.end():].strip()
        if index + 1 < len(numbers) and not separator.fullmatch(tail):
            raise PlanError("I support resource batches with one shared destination, not mixed instructions. Nothing has started.")
        goals[item] = goals.get(item, 0) + count
        if goals[item] > 64:
            raise PlanError("The combined quantity of one resource exceeds 64. Nothing has started.")
    destination = bool(CHEST_DESTINATION.search(tail))
    if destination:
        delivery = re.fullmatch(
            r"(?:and\s+|then\s+)?(?:deposit|put|store|add|drop|bring)\s+(?:it|them|all(?:\s+of\s+them)?|everything)?\s*"
            r"(?:in|into|to)\s+(?:(?:the|a|my|your|nearby|this|that)\s+)*chest(?:\s+please)?[.!?]?", tail, re.IGNORECASE
        )
        if not delivery:
            raise PlanError("Please give this batch one clear destination, e.g. 'and put them in the chest'. Nothing has started.")
    elif tail and not re.fullmatch(r"(?:please)?[.!?]?", tail, re.IGNORECASE):
        raise PlanError("I support collecting this batch or putting it in a chest. Nothing has started; please clarify the destination.")
    additional = bool(re.search(r"\b(?:mine|chop|harvest|gather|collect|mining)\b", text, re.IGNORECASE))
    steps = []
    for item, count in goals.items():
        args = {"item": item, "count": count, "max_distance": 48}
        if not additional:
            args["collection_mode"] = "ensure_inventory"
        steps.append({"name": "mine_logs" if item.endswith("_log") else "mine_resource", "args": args})
    if destination:
        steps.extend({"name": "deposit_item", "args": {"item": item, "count": count, "max_distance": 48}} for item, count in goals.items())
    return {"name": "execute_plan", "args": {"steps": steps}}


def is_explicit_task_request(message: str, bot_username: str) -> bool:
    """Gate raw planner execution so ordinary chat cannot execute arbitrary code."""
    text = message.lower()
    name = bot_username.lower()
    physical_verbs = re.compile(
        r"\b(?:mine|gather|collect|chop|get|harvest|craft|make|build|place|smelt|drop|give|bring|toss|hand)\b"
    )
    return bool(physical_verbs.search(text) or f"@{name}" in text or name in text)
