"""MeshCore region (flood) scopes: presets and name checks, shared by meshbot, lcd and web.

Presets follow the Texas draft at comchan.net/region-scopes (Nov 2026, names may still
change). Any other locale goes in as a custom name.
"""
import re

PRESETS = [
    ("us-tx-aus", "Austin"),
    ("us-tx-sat", "San Antonio"),
    ("us-tx-hou", "Houston"),
    ("us-tx-dal", "Dallas"),
    ("us-tx-ftw", "Fort Worth"),
    ("us-tx-dfw", "Dallas-Fort Worth"),
    ("us-tx-central", "Central Texas"),
    ("us-tx-north", "North Texas"),
    ("us-tx-east", "East Texas"),
    ("us-tx-south", "South Texas"),
    ("us-tx-west", "West Texas"),
    ("us-tx", "Texas"),
    ("us", "United States"),
]

MAX_LEN = 29    # the radio stores "#name" in 31 bytes with a terminator
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def normalize(name) -> str:
    """Lowercase, no leading '#'; '*' (the unscoped root) counts as no scope."""
    n = str(name or "").strip().lower().lstrip("#")
    return "" if n == "*" else n


def valid(name: str) -> bool:
    return name == "" or (len(name) <= MAX_LEN and _NAME.fullmatch(name) is not None)
