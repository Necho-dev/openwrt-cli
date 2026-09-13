from __future__ import annotations

import re

from rich.text import Text

KEY_STYLE = "bold #7ec8ff"
LABEL_STYLE = "#8aa4b8"
SEP_STYLE = "#5d7a94"

_SPLIT = re.compile(r"( · |\n| {2,})")
_LEAD_KEY = re.compile(
    r"^(?P<key>"
    r"Ctrl\+[A-Za-z]"
    r"|Enter\s*/\s*[ynYN]"
    r"|Esc\s*/\s*[ynYN]"
    r"|\[\s*\]"
    r"|↑↓"
    r"|Delete|Del|Backspace|Enter|Esc|回车"
    r"|[a-zA-Z0-9/?]"
    r")(?P<rest>(?:\s+.*|[\u4e00-\u9fff].*))?$",
    re.IGNORECASE,
)


def highlight_keys(text: str) -> Text:
    """Bold-accent shortcut tokens; keep labels muted like the Footer."""
    out = Text()
    for part in _SPLIT.split(text or ""):
        if not part:
            continue
        if _SPLIT.fullmatch(part):
            out.append(part, style=SEP_STYLE)
            continue
        lead_len = len(part) - len(part.lstrip())
        if lead_len:
            out.append(part[:lead_len], style=SEP_STYLE)
        body = part[lead_len:]
        match = _LEAD_KEY.match(body)
        if not match:
            out.append(body, style=LABEL_STYLE)
            continue
        out.append(match.group("key"), style=KEY_STYLE)
        rest = match.group("rest") or ""
        if rest:
            out.append(rest, style=LABEL_STYLE)
    return out
