"""Display title and radio name rules, shared by web, meshbot and lcd."""

DEFAULT_TITLE = "Mesh Stack"
TITLE_MAX = 24
RADIO_NAME_MAX = 31     # bytes; the radio stores 32 with a terminator


def _printable(s: str) -> bool:
    return all(c.isprintable() for c in s)


def check_title(s: str):
    """Error message, or None if `s` is a usable header title (blank means 'use the default')."""
    s = (s or "").strip()
    if len(s) > TITLE_MAX:
        return f"Title is at most {TITLE_MAX} characters"
    if not _printable(s):
        return "Title has a control character"
    return None


def resolve_title(configured, env) -> str:
    """Config tab value, else SMS_TITLE, else the default; invalid values are skipped."""
    for v in (configured, env):
        v = (v or "").strip()
        if v and check_title(v) is None:
            return v
    return DEFAULT_TITLE


def check_radio_name(s: str):
    s = (s or "").strip()
    if not s:
        return "Radio name can't be empty"
    if len(s.encode("utf-8")) > RADIO_NAME_MAX:
        return f"Radio name is at most {RADIO_NAME_MAX} bytes"
    if not _printable(s):
        return "Radio name has a control character"
    return None
