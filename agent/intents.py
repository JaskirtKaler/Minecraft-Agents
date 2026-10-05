"""Small, conservative parser for deterministic Minecraft resource tasks.

This intentionally recognizes only requests whose success criteria we can verify
without asking an LLM to invent Mineflayer API calls.  Everything more complex
continues through the planner until it has a typed tool plan of its own.
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
COMPLEX_VERB = re.compile(r"\b(?:craft|make|build|place|smelt)\b", re.IGNORECASE)
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


def _recipient(text: str, requester: Optional[str]) -> Optional[str]:
    match = RECIPIENT.search(text)
    if not match:
        return None
    matched_recipient = match.group(0).rsplit(" ", 1)[-1].lower()
    return requester if matched_recipient == "me" else match.group(1)


def parse_resource_task(message: str, requester: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse one safe mine/give request into the Node skill-runner contract.

    A request that combines this work with crafting, building, or placement is
    deliberately not partially executed: it needs a complete typed plan instead.
    """
    text = " ".join(message.strip().split())
    if not text:
        return None

    is_mining = bool(MINE_VERB.search(text))
    is_delivery = bool(DELIVERY_VERB.search(text))
    if not is_mining and not is_delivery:
        return None

    if COMPLEX_VERB.search(text):
        return None

    item = _wood_item(text)
    count = _count(text)
    if not item or not count:
        return None

    recipient = _recipient(text, requester)
    args: Dict[str, Any] = {"item": item, "count": count, "max_distance": 48}

    if is_mining and is_delivery:
        if not recipient:
            return None
        args["recipient"] = recipient
        return {"name": "mine_and_give", "args": args}
    if is_mining:
        return {"name": "mine_logs", "args": args}
    if is_delivery:
        if not recipient:
            return None
        args["recipient"] = recipient
        return {"name": "give_item", "args": args}
    return None


def is_explicit_task_request(message: str, bot_username: str) -> bool:
    """Gate raw planner execution so ordinary chat cannot execute arbitrary code."""
    text = message.lower()
    name = bot_username.lower()
    physical_verbs = re.compile(
        r"\b(?:mine|gather|collect|chop|get|harvest|craft|make|build|place|smelt|drop|give|bring|toss|hand)\b"
    )
    return bool(physical_verbs.search(text) or f"@{name}" in text or name in text)
