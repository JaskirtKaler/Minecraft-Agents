"""Route explicitly addressed human commands before any planner work starts."""

import re
from collections.abc import Collection

_ADDRESSED_COMMAND = re.compile(r"@([A-Za-z0-9_]{1,16})(?:\s+|:\s*)(.+)", re.DOTALL)


def addressed_command(sender: str, message: str, *, bot_username: str,
                      allowed_players: Collection[str]) -> str | None:
    """Return the command body only for this bot and an allowed human sender.

    Minecraft's offline protocol does not distinguish human and bot accounts.
    Explicit human names prevent even an addressed bot reply from triggering work.
    """
    if not isinstance(sender, str) or not isinstance(message, str):
        return None
    if sender.casefold() == bot_username.casefold():
        return None
    if sender.casefold() not in {name.casefold() for name in allowed_players}:
        return None
    match = _ADDRESSED_COMMAND.fullmatch(message.strip())
    if not match or match[1].casefold() != bot_username.casefold():
        return None
    return match[2].strip() or None
