#!/usr/bin/env python3
"""
Mesh Stack - walk-up web UI (bottle).

Tabs: Messages | Weather | Contacts | Coverage | Config | Flash
Serves on :80 (SMS_WEB_PORT). Meant for phones on the node's Wi-Fi / AP.

Reads meshbot's shared files (status.json, channels.json, config.json,
telemetry.sqlite, weather.sqlite); writes outbox rows (send / weather refresh),
config.json (Config tab), and kicks flash.sh (Flash tab).
"""
import html
import json
import os
import sqlite3
import subprocess
import time
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote, urlparse
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from bottle import Bottle, request, response, redirect, static_file, HTTPError

import about
import colors
import names
import regions

APP_DIR = os.environ.get("SMS_DIR", "/opt/mesh-stack")
DB_PATH = os.environ.get("SMS_DB", f"{APP_DIR}/telemetry.sqlite")
STATUS_PATH = os.environ.get("SMS_STATUS", f"{APP_DIR}/status.json")
CHANNELS_PATH = os.environ.get("SMS_CHANNELS", f"{APP_DIR}/channels.json")
CONTACTS_PATH = os.environ.get("SMS_CONTACTS", f"{APP_DIR}/contacts.json")
CONFIG_PATH = os.environ.get("SMS_CONFIG", f"{APP_DIR}/config.json")
WEATHER_DB = os.environ.get("SMS_WEATHER_DB", f"{APP_DIR}/weather.sqlite")
STATIC_DIR = os.environ.get("SMS_STATIC", f"{APP_DIR}/static")
MAP_DIR = os.environ.get("SMS_MAPS", f"{APP_DIR}/maps")    # dark.mbtiles, topo.mbtiles
FLASH_LOG = f"{APP_DIR}/flash.log"
FLASH_SH = f"{APP_DIR}/flash.sh"
FW_DIR = f"{APP_DIR}/firmware"
PORT = int(os.environ.get("SMS_WEB_PORT", "80"))
MAX_SEND = 180

app = Bottle()


# ── helpers ────────────────────────────────────────────────────────────────
def _json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def status():
    return _json(STATUS_PATH, {})


def cfg():
    s = status().get("cfg") or {}
    return {**s, **_json(CONFIG_PATH, {})}


def theme_colors():
    c = cfg()
    return (colors.resolve(c.get("color_main"), os.environ.get("SMS_COLOR_MAIN")),
            colors.resolve(c.get("color_accent"), os.environ.get("SMS_COLOR_ACCENT")))


def site_title():
    return names.resolve_title(cfg().get("title"), os.environ.get("SMS_TITLE"))


def channels():
    """{idx:int -> name:str}; always includes 0/Public."""
    raw = _json(CHANNELS_PATH, {})
    out = {0: "Public"}
    for k, v in raw.items():
        try:
            out[int(k)] = v or f"channel {k}"
        except ValueError:
            pass
    return dict(sorted(out.items()))


def chan_name(idx):
    if idx is None:
        return "dm"
    return channels().get(int(idx), f"#{idx}")


def db_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def recent(limit=40):
    try:
        c = db_ro(DB_PATH)
        rows = c.execute(
            "SELECT ts, source, sender, channel_idx, text FROM rx "
            "ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        c.close()
        return rows
    except sqlite3.Error:
        return []


def outbox_conn():
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.execute("""CREATE TABLE IF NOT EXISTS outbox (
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     ts INTEGER, target TEXT, text TEXT, sent INTEGER DEFAULT 0)""")
    return c


def queue_send(target, text):
    """target: channel index, or "@<key prefix>" for a DM."""
    text = (text or "").strip()[:MAX_SEND]
    if not text:
        return
    target = str(target)
    if not target.startswith("@"):
        target = str(int(target))
    c = outbox_conn()
    c.execute("INSERT INTO outbox (ts,target,text) VALUES (?,?,?)",
              (int(time.time()), target, text))
    c.commit()
    c.close()


def contacts_list():
    return _json(CONTACTS_PATH, [])


def contact_uri(c):
    return (f"meshcore://contact/add?name={quote(c.get('name', ''))}"
            f"&public_key={c.get('public_key', '')}&type={int(c.get('type', 1))}")


def parse_contact_uri(text):
    """Accept a meshcore://contact/add?... URL; return {name,public_key,type} or None."""
    text = (text or "").strip()
    try:
        q = parse_qs(urlparse(text).query)
        pk = q.get("public_key", [""])[0]
        if len(pk) != 64:
            return None
        return {"name": q.get("name", [pk[:6]])[0],
                "public_key": pk, "type": int(q.get("type", ["1"])[0])}
    except Exception:
        return None


def queue_cmd(kind, arg=""):
    c = outbox_conn()
    c.execute("""CREATE TABLE IF NOT EXISTS cmdq (
                     id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT,
                     arg TEXT, done INTEGER DEFAULT 0, note TEXT)""")
    c.execute("INSERT INTO cmdq (ts,kind,arg) VALUES (?,?,?)",
              (int(time.time()), kind, arg))
    c.commit()
    c.close()


def cmdq_recent():
    try:
        c = db_ro(DB_PATH)
        rows = c.execute("SELECT ts,kind,done,note FROM cmdq ORDER BY id DESC LIMIT 6").fetchall()
        c.close()
        return rows
    except sqlite3.Error:
        return []


CONTACT_TYPES = {1: "companion", 2: "repeater", 3: "room", 4: "sensor"}


def weather_rows(n=12):
    """Cached station readings, newest first."""
    try:
        c = db_ro(WEATHER_DB)
        r = c.execute("SELECT * FROM readings ORDER BY recv_time DESC LIMIT ?", (n,))
        cols = [d[0] for d in r.description]
        rows = [dict(zip(cols, row)) for row in r.fetchall()]
        c.close()
        return rows
    except sqlite3.Error:
        return []


def weather_log(n=15):
    """meshbot's weather events, newest first: (ts, kind, text)."""
    try:
        c = db_ro(DB_PATH)
        rows = c.execute("SELECT ts, kind, text FROM wxlog ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        c.close()
        return rows
    except sqlite3.Error:
        return []


def dur(s):
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


def stamp(ts, secs=False):
    """Local clock time; the date too when it isn't today."""
    t = time.localtime(ts)
    day = "" if time.strftime("%Y%j", t) == time.strftime("%Y%j") else f"{time.strftime('%b', t)} {t.tm_mday}, "
    return day + time.strftime("%I:%M:%S %p" if secs else "%I:%M %p", t).lstrip("0")


def fnum(row, k, d=0):
    v = row.get(k)
    return f"{v:.{d}f}" if isinstance(v, (int, float)) else ""


def show(x, unit=""):
    return f"{x}{unit}" if x else "—"


def also(x, unit):
    return f"{x} {unit}" if x else ""


def gps_text(s):
    if s.get("gps_on") is None:
        return "GPS ?"
    if not s["gps_on"]:
        return "GPS off"
    fix = s.get("gps_fix")
    if fix is None:
        return "GPS ? sats"
    return f"GPS {s.get('gps_sats') or 0} sats{'' if fix else ', no fix'}"


def coverage_rows(days=None):
    """meshbot's wardrive log, newest first: (ts, lat, lon, kind, node, hops, snr, rssi, snr_in)."""
    try:
        c = db_ro(DB_PATH)
        q = "SELECT ts, lat, lon, kind, node, hops, snr, rssi, snr_in FROM coverage"
        args = ()
        if days:
            q, args = q + " WHERE ts > ?", (int(time.time()) - days * 86400,)
        rows = c.execute(q + " ORDER BY ts DESC", args).fetchall()
        c.close()
        return rows
    except sqlite3.Error:
        return []


def node_names():
    """Public-key hex -> contact name, for matching the short repeater prefixes in the log."""
    return {(c.get("public_key") or "").lower(): c.get("name") or "?" for c in contacts_list()}


def name_for(node, names):
    node = (node or "").lower()
    hits = [n for k, n in names.items() if node and k.startswith(node)]
    return hits[0] if len(hits) == 1 else ("?" if not hits else f"{hits[0]} +{len(hits) - 1}")


# ── layout ─────────────────────────────────────────────────────────────────
TABS = [("/", "Messages"), ("/weather", "Weather"), ("/contacts", "Contacts"),
        ("/coverage", "Coverage"), ("/config", "Config"), ("/flash", "Flash"), ("/about", "About")]

# Oswald body/UI,
# Bungee Hairline headings, red primary / yellow accent, pill nav + buttons.
# Fonts are served from /static so the page looks right offline on the AP too.
CSS = """
@font-face{font-family:'Oswald';font-weight:300 700;font-display:swap;src:url(/static/oswald.woff2) format('woff2')}
@font-face{font-family:'Bungee Hairline';font-weight:400;font-display:swap;src:url(/static/bungee-hairline.woff2) format('woff2')}
:root{--red:#e00;--yellow:#ffcc00;--on-red:#fff;--bg:#0a0a0a;--surface:#161616;--surface-border:#2a2a2a;
  --text:#f2f2f2;--text-dim:#9c9c9c;--red-ink:#ff3b3b;--yellow-ink:#ffcc00;--field:#000}
@media (prefers-color-scheme:light){:root{--bg:#fff;--surface:#f4f4f4;--surface-border:#e2e2e2;
  --text:#161616;--text-dim:#555;--red-ink:#d00000;--yellow-ink:#9a6f00;--field:#fff}}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font:400 16px/1.5 'Oswald',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;
  background-image:radial-gradient(var(--surface-border) 1px,transparent 1px);background-size:26px 26px}
header{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 16px;
  background:color-mix(in srgb,var(--bg) 78%,transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  border-bottom:1px solid var(--surface-border)}
header img.logo{height:40px;display:block}
header .title{font-family:'Bungee Hairline',cursive;font-size:20px;letter-spacing:.02em}
header nav{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
header nav a{color:var(--text-dim);text-decoration:none;font-size:15px;padding:6px 14px;border-radius:999px;
  border:1px solid transparent;transition:color .2s,border-color .2s}
header nav a:hover{color:var(--text);border-color:var(--surface-border)}
header nav a.on{color:var(--text);border-color:var(--red)}
main{max-width:900px;margin:0 auto;padding:28px 16px 56px}
h2.sec{font-weight:500;font-size:1.3rem;margin:28px 0 12px}
h1.page{font-family:'Bungee Hairline',cursive;font-weight:400;font-size:clamp(1.8rem,5vw,2.6rem);line-height:1.1;margin:0 0 6px}
h1.page::after{content:'';display:block;width:60px;height:3px;margin-top:14px;background:var(--red);border-radius:2px}
h4{font-family:'Bungee Hairline',cursive;font-weight:400;font-size:1.2rem;margin:28px 0 10px}
h4::after{content:'';display:block;width:40px;height:2px;margin-top:8px;background:var(--red)}
.sub{color:var(--text-dim);font-weight:300;font-size:14px;margin:10px 0 16px}
a{color:var(--red-ink)}
code{color:var(--yellow-ink)}
.msg{padding:10px 14px;margin:0 0 8px;background:var(--surface);border:1px solid var(--surface-border);border-radius:10px}
.msg .who{color:var(--yellow-ink);font-weight:700}
.msg.tx{border-left:3px solid var(--red)}
.msg.tx .who{color:var(--red-ink)}
.meta{color:var(--text-dim);font-size:12px;font-weight:300}
form.row{display:flex;gap:8px;margin:14px 0;flex-wrap:wrap;align-items:center}
input[type=text],input[type=number],select,textarea{font:inherit;padding:9px 12px;background:var(--field);color:var(--text);
  border:1px solid var(--surface-border);border-radius:8px}
input:focus,select:focus,textarea:focus{outline:2px solid var(--yellow-ink);outline-offset:1px}
input[type=text]{flex:1;min-width:180px}
button{font:700 15px 'Oswald',sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:10px 24px;border-radius:999px;
  border:0;background:var(--red);color:var(--on-red);cursor:pointer;transition:background-color .2s,box-shadow .2s}
button:hover{filter:brightness(1.12);box-shadow:0 0 0 3px color-mix(in srgb,var(--yellow) 35%,transparent)}
button:focus-visible{outline:2px solid var(--yellow-ink);outline-offset:3px}
button.alt{background:none;color:var(--text);border:1px solid var(--surface-border)}
button.alt:hover{border-color:var(--text-dim);box-shadow:none}
table.cfg{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--surface-border);border-radius:10px;overflow:hidden}
table.cfg td{padding:10px 12px;border-bottom:1px solid var(--surface-border);vertical-align:middle}
table.cfg tr:last-child td{border-bottom:0}
table.cfg td:first-child{color:var(--text-dim);width:44%}
pre{background:var(--surface);border:1px solid var(--surface-border);border-radius:10px;padding:12px;overflow:auto;
  max-height:50vh;white-space:pre-wrap;font-size:13px}
.strip{display:flex;align-items:center;gap:8px 16px;flex-wrap:wrap;margin:8px 0 26px}
.strip .st{font-size:21px;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}
.tile{background:var(--surface);border:1px solid var(--surface-border);border-radius:14px;padding:22px 24px}
.tl{display:flex;align-items:center;gap:10px;color:var(--text-dim);font-size:13px;letter-spacing:.08em;text-transform:uppercase}
.ti{font-size:24px;letter-spacing:0}
.tv{font-size:38px;font-weight:700;line-height:1.1;color:var(--yellow-ink);margin:16px 0 6px;white-space:nowrap}
.tv small{font-size:16px;font-weight:400;color:var(--text-dim);margin-left:5px}
.tv.old{color:var(--text-dim)}
.ts{color:var(--text-dim);font-size:15px;font-weight:300;min-height:1.5em}
.empty{text-align:center;background:var(--surface);border:1px solid var(--surface-border);border-radius:14px;padding:44px 24px}
.empty .big{font-size:56px;line-height:1}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--surface-border);border-radius:14px}
table.hist{border-collapse:collapse;width:100%;white-space:nowrap}
table.hist th,table.hist td{padding:14px 20px;text-align:left;border-bottom:1px solid var(--surface-border)}
table.hist th{color:var(--text-dim);font-weight:400;font-size:14px}
table.hist tr:last-child td{border-bottom:0}
.feed{display:flex;flex-direction:column;gap:10px}
.ev{display:flex;align-items:baseline;gap:6px 14px;flex-wrap:wrap;padding:14px 20px;background:var(--surface);
  border:1px solid var(--surface-border);border-radius:12px}
.ev .ei{font-size:18px}
.ev .et{flex:1;min-width:180px}
@media (max-width:600px){header nav{margin-left:0;width:100%}header nav a{padding:5px 11px;font-size:14px}}
@media (max-width:760px){.tiles{grid-template-columns:repeat(2,1fr);gap:12px}.tile{padding:16px}.tv{font-size:30px;margin:12px 0 4px}.tl{font-size:12px;gap:8px}table.hist th,table.hist td{padding:12px 14px}}
/* Messages tab: Discord-style sidebar + conversation */
main.wide{max-width:1300px;padding:14px 16px}
main.wide h1.page,main.wide>.sub{display:none}
.chat{display:grid;grid-template-columns:270px 1fr;grid-template-rows:100%;height:calc(100vh - 136px);min-height:420px;
  background:var(--surface);border:1px solid var(--surface-border);border-radius:12px;overflow:hidden}
#side{border-right:1px solid var(--surface-border);overflow-y:auto;padding:10px 8px;background:var(--bg)}
#find{width:100%;margin-bottom:6px;font:inherit;padding:7px 10px;background:var(--field);color:var(--text);
  border:1px solid var(--surface-border);border-radius:8px}
.sec{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-dim);margin:14px 8px 4px}
.it{display:flex;align-items:center;gap:9px;padding:5px 8px;border-radius:8px;color:var(--text-dim);text-decoration:none}
.it:hover{background:var(--surface);color:var(--text)}
.it.on{background:var(--surface);color:var(--text);box-shadow:inset 3px 0 0 var(--red)}
.it .ic{flex:none;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-size:13px;
  font-weight:700;color:#fff;background:var(--surface-border)}
.it .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#pane{display:flex;flex-direction:column;min-width:0;min-height:0}
#pane .top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--surface-border)}
#title{font-weight:700;font-size:17px}
#tools{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
#tools input{font:inherit;padding:6px 10px;width:150px;background:var(--field);color:var(--text);
  border:1px solid var(--surface-border);border-radius:999px}
#tools button,#back{padding:6px 14px;font-size:13px}
#back{display:none}
#log{flex:1;min-height:0;overflow-y:auto;padding:12px 14px 4px}
.m{display:grid;grid-template-columns:40px 1fr;column-gap:12px;padding:2px 6px;border-radius:6px;margin-top:12px}
.m.g{margin-top:0}
.m:hover{background:color-mix(in srgb,var(--bg) 40%,transparent)}
.m .av{grid-row:span 2;width:38px;height:38px;border-radius:50%;display:grid;place-items:center;
  font-weight:700;color:#fff}
.m .h b{margin-right:8px;color:var(--yellow-ink)}
.m.me .h b{color:var(--red-ink)}
.m .t{grid-column:2;white-space:pre-wrap;overflow-wrap:anywhere}
.m.cli .t{font:13px/1.45 ui-monospace,Consolas,monospace}
.m.cli.me .t{color:var(--text-dim)}
#compose{display:flex;gap:8px;padding:12px 14px;border-top:1px solid var(--surface-border)}
#compose input{flex:1;font:inherit;padding:10px 14px;background:var(--field);color:var(--text);
  border:1px solid var(--surface-border);border-radius:999px}
@media (max-width:760px){
  .chat{grid-template-columns:1fr;height:calc(100vh - 140px)}
  #side{display:none}
  body.side #side{display:block}
  body.side #pane{display:none}
  #back{display:inline-block}
}
"""


def page(active, body, wide=False):
    s = status()
    age = s.get("last_rx_age")
    bar = (f"{s.get('node_name', 'node')} &middot; rx {s.get('rx_count', 0)} &middot; "
           f"last {'never' if age is None else dur(age)} &middot; {gps_text(s)}")
    nav = "".join(
        f'<a href="{p}" class="{"on" if p == active else ""}">{t}</a>'
        for p, t in TABS)
    heading = dict(TABS).get(active, "Mesh Stack")
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{esc(site_title())} &middot; {heading}</title><link rel=icon href="/static/favicon.ico">
<style>{CSS}{colors.theme_css(*theme_colors())}</style></head><body>
<header><img class=logo src="/static/logo.png" alt=""><span class=title>{esc(site_title())}</span><nav>{nav}</nav></header>
<main class="{'wide' if wide else ''}"><h1 class=page>{heading}</h1><p class=sub>{bar}</p>{body}</main></body></html>"""


def esc(x):
    return html.escape(str(x if x is not None else ""))


# ── routes ─────────────────────────────────────────────────────────────────
@app.get("/static/<path:path>")
def _static(path):
    return static_file(path, root=STATIC_DIR)


@app.get("/")
def messages():
    return page("/", CHAT_HTML, wide=True)


@app.post("/send")
def send():
    queue_send(request.forms.get("target", "0"), request.forms.get("text", ""))
    redirect("/")


# ── chat API (the Messages tab is a small JS app over these) ──────────────────
def rx_has_peer(c):
    return "peer" in [r[1] for r in c.execute("PRAGMA table_info(rx)")]


def contact_index():
    """{12-hex key prefix: contact dict} from the bot's contacts.json."""
    return {(x.get("public_key") or "")[:12].lower(): x for x in contacts_list()
            if isinstance(x, dict) and x.get("public_key")}


@app.get("/api/convos")
def api_convos():
    """Sidebar: channels (by index), DM threads (newest first), repeaters/rooms."""
    cons = contact_index()
    chans, dms, cli = {}, {}, {}
    try:
        c = db_ro(DB_PATH)
        for idx, ts in c.execute("SELECT channel_idx, MAX(ts) FROM rx WHERE channel_idx IS NOT NULL "
                                 "GROUP BY channel_idx"):
            chans[idx] = ts
        if rx_has_peer(c):
            for peer, ts in c.execute("SELECT peer, MAX(ts) FROM rx WHERE peer IS NOT NULL "
                                      "AND source IN ('dm','tx') GROUP BY peer"):
                dms[peer] = ts
            for peer, ts in c.execute("SELECT peer, MAX(ts) FROM rx WHERE peer IS NOT NULL "
                                      "AND source IN ('cli','clitx') GROUP BY peer"):
                cli[peer] = ts
        c.close()
    except sqlite3.Error:
        pass
    response.content_type = "application/json"
    return json.dumps({
        "me": status().get("node_name", "me"),
        "channels": [{"id": f"c{i}", "name": n, "last": chans.get(i)} for i, n in channels().items()],
        "dms": sorted(({"id": f"d{k}", "name": (cons.get(k) or {}).get("name") or k, "last": ts}
                       for k, ts in dms.items()), key=lambda x: -(x["last"] or 0)),
        "contacts": sorted(({"id": f"{'r' if x.get('type') in (2, 3) else 'd'}{k}",
                             "name": x.get("name") or k, "type": x.get("type", 1),
                             "last": cli.get(k) or dms.get(k), "heard": x.get("last_advert")}
                            for k, x in cons.items()), key=lambda x: x["name"].lower()),
    })


@app.get("/api/msgs")
def api_msgs():
    """Messages of one conversation, oldest first. id: c<idx> | d<key12> | r<key12>."""
    cid = request.query.get("id", "c0")
    out = []
    try:
        c = db_ro(DB_PATH)
        cols = "rowid, ts, source, sender, text, path_len, snr"
        if cid.startswith("c"):
            rows = c.execute(f"SELECT {cols} FROM rx WHERE channel_idx=? ORDER BY rowid DESC LIMIT 150",
                             (int(cid[1:]),)).fetchall()
        elif rx_has_peer(c):
            kinds = "('cli','clitx')" if cid.startswith("r") else "('dm','tx')"
            rows = c.execute(f"SELECT {cols} FROM rx WHERE peer=? AND source IN {kinds} "
                             "ORDER BY rowid DESC LIMIT 150", (cid[1:13].lower(),)).fetchall()
        else:
            rows = []
        c.close()
    except (sqlite3.Error, ValueError):
        rows = []
    for rid, ts, src, sender, text, hops, snr in reversed(rows):
        text = text or ""
        if src.startswith("chan") and sender and text.startswith(sender + ":"):
            text = text[len(sender) + 1:].lstrip()         # channel lines carry "name: msg"
        out.append({"id": rid, "ts": ts, "me": src in ("tx", "clitx"), "cli": src in ("cli", "clitx"),
                    "who": sender or "?", "text": text,
                    "hops": None if hops in (None, 255) else hops, "snr": snr})
    response.content_type = "application/json"
    return json.dumps(out)


@app.post("/api/send")
def api_send():
    d = request.json or {}
    cid, text = d.get("id", ""), (d.get("text") or "").strip()
    if not text:
        return {"ok": False}
    if cid.startswith("c"):
        queue_send(cid[1:], text)
    elif cid.startswith("d"):
        queue_send("@" + cid[1:13], text)
    elif cid.startswith("r"):
        queue_cmd("rpt_cmd", json.dumps({"key": cid[1:13], "cmd": text}))
    return {"ok": True}


@app.post("/api/rpt")
def api_rpt():
    """Repeater admin: action login (pwd) | logout | status. Never logged or stored here."""
    d = request.json or {}
    act = d.get("action")
    if act not in ("login", "logout", "status"):
        return {"ok": False}
    arg = {"key": (d.get("id") or "")[1:13]}
    if act == "login":
        arg["pwd"] = d.get("pwd") or ""
    queue_cmd("rpt_" + act, json.dumps(arg))
    return {"ok": True}


CHAT_HTML = r"""
<div class=chat>
 <aside id=side>
  <input id=find type=search placeholder="Find a channel, contact or repeater">
  <div class=sec>Channels</div><div id=lc></div>
  <div class=sec>Direct messages</div><div id=ld></div>
  <div class=sec>Repeaters &amp; rooms</div><div id=lr></div>
  <div id=lf></div>
 </aside>
 <section id=pane>
  <div class=top><button id=back class=alt title="Conversations">&#9776;</button>
   <span id=title>Pick a conversation</span><span id=sub class=meta></span>
   <span id=tools></span></div>
  <div id=log></div>
  <form id=compose autocomplete=off><input id=box maxlength=180 placeholder="Message"><button>Send</button></form>
 </section>
</div>
<script>
const $ = s => document.querySelector(s);
let cur = location.hash.slice(1) || 'c0', data = null, lastId = 0, timer = null;
const hue = s => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const ago = t => { if (!t) return ''; const s = Date.now() / 1000 - t;
  return s < 60 ? 'now' : s < 3600 ? Math.floor(s / 60) + 'm' : s < 86400 ? Math.floor(s / 3600) + 'h' : Math.floor(s / 86400) + 'd'; };
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

function item(c, icon) {
  const a = el('a', 'it' + (c.id === cur ? ' on' : ''));
  a.href = '#' + c.id;
  const i = el('span', 'ic', icon || (c.name || '?').replace(/[^\p{L}\p{N}]/gu, '').slice(0, 1).toUpperCase() || '?');
  if (!icon) i.style.background = `hsl(${hue(c.name)} 55% 38%)`;
  a.append(i, el('span', 'nm', c.name), el('span', 'meta', ago(c.last)));
  return a;
}
function sidebar() {
  if (!data) return;
  const q = $('#find').value.trim().toLowerCase(), hit = c => !q || c.name.toLowerCase().includes(q);
  const fill = (box, list, icon) => { box.replaceChildren(...list.filter(hit).map(c => item(c, icon))); };
  fill($('#lc'), data.channels, '#');
  const dmIds = new Set(data.dms.map(d => d.id));
  fill($('#ld'), data.dms);
  const rpt = data.contacts.filter(c => c.type === 2 || c.type === 3);
  fill($('#lr'), q ? rpt : rpt.filter(c => c.last).concat(rpt.filter(c => !c.last)).slice(0, 25), '⇄');
  $('#lf').replaceChildren(...(q ? [el('div', 'sec', 'Start a DM'),
    ...data.contacts.filter(c => c.type === 1 && !dmIds.has(c.id) && hit(c)).slice(0, 20).map(c => item(c))] : []));
}
function find(id) {
  return data && [...data.channels, ...data.dms, ...data.contacts].find(c => c.id === id);
}
function header() {
  const c = find(cur), kind = cur[0];
  $('#title').textContent = c ? (kind === 'c' && !c.name.startsWith('#') ? '# ' : '') + c.name : cur;
  $('#sub').textContent = kind === 'c' ? 'channel' : kind === 'd' ? 'direct message' : 'repeater console';
  $('#box').placeholder = kind === 'r' ? 'CLI command (e.g. ver, clock, neighbors, get name)'
    : 'Message ' + (c ? c.name : '');
  const t = $('#tools');
  if (t.dataset.for === cur) return;     // keep a half-typed password across sidebar refreshes
  t.dataset.for = cur;
  t.replaceChildren();
  if (kind === 'r') {
    const pw = el('input'); pw.type = 'password'; pw.placeholder = 'admin password'; pw.id = 'pw';
    const b = (label, fn) => { const x = el('button', 'alt', label); x.type = 'button'; x.onclick = fn; return x; };
    t.append(pw, b('Login', () => rpt('login', pw.value).then(() => pw.value = '')),
             b('Status', () => rpt('status')), b('Logout', () => rpt('logout')));
  }
}
function row(m, prev) {
  const grouped = prev && prev.who === m.who && prev.me === m.me && m.ts - prev.ts < 300;
  const r = el('div', 'm' + (grouped ? ' g' : '') + (m.me ? ' me' : '') + (m.cli ? ' cli' : ''));
  if (!grouped) {
    const who = m.me ? (data ? data.me : 'me') : m.who;
    const av = el('span', 'av', who.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 1).toUpperCase() || '?');
    av.style.background = `hsl(${hue(who)} 55% 38%)`;
    const h = el('div', 'h'); h.append(el('b', '', who), el('span', 'meta',
      new Date(m.ts * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'})
      + (m.hops != null ? ` · ${m.hops} hop${m.hops === 1 ? '' : 's'}` : '') + (m.snr != null ? ` · SNR ${m.snr}` : '')));
    r.append(av, h);
  }
  r.append(el('div', 't', m.text));
  return r;
}
async function load(full) {
  const id = cur;
  const msgs = await (await fetch('/api/msgs?id=' + encodeURIComponent(id))).json();
  if (id !== cur) return;
  const log = $('#log'), atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const newest = msgs.length ? msgs[msgs.length - 1].id : 0;
  if (!full && newest === lastId) return;
  lastId = newest;
  log.replaceChildren(...(msgs.length ? msgs.map((m, i) => row(m, msgs[i - 1]))
    : [el('p', 'sub', cur[0] === 'r' ? 'Log in with the admin password, then send CLI commands. Replies show up here.'
                                     : 'No messages yet.')]));
  if (full || atEnd) log.scrollTop = log.scrollHeight;
}
async function convos() {
  data = await (await fetch('/api/convos')).json();
  sidebar(); header();
}
async function rpt(action, pwd) {
  await fetch('/api/rpt', {method: 'POST', headers: {'Content-Type': 'application/json'},
                           body: JSON.stringify({id: cur, action, pwd})});
  setTimeout(() => load(), 1500);
}
function open(id) {
  cur = id; lastId = 0;
  document.body.classList.remove('side');
  sidebar(); header(); load(true);
}
$('#compose').onsubmit = async e => {
  e.preventDefault();
  const text = $('#box').value.trim();
  if (!text) return;
  $('#box').value = '';
  await fetch('/api/send', {method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({id: cur, text})});
  setTimeout(() => load(), 1500);
};
$('#find').oninput = sidebar;
$('#back').onclick = () => document.body.classList.toggle('side');
window.onhashchange = () => open(location.hash.slice(1) || 'c0');
convos().then(() => open(cur));
setInterval(() => load(), 3000);
setInterval(convos, 15000);
</script>
"""


WX_LOG_ICON = {"ok": "✅", "text": "💬", "ask": "📡", "warn": "⚠️"}


def wx_tile(icon, label, num, unit="", sub="", old=False):
    value = f"{num}<small>{unit}</small>" if num else "—"
    return (f'<div class=tile><div class=tl><span class=ti>{icon}</span>{label}</div>'
            f'<div class="tv{" old" if old else ""}">{value}</div><div class=ts>{sub}</div></div>')


def wx_history_row(r):
    wind = show(fnum(r, "wind_speed_mph"), " mph")
    if r.get("wind_direction"):
        wind += f" {esc(r['wind_direction'])}"
    cells = (stamp(r["recv_time"]), show(fnum(r, "temp_f"), "°F"), show(fnum(r, "humidity"), "%"), wind,
             show(fnum(r, "rain_total_in", 2), " in"), show(fnum(r, "pressure_inhg", 2), " inHg"),
             show(fnum(r, "battery_voltage", 2), " V"))
    return "<tr>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>"


@app.get("/weather")
def weather():
    rows, s, c, now = weather_rows(), status(), cfg(), time.time()
    idx = int(c.get("wx_channel_idx", -1))
    where = chan_name(idx) if idx >= 0 else "(set a Weather query channel in Config)"
    ref = (c.get("wx_node") or "").strip()
    station = ref and name_for(ref, node_names())
    if station == "?":
        station = ref
    tried = s.get("wx_try_at") or 0
    last_poll = f"📡 last poll {dur(now - tried)} ago · {esc(s.get('wx_result') or '—')}" if tried else ""
    head = [f"<span class=st>📍 {esc(station)}</span>"] if station else []

    if not rows:
        body = ("<div class=empty><div class=big>🌤️</div><p>No weather yet</p>"
                "<p class=sub>Press Refresh or wait for the next poll. A weather station has to answer.</p></div>")
    else:
        w = rows[0]
        age = now - int(w["recv_time"])
        old = age > max(15, 2.5 * max(1, int(c.get("wx_poll_min", 10)))) * 60
        n = lambda k, d=0: fnum(w, k, d)
        wd = esc(w.get("wind_direction") or "")
        rate = n("rain_rate_iph", 2)
        tiles = [
            wx_tile("🌡️", "Temperature", n("temp_f"), "°F", also(n("temp_c", 1), "°C")),
            wx_tile("💧", "Humidity", n("humidity"), "%", "relative"),
            wx_tile("🌫️", "Dew point", n("dewpoint_f"), "°F", also(n("dewpoint_c", 1), "°C")),
            wx_tile("💨", "Wind", n("wind_speed_mph"), "mph", f"from {wd}" if wd else ""),
            wx_tile("🌧️", "Rain", n("rain_total_in", 2), "in", f"rate {rate} in/h" if rate else ""),
            wx_tile("📊", "Pressure", n("pressure_inhg", 2), "inHg", also(n("pressure_hpa"), "hPa") or "telemetry only"),
            wx_tile("🔋", "Battery", n("battery_voltage", 2), "V", also(n("battery_current", 1), "A")),
        ]
        if n("light_lux"):
            tiles.append(wx_tile("☀️", "Light", n("light_lux"), "lx"))
        tiles.append(wx_tile("⚠️" if old else "🕒", "Updated", dur(age), "ago", stamp(w["recv_time"]), old))
        hist = ('<div class=scroll><table class=hist><tr><th>Received</th><th>🌡️ Temp</th><th>💧 RH</th>'
                '<th>💨 Wind</th><th>🌧️ Rain</th><th>📊 Pressure</th><th>🔋 Battery</th></tr>'
                + "".join(wx_history_row(r) for r in rows) + "</table></div>")
        body = f'<div class=tiles>{"".join(tiles)}</div><h4>📡 Telemetry received</h4>{hist}'

    events = "".join(
        f'<div class=ev><span class=ei>{WX_LOG_ICON.get(k, "•")}</span>'
        f'<span class=et>{esc(t[:1].upper() + t[1:])}</span>'
        f'<span class=meta>{stamp(ts, True)} · {dur(now - ts)} ago</span></div>'
        for ts, k, t in weather_log())
    log = (f"<div class=feed>{events}</div>" if events
           else "<p class=sub>Nothing logged yet. Weather polls and asks show up here.</p>")
    refresh = (f'<form class=row method=post action=/weather/refresh>'
               f'<button class=alt>🔄 Refresh — send "{esc(c.get("cmd_prefix", "wx"))}" on {esc(where)}</button></form>')
    strip = f'<div class=strip>{"".join(head)}<span class=meta>{last_poll}</span></div>'
    return page("/weather", f"{refresh}{strip}{body}<h4>📝 Log</h4>{log}")


@app.post("/weather/refresh")
def weather_refresh():
    if int(cfg().get("wx_channel_idx", -1)) >= 0:
        queue_cmd("wx_ask")
    redirect("/weather")


@app.get("/contacts")
def contacts_get():
    cl = contacts_list()
    rows = "".join(
        f'<div class=msg><span class=who>{esc(c.get("name") or "?")}</span> '
        f'<span class=meta>{CONTACT_TYPES.get(int(c.get("type", 1)), "?")} &middot; '
        f'{esc((c.get("public_key") or "")[:12])}</span><br>'
        f'<input type=text readonly onclick="this.select()" value="{esc(contact_uri(c))}"></div>'
        for c in cl
    ) or "<p class=sub>no contacts yet</p>"
    hist = "".join(
        f'<div class=meta>{time.strftime("%H:%M:%S", time.localtime(ts))} '
        f'{esc(kind)} {"ok" if done else "queued"} {esc(note or "")}</div>'
        for ts, kind, done, note in cmdq_recent())
    return page("/contacts", f"""
<p class=sub>{len(cl)} contacts. Copy a URI into the station radio (phone app &rarr; add contact),
and paste one back here.</p>
<form class=row method=post action=/contacts/import>
  <input type=text name=uri placeholder="meshcore://contact/add?..." required>
  <button>Import</button>
</form>
<form class=row method=post action=/advert>
  <button>Send flood advert</button>
  <button name=kind value=advert class=alt>Send zero-hop advert</button>
</form>
<form class=row method=post action=/contacts/refresh><button class=alt>Refresh from radio</button></form>
{rows}
<h4>recent contact actions</h4>{hist or "<p class=sub>none</p>"}""")


@app.post("/contacts/import")
def contacts_import():
    d = parse_contact_uri(request.forms.get("uri", ""))
    if d:
        queue_cmd("add_contact", json.dumps(d))
    redirect("/contacts")


@app.post("/advert")
def advert():
    queue_cmd("advert" if request.forms.get("kind") == "advert" else "flood_advert")
    redirect("/contacts")


@app.post("/advert/position")
def advert_position():
    queue_cmd("advert_pos", "1" if request.forms.get("on") == "1" else "0")
    redirect("/config")


@app.post("/radio/name")
def radio_name():
    name = (request.forms.getunicode("name") or "").strip()
    if (err := names.check_radio_name(name)):
        raise HTTPError(400, err)
    queue_cmd("set_name", name)
    redirect("/config")


@app.post("/contacts/refresh")
def contacts_refresh():
    queue_cmd("refresh_contacts")
    redirect("/contacts")


COVERAGE_KINDS = {"echo": "our ping repeated", "rx": "heard through", "disc": "answered survey",
                  "drop": "survey, no answer"}


TILE_TYPES = {"dark": ("application/x-protobuf", "gzip"), "topo": ("image/jpeg", None)}


@app.get("/tiles/<name>/<z:int>/<x:int>/<y:int>")
def tile(name, z, x, y):
    """Offline map tiles from maps/<name>.mbtiles (TMS rows, so y is flipped)."""
    if name not in TILE_TYPES:
        raise HTTPError(404)
    try:
        c = db_ro(f"{MAP_DIR}/{name}.mbtiles")
        row = c.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                        (z, x, (1 << z) - 1 - y)).fetchone()
        c.close()
    except sqlite3.Error:
        row = None
    if not row:
        response.status = 204                 # outside the downloaded area: blank, not an error
        return ""
    ctype, enc = TILE_TYPES[name]
    response.content_type = ctype
    if enc:
        response.set_header("Content-Encoding", enc)
    response.set_header("Cache-Control", "max-age=86400")
    return bytes(row[0])


def merged_nodes(rows):
    """Repeater stats keyed by the longest prefix heard. Paths carry 1-3 byte hashes, so a
    short prefix that only one longer heard prefix starts with is the same repeater."""
    rpt = {}
    for ts, lat, lon, kind, node, hops, snr, rssi, snr_in in rows:
        if not node:
            continue
        e = rpt.setdefault(node, {"n": 0, "best": None, "last": ts})
        e["n"] += 1
        e["last"] = max(e["last"], ts)
        if snr is not None and (e["best"] is None or snr > e["best"]):
            e["best"] = snr
    for short in sorted(rpt, key=len):
        longer = [k for k in rpt if len(k) > len(short) and k.startswith(short)]
        if len(longer) == 1:
            a, b = rpt.pop(short), rpt[longer[0]]
            b["n"] += a["n"]
            b["last"] = max(a["last"], b["last"])
            if a["best"] is not None and (b["best"] is None or a["best"] > b["best"]):
                b["best"] = a["best"]
    return rpt


@app.get("/api/map")
def api_map():
    """Coverage points (last week), positioned repeaters / rooms, and us, as GeoJSON."""
    names = node_names()
    pts = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"kind": kind, "snr": snr, "node": node or "",
                           "name": name_for(node, names) if node else "",
                           "t": time.strftime("%b %d %H:%M", time.localtime(ts))}}
           for ts, lat, lon, kind, node, hops, snr, rssi, snr_in in coverage_rows(days=7)
           if lat is not None and lon is not None]
    nodes = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
              "properties": {"name": c.get("name") or "?", "type": int(c.get("type", 0))}}
             for c in contacts_list()
             if c.get("lat") and c.get("lon") and int(c.get("type", 0)) in (2, 3)]
    st = status()
    me = ([st["lon"], st["lat"]] if st.get("lat") is not None and st.get("lon") is not None else None)
    return {"coverage": {"type": "FeatureCollection", "features": pts},
            "nodes": {"type": "FeatureCollection", "features": nodes}, "me": me}


MAP_JS = """
const O = location.origin;
const style = {version: 8, glyphs: O + '/static/map/fonts/{fontstack}/{range}.pbf',
  sprite: O + '/static/map/sprites/dark',
  sources: {
    protomaps: {type: 'vector', tiles: [O + '/tiles/dark/{z}/{x}/{y}'], maxzoom: 15,
      attribution: '&copy; OpenStreetMap, Protomaps'},
    topo: {type: 'raster', tiles: [O + '/tiles/topo/{z}/{x}/{y}'], tileSize: 256, minzoom: 8, maxzoom: 14,
      attribution: 'USGS The National Map'}},
  layers: [...basemaps.layers('protomaps', basemaps.namedFlavor('dark'), {lang: 'en'}),
    {id: 'topo', type: 'raster', source: 'topo', layout: {visibility: 'none'}}]};
const map = new maplibregl.Map({container: 'map', style, center: [-97.65, 30.65], zoom: 11,
  maxBounds: [[-99.3, 28.9], [-96.0, 33.6]], attributionControl: {compact: true}});
map.addControl(new maplibregl.NavigationControl({showCompass: false}));
function base(which) {
  map.setLayoutProperty('topo', 'visibility', which === 'topo' ? 'visible' : 'none');
  document.querySelectorAll('.mapsw button').forEach(b => b.classList.toggle('alt', b.dataset.b !== which));
  try { localStorage.setItem('sms-base', which); } catch (e) {}
}
map.on('load', async () => {
  let saved = 'dark';
  try { saved = localStorage.getItem('sms-base') || 'dark'; } catch (e) {}
  base(saved);
  const d = await (await fetch('/api/map')).json();
  map.addSource('nodes', {type: 'geojson', data: d.nodes});
  map.addSource('cov', {type: 'geojson', data: d.coverage});
  map.addLayer({id: 'nodes', type: 'circle', source: 'nodes', paint: {
    'circle-radius': 6, 'circle-color': ['match', ['get', 'type'], 2, '#ff9628', '#5096ff'],
    'circle-stroke-width': 1.5, 'circle-stroke-color': '#000'}});
  map.addLayer({id: 'node-names', type: 'symbol', source: 'nodes', minzoom: 10, layout: {
    'text-field': ['get', 'name'], 'text-font': ['Noto Sans Medium'], 'text-size': 12,
    'text-offset': [0, 1.1], 'text-anchor': 'top', 'text-optional': true},
    paint: {'text-color': '#ffd27a', 'text-halo-color': '#000', 'text-halo-width': 1.5}});
  map.addLayer({id: 'cov', type: 'circle', source: 'cov', paint: {
    'circle-radius': 4, 'circle-stroke-width': 0.5, 'circle-stroke-color': '#000',
    'circle-color': ['case', ['==', ['get', 'kind'], 'drop'], '#7a2020',
      ['>', ['coalesce', ['get', 'snr'], -99], 0], '#28c85a',
      ['>', ['coalesce', ['get', 'snr'], -99], -10], '#f0b428', '#e63c3c']}});
  if (d.me) {
    const el = document.createElement('div'); el.className = 'me';
    new maplibregl.Marker({element: el}).setLngLat(d.me).addTo(map);
    map.setCenter(d.me);
  }
  for (const id of ['cov', 'nodes']) {
    map.on('click', id, e => {
      const p = e.features[0].properties;
      const html = id === 'cov'
        ? `<b>${p.name || p.kind}</b><br>${p.kind}${p.node ? ' via ' + p.node : ''}<br>SNR ${p.snr ?? '-'} &middot; ${p.t}`
        : `<b>${p.name}</b><br>${p.type == 2 ? 'repeater' : 'room server'}`;
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
    map.on('mouseenter', id, () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', id, () => map.getCanvas().style.cursor = '');
  }
});
document.querySelectorAll('.mapsw button').forEach(b => b.onclick = () => base(b.dataset.b));
"""

MAP_CSS = """#map{height:65vh;min-height:320px;border-radius:8px;margin:8px 0}
.mapsw{display:flex;gap:8px}.me{width:14px;height:14px;border-radius:50%;background:#fff;
border:3px solid #c81414;box-shadow:0 0 6px #000}.maplibregl-popup-content{color:#111}"""


@app.get("/coverage")
def coverage():
    s = status()
    wd = s.get("wardrive") or {}
    rows = coverage_rows()
    counts = {k: sum(1 for r in rows if r[3] == k) for k in COVERAGE_KINDS}
    names = node_names()
    table = "".join(
        f'<tr><td>{esc(name_for(n, names))}</td><td><code>{esc(n[:8])}</code></td><td>{e["n"]}</td>'
        f'<td>{"—" if e["best"] is None else round(e["best"], 1)}</td>'
        f'<td>{time.strftime("%b %d %H:%M", time.localtime(e["last"]))}</td></tr>'
        for n, e in sorted(merged_nodes(rows).items(), key=lambda kv: -kv[1]["n"]))
    summary = " &middot; ".join(f"{counts[k]} {v}" for k, v in COVERAGE_KINDS.items())
    return page("/coverage", f"""
<link rel=stylesheet href="/static/map/maplibre-gl.css"><style>{MAP_CSS}</style>
<p>{esc(gps_text(s))} &middot; survey <b>{'on' if wd.get('survey') else 'off'}</b>
&middot; last row {esc(time.strftime('%b %d %H:%M', time.localtime(wd['last_at']))) if wd.get('last_at') else 'never'}</p>
<p class=sub>{summary}</p>
<div class="row mapsw"><button data-b=dark>Dark</button><button data-b=topo class=alt>Topo</button>
<form method=get action=/coverage.csv style="margin-left:auto"><button class=alt>Download CSV</button></form></div>
<div id=map></div>
<p class=sub>Dots: green SNR &gt; 0, yellow &gt; -10, red below, dark red no answer (last 7 days).
Orange: repeaters, blue: room servers, with a known position. Tap one for details.
Maps work offline: San Antonio to Dallas-Fort Worth.</p>
<h4>repeaters heard</h4>
<table class=cfg><tr><th>name</th><th>prefix</th><th>rows</th><th>best SNR</th><th>last</th></tr>
{table or '<tr><td colspan=5>none yet</td></tr>'}</table>
<p class=sub>Logged on this node only. The map at meshmapper.net and MeshWars take data from the
MeshMapper app, not from here. Survey and logging settings are on the Config tab.</p>
<script src="/static/map/maplibre-gl.js"></script><script src="/static/map/basemaps.js"></script>
<script>{MAP_JS}</script>""", wide=True)


@app.get("/coverage.csv")
def coverage_csv():
    names = node_names()
    response.content_type = "text/csv; charset=utf-8"
    response.set_header("Content-Disposition", "attachment; filename=sms-coverage.csv")
    lines = ["time_utc,lat,lon,kind,node,name,hops,snr,rssi,snr_in"]
    for ts, lat, lon, kind, node, hops, snr, rssi, snr_in in coverage_rows():
        name = name_for(node, names).replace('"', "'") if node else ""
        lines.append(",".join("" if v is None else str(v) for v in (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)), lat, lon, kind, node,
            f'"{name}"', hops, snr, rssi, snr_in)))
    return "\n".join(lines) + "\n"


@app.get("/config")
def config_get():
    c = cfg()
    ch = channels()
    def sel(name, val):
        return "".join(
            f'<option value="{i}"{" selected" if i == int(val) else ""}>{esc(n)}</option>'
            for i, n in ({**ch, -1: "(none / DMs only)"}).items())
    main, accent = theme_colors()
    main_color, accent_color = main or colors.DEFAULT_MAIN, accent or colors.DEFAULT_ACCENT
    return page("/config", f"""
<form method=post action=/config>
<table class=cfg>
<tr><td>Header title (screen and web)</td><td><input type=text name=title maxlength={names.TITLE_MAX} placeholder="{esc(names.DEFAULT_TITLE)}" value="{esc(c.get("title", ""))}"></td></tr>
<tr><td>Main color</td><td><input type=color name=color_main value="{main_color}"></td></tr>
<tr><td>Accent color</td><td><input type=color name=color_accent value="{accent_color}"></td></tr>
<tr><td>Bot reply channel</td><td><select name=bot_channel_idx>{sel("b", c.get("bot_channel_idx", -1))}</select></td></tr>
<tr><td>Command prefix</td><td><input type=text name=cmd_prefix value="{esc(c.get("cmd_prefix", "wx"))}"></td></tr>
<tr><td>Default send channel</td><td><select name=web_send_default_idx>{sel("d", c.get("web_send_default_idx", 0))}</select></td></tr>
<tr><td>Weather query channel</td><td><select name=wx_channel_idx>{sel("w", c.get("wx_channel_idx", -1))}</select></td></tr>
<tr><td>Idle poweroff (sec, 0=never)</td><td><input type=number name=idle_poweroff_sec value="{int(c.get("idle_poweroff_sec", 0))}"></td></tr>
<tr><td>Weather station (contact name or key prefix)</td><td><input type=text name=wx_node value="{esc(c.get("wx_node", ""))}"></td></tr>
<tr><td>Weather poll (minutes)</td><td><input type=number name=wx_poll_min min=1 value="{int(c.get("wx_poll_min", 10))}"></td></tr>
<tr><td>Coverage logging (listen only)</td><td><input type=checkbox name=wardrive_log value=1{" checked" if int(c.get("wardrive_log", 1)) else ""}></td></tr>
<tr><td>Coverage survey (zero-hop discover while moving)</td><td><input type=checkbox name=wardrive_survey value=1{" checked" if int(c.get("wardrive_survey", 0)) else ""}></td></tr>
<tr><td>Survey at most every (sec)</td><td><input type=number name=wardrive_survey_sec min=120 value="{int(c.get("wardrive_survey_sec", 120))}"></td></tr>
<tr><td>Survey after moving (m)</td><td><input type=number name=wardrive_survey_m min=50 value="{int(c.get("wardrive_survey_m", 200))}"></td></tr>
<tr><td>Region scope (flood only through repeaters carrying it)</td><td><input type=checkbox name=region_scope_on value=1{" checked" if int(c.get("region_scope_on", 0)) else ""}></td></tr>
<tr><td>Region scope name</td><td><input type=text name=region_scope list=scopes maxlength={regions.MAX_LEN} pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="us-tx-aus" value="{esc(regions.normalize(c.get("region_scope", "")))}"><datalist id=scopes>{"".join(f'<option value="{s}">{esc(n)}</option>' for s, n in regions.PRESETS)}</datalist></td></tr>
<tr><td>Fox MAVLink source (Mission Planner mirror)</td><td><input type=text name=fox_mavlink value="{esc(c.get("fox_mavlink", "udpin:0.0.0.0:14550"))}"></td></tr>
<tr><td>Fox bot ($fox replies; switching on announces on #aerial)</td><td><input type=checkbox name=fox_bot value=1{" checked" if int(c.get("fox_bot", 0)) else ""}></td></tr>
<tr><td>Fox auto-broadcast on #aerial</td><td><input type=checkbox name=fox_auto value=1{" checked" if int(c.get("fox_auto", 0)) else ""}></td></tr>
<tr><td>Fox broadcast every (sec)</td><td><input type=number name=fox_auto_sec min=60 value="{int(c.get("fox_auto_sec", 120))}"></td></tr>
<tr><td>Fox battery reserve (% counted as empty)</td><td><input type=number name=fox_reserve_pct min=0 max=90 value="{int(c.get("fox_reserve_pct", 20))}"></td></tr>
</table>
<form class=row><button>Save</button> <button class=alt name=reset_colors value=1>Reset colors</button></form>
</form>
<form class=row method=post action=/advert/position>
<span>Position in adverts: <b>{"unknown" if status().get("adv_loc") is None else "on" if status().get("adv_loc") else "off"}</b></span>
<button name=on value={0 if status().get("adv_loc") else 1}{" disabled" if status().get("adv_loc") is None else ""}>Turn {"off" if status().get("adv_loc") else "on"}</button>
</form>
<form class=row method=post action=/radio/name>
<span>Radio name: <b>{esc(status().get("node_name") or "unknown")}</b></span>
<input type=text name=name maxlength={names.RADIO_NAME_MAX} value="{esc(status().get("node_name") or "")}" required>
<button>Rename</button>
</form>
<p class=sub>Saved to <code>config.json</code>; the bot picks it up within ~5 s. A new radio name reaches the mesh with the next advert (Contacts tab).</p>
<p class=sub>Radio flood scope: <code>{("#" + esc(status().get("region_scope", ""))) if status().get("region_scope") else "none (unscoped)"}</code>.
A scope applies to every flood the radio sends, adverts and DMs without a known path included, and only repeaters carrying it forward them. Leave it off on meshes that don't use scopes.</p>""")


@app.post("/config")
def config_post():
    f = request.forms
    title = (f.getunicode("title") or "").strip()
    if (err := names.check_title(title)):
        raise HTTPError(400, err)
    picked = {}
    for key, default in (("color_main", colors.DEFAULT_MAIN), ("color_accent", colors.DEFAULT_ACCENT)):
        raw = (f.get(key) or "").strip()
        value = colors.parse(raw)
        if raw and not value:
            raise HTTPError(400, "Colors are #rrggbb")
        picked[key] = "" if f.get("reset_colors") or value in (None, default) else value
    scope = regions.normalize(f.get("region_scope", ""))
    if not regions.valid(scope):
        raise HTTPError(400, f"Region scope: lowercase letters, digits and hyphens, up to {regions.MAX_LEN} characters")
    if f.get("region_scope_on") and not scope:
        raise HTTPError(400, "Pick a region scope before turning it on")
    new = {**_json(CONFIG_PATH, {}),      # keep settings this form doesn't show
        "title": title,
        **picked,
        "bot_channel_idx": int(f.get("bot_channel_idx", -1)),
        "cmd_prefix": (f.get("cmd_prefix", "wx") or "wx").strip().lower(),
        "web_send_default_idx": int(f.get("web_send_default_idx", 0)),
        "wx_channel_idx": int(f.get("wx_channel_idx", -1)),
        "idle_poweroff_sec": max(0, int(f.get("idle_poweroff_sec", 0) or 0)),
        "wx_node": (f.get("wx_node", "") or "").strip(),
        "wx_poll_min": max(1, int(f.get("wx_poll_min", 10) or 10)),
        "wardrive_log": 1 if f.get("wardrive_log") else 0,
        "wardrive_survey": 1 if f.get("wardrive_survey") else 0,
        # repeaters answer 4 discovers per 2 min, shared by everyone nearby
        "wardrive_survey_sec": max(120, int(f.get("wardrive_survey_sec", 120) or 120)),
        "wardrive_survey_m": max(50, int(f.get("wardrive_survey_m", 200) or 200)),
        "region_scope_on": 1 if f.get("region_scope_on") else 0,
        "region_scope": scope,
        "fox_mavlink": (f.get("fox_mavlink", "") or "udpin:0.0.0.0:14550").strip(),
        "fox_bot": 1 if f.get("fox_bot") else 0,
        "fox_auto": 1 if f.get("fox_auto") else 0,
        "fox_auto_sec": max(60, int(f.get("fox_auto_sec", 120) or 120)),
        "fox_reserve_pct": min(90, max(0, int(f.get("fox_reserve_pct", 20) or 0))),
    }
    try:
        with open(CONFIG_PATH + ".tmp", "w") as fh:
            json.dump(new, fh, indent=2)
        os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
    except OSError as e:
        raise HTTPError(500, f"write failed: {e}")
    redirect("/config")


def flash_methods():
    """What the Pi can flash with right now: a USB link to the T114, and openocd for SWD."""
    usb = any(os.path.exists(p) for p in ("/dev/ttyACM0", "/dev/ttyACM1")) or any(
        "Heltec" in n for n in (os.listdir("/dev/serial/by-id") if os.path.isdir("/dev/serial/by-id") else []))
    swd = any(os.access(os.path.join(d, "openocd"), os.X_OK)
              for d in os.environ.get("PATH", "/usr/bin").split(":"))
    return usb, swd


def bundled_fw():
    try:
        names = [n for n in os.listdir(FW_DIR) if n.endswith(".uf2") and "sfm" in n
                 and n.startswith("Heltec_t114_companion_radio_ble_usb")]
        return max(names, key=lambda n: os.path.getmtime(os.path.join(FW_DIR, n)))
    except (OSError, ValueError):
        return None


@app.get("/flash")
def flash_get():
    s = status()
    log = ""
    try:
        with open(FLASH_LOG) as fh:
            log = fh.read()[-4000:]
    except OSError:
        pass
    usb, swd = flash_methods()
    fw = bundled_fw()
    def opt(v, label, ok, why):
        return f'<option value={v}{"" if ok else " disabled"}>{label}{"" if ok else " — " + why}</option>'
    return page("/flash", f"""
<p>Radio: <b>{esc(s.get('node_name', '?'))}</b> &nbsp; firmware <b>{esc(s.get('fw', '?'))}</b></p>
<form class=row method=post action=/flash/run>
  <select name=method>
    {opt("usb", "USB cable", usb, "no T114 on a Pi USB port")}
    {opt("swd", "Internal wiring (SWD)", swd, "openocd not installed")}
  </select>
  <select name=which>
    <option value=sfm>Patched build ({esc(fw or "none bundled")})</option>
    <option value=stock>Stock latest MeshCore release</option>
  </select>
  <button class=alt>Flash T114</button>
</form>
<p class=sub><b>USB cable:</b> a cable from a Pi USB port to the T114; the Pi puts the radio in
its bootloader and copies the firmware, no button presses.
<b>Internal wiring:</b> SWD from Pi pins 40 (clock), 38 (data) and 39 (ground) to the T114's
SWD pads; writes the app only, so contacts and settings stay, and it works even if the radio
won't boot. <b>Stock</b> downloads the upstream release and loses the patched-firmware features (Pi battery,
GPS lock, screen keys). Both stop the bot while flashing. Reload this page to see progress.</p>
<pre>{esc(log) or "(no flash run yet)"}</pre>""")


@app.post("/flash/run")
def flash_run():
    method = request.forms.get("method") or "usb"
    which = request.forms.get("which") or "sfm"
    if method not in ("usb", "swd") or which not in ("sfm", "stock"):
        raise HTTPError(400, "bad target")
    try:
        subprocess.Popen(["sudo", "-n", FLASH_SH, method, which],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPError(500, str(e))
    time.sleep(1)
    redirect("/flash")


@app.get("/about")
def about_get():
    s = status()
    rows = "".join(
        f'<tr><td><a href="{esc(url)}" target=_blank rel="noopener noreferrer">{esc(name)}</a></td>'
        f'<td>{esc(role)}</td><td>{esc(lic)}</td></tr>'
        for name, role, lic, url in about.CREDITS)
    return page("/about", f"""
<table class=cfg>
<tr><td>Project</td><td><b>{esc(about.NAME)}</b></td></tr>
<tr><td>Version</td><td>{esc(about.VERSION)}</td></tr>
<tr><td>Radio firmware</td><td>{esc(s.get("fw") or "unknown")}</td></tr>
<tr><td>License</td><td>{esc(about.LICENSE)}</td></tr>
<tr><td>Source</td><td><a href="{esc(about.REPO)}" target=_blank rel="noopener noreferrer">{esc(about.REPO)}</a></td></tr>
</table>
<h2 class=sec>Credits</h2>
<table class=cfg><tr><td><b>Project</b></td><td><b>Used for</b></td><td><b>License</b></td></tr>{rows}</table>
<p class=sub>Map data &copy; OpenStreetMap contributors. This project is not affiliated with or endorsed by the
projects above.</p>""")


@app.get("/events")
def events():
    response.content_type = "text/plain; charset=utf-8"
    return "\n".join(
        f'{time.strftime("%H:%M:%S", time.localtime(ts))} {src} {sender or ""} '
        f'[{chan_name(cidx)}]: {text}'
        for ts, src, sender, cidx, text in recent(60)
    ) or "(no messages)"


class _Server(ThreadingMixIn, WSGIServer):
    """One thread per connection: an idle browser pre-connect must not stall everyone else."""
    daemon_threads = True
    request_queue_size = 32


class _Handler(WSGIRequestHandler):
    timeout = 30     # drop connections that never send a request

    def log_message(self, *args):
        pass


def make_web_server(port, host="0.0.0.0"):
    return make_server(host, port, app, server_class=_Server, handler_class=_Handler)


if __name__ == "__main__":
    make_web_server(PORT).serve_forever()
