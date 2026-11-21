#!/usr/bin/env python3
"""Fail if the tree holds anything that looks like a secret or personal detail.

SCAN_EXTRA="regex" adds local patterns (names, hostnames) without committing them.
"""
import ipaddress
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "map", "hardware", "firmware"}
SKIP_SUFFIX = {".png", ".ico", ".woff2", ".pbf", ".uf2", ".zip", ".step", ".stl"}
OK_IPS = {"0.0.0.0", "8.8.8.8", "10.42.0.1", "255.255.255.255"}
OK_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "100.64.0.0/10", "192.168.1.0/24", "192.168.50.0/24", "10.0.0.0/8")]

PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.(?!service\b|socket\b)[a-z]{2,}\b", re.I),
    "mac": re.compile(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", re.I),
    "ssh fingerprint": re.compile(r"SHA256:[A-Za-z0-9+/]{43}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|xox[baprs]-[\w-]{10,})\b"),
    "windows user path": re.compile(r"[A-Za-z]:\\Users\\\w+", re.I),
}
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EXTRA = re.compile(os.environ["SCAN_EXTRA"], re.I) if os.environ.get("SCAN_EXTRA") else None


def files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (SKIP_DIRS & set(p.relative_to(ROOT).parts)) and p.suffix not in SKIP_SUFFIX:
            yield p


def ip_ok(s):
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return True
    return s in OK_IPS or any(ip in n for n in OK_NETS) or not ip.is_private


def main():
    sys.stdout.reconfigure(errors="replace")
    hits = []
    for p in files():
        if p.name == "scan_private.py":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            found = [k for k, rx in PATTERNS.items() if rx.search(line)]
            found += ["ip " + m for m in IPV4.findall(line) if not ip_ok(m)]
            if EXTRA and EXTRA.search(line):
                found.append("SCAN_EXTRA")
            for f in found:
                hits.append(f"{p.relative_to(ROOT)}:{n}: {f}: {line.strip()[:90]}")
    print("\n".join(hits) or "clean")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
