#!/usr/bin/env python3
"""
Mesh Stack - T114 command bot + telemetry logger.

Speaks to a MeshCore Companion Radio (Heltec T114) over USB serial using the
`meshcore` library (asyncio).

────────────────────────────────────────────────────────────────────────────
Bot etiquette (modelled on https://www.austinmesh.org/join/bot-guidelines/):

  1. Most channels are for humans. This bot replies only on the configured bot
     channel and to direct messages, and on a channel only when addressed by the
     command prefix ("<prefix> wx"). It ignores its own messages. Exception: "$fox"
     (Fox aerial repeater telemetry) is also answered on #aerial, the channel
     set up for it.
  2. Long interactions belong in DMs. Replies are single-shot.
  3. Avoid internet dependencies. `wx` reads a LOCAL weather cache
     (SMS_WEATHER_DB) in the `readings` schema - no cloud API. See
     docs/notes/weather.md.
  4. Battery + solar. Short replies, per-sender rate limit, optional idle-poweroff.
  5. Source-available.

Runtime config: env vars are defaults; /opt/mesh-stack/config.json overrides them
and is re-read every few seconds (the web Config tab writes it).

Shared files it writes for the web UI / screen:
  status.json    - rx count, last message, firmware, node name
  channels.json  - {index: name} from the radio
  telemetry.sqlite  rx (every message incl. outbound 'tx'), outbox (web send queue)
────────────────────────────────────────────────────────────────────────────
"""
import asyncio
import json
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import sqlite3
import subprocess
import time

from meshcore import MeshCore, EventType

import fox
import mux
import names
import regions
import wardrive

APP_DIR = os.environ.get("SMS_DIR", "/opt/mesh-stack")
SERIAL = os.environ.get("SMS_SERIAL", "/dev/serial0")   # P1-header UART; /dev/ttyACM0 on USB
BAUD = int(os.environ.get("SMS_BAUD", "115200"))
# The mux owns the serial port: meshbot talks to it on loopback and the MeshCore app can share
# the radio over the tailnet on MUX_PORT (0 = no app port). SMS_MUX=0 goes back to direct serial.
MUX = os.environ.get("SMS_MUX", "1") != "0"
MUX_LOCAL_PORT = int(os.environ.get("SMS_MUX_LOCAL_PORT", "5001"))
MUX_PORT = int(os.environ.get("SMS_MUX_PORT", "5000"))
MUX_ALLOW = [n for n in os.environ.get("SMS_MUX_ALLOW", "").split(",") if n]   # extra CIDRs beyond the node's own networks
MUX_LAN = os.environ.get("SMS_MUX_LAN", "1") != "0"   # also admit peers on the node's own private networks
DB_PATH = os.environ.get("SMS_DB", f"{APP_DIR}/telemetry.sqlite")
STATUS_PATH = os.environ.get("SMS_STATUS", f"{APP_DIR}/status.json")
CHANNELS_PATH = os.environ.get("SMS_CHANNELS", f"{APP_DIR}/channels.json")
CONTACTS_PATH = os.environ.get("SMS_CONTACTS", f"{APP_DIR}/contacts.json")
CONFIG_PATH = os.environ.get("SMS_CONFIG", f"{APP_DIR}/config.json")
POWER_JSON = os.environ.get("SMS_POWER_JSON", f"{APP_DIR}/power.json")
FOX_JSON = os.environ.get("SMS_FOX_JSON", f"{APP_DIR}/fox.json")
WEATHER_DB = os.environ.get("SMS_WEATHER_DB", "")

# Host-fed battery (needs the sms-batt T114 build, firmware/t114/): push the pack
# voltage from power.json so the T114's battery icon / app / telemetry show the
# Pi pack. Sent per-cell, since the T114 maps 3.0-4.2 V to 0-100%.
EXT_BATT_SEC = 60
EXT_BATT_MAX_AGE = 120                       # ignore a stale power.json
BATT_CELLS = int(os.environ.get("SMS_BATT_CELLS", "1"))      # cells in series
BATT_CHG_MA = float(os.environ.get("SMS_BATT_CHG_MA", "-30"))  # charging below this (INA219 sign)

# Weather payload contract (see docs/notes/weather.md).
WX_NUMERIC_KEYS = [
    "temp_c", "temp_f", "humidity", "dewpoint_c", "dewpoint_f",
    "pressure_hpa", "pressure_inhg", "light_lux",
    "wind_speed_mph", "rain_rate_iph", "rain_total_in",
    "battery_voltage", "battery_current", "battery_power",
    "timestamp",
]
WX_ALL_KEYS = WX_NUMERIC_KEYS + ["wind_direction"]

WEATHER_MAX_AGE_SEC = 1800
RATE_LIMIT_SEC = 45
MAX_REPLY = 180
RECONNECT_SEC = 15
CONFIG_REFRESH_SEC = 5

# defaults; overridden by config.json
CFG = {
    "bot_channel_idx": int(os.environ.get("SMS_BOT_CHANNEL_IDX", "-1")),
    "cmd_prefix": os.environ.get("SMS_CMD_PREFIX", "wx").lower(),
    "wx_channel_idx": int(os.environ.get("SMS_WX_CHANNEL_IDX", "-1")),
    "web_send_default_idx": int(os.environ.get("SMS_WEB_SEND_TARGET", "0") or "0"),
    "idle_poweroff_sec": int(os.environ.get("IDLE_POWEROFF_SEC", "0")),
    # weather station on the mesh: contact name or public-key prefix ("" = none)
    "wx_node": os.environ.get("SMS_WX_NODE", ""),
    "wx_poll_min": int(os.environ.get("SMS_WX_POLL_MIN", "10")),
    # wardrive coverage log (wardrive.py): listen-only logging, and the opt-in
    # zero-hop discover survey (repeaters answer at most 4 per 2 min, shared)
    "wardrive_log": int(os.environ.get("SMS_WARDRIVE_LOG", "1")),
    "wardrive_survey": int(os.environ.get("SMS_WARDRIVE_SURVEY", "0")),
    "wardrive_survey_sec": int(os.environ.get("SMS_WARDRIVE_SURVEY_SEC", "120")),
    "wardrive_survey_m": int(os.environ.get("SMS_WARDRIVE_SURVEY_M", "200")),
    "wardrive_max_gb": int(os.environ.get("SMS_WARDRIVE_MAX_GB", "32")),   # oldest rows go first
    # Fox aerial repeater (fox.py reads the MAVLink): "$fox" replies on #aerial / #bot,
    # and the optional auto-broadcast on #aerial
    "fox_bot": int(os.environ.get("SMS_FOX_BOT", "0")),       # off: no $fox replies, no auto posts
    "fox_auto": int(os.environ.get("SMS_FOX_AUTO", "0")),
    "fox_auto_sec": int(os.environ.get("SMS_FOX_AUTO_SEC", "120")),
    # flood scope for everything the radio floods; off by default (many meshes are unscoped)
    "region_scope_on": int(os.environ.get("SMS_REGION_SCOPE_ON", "0")),
    "region_scope": os.environ.get("SMS_REGION_SCOPE", ""),
}

_last_seen: dict[str, float] = {}
_last_activity = time.monotonic()
_last_rx_mono: float | None = None
_rx_count = 0
_last_rx_text = ""
_fw = ""
_node_name = ""
_self_prefix = ""
_mc: "MeshCore | None" = None
_conn: "sqlite3.Connection | None" = None
_wd: "wardrive.Wardrive | None" = None


def load_config() -> None:
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        for k in CFG:
            if k in data and data[k] is not None:
                CFG[k] = type(CFG[k])(data[k])
    except (OSError, ValueError, TypeError):
        pass


def save_config_key(key: str, value) -> None:
    """One setting from the screen into config.json, keeping the Config tab's others."""
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[key] = value
    CFG[key] = value
    try:
        with open(CONFIG_PATH + ".tmp", "w") as f:
            json.dump(data, f, indent=2)
        os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
    except OSError as e:
        print(f"[bot] config write failed: {e}", flush=True)


# ── storage ────────────────────────────────────────────────────────────────
def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rx (
               ts INTEGER NOT NULL, source TEXT, sender TEXT, channel_idx INTEGER,
               path_len INTEGER, snr REAL, rssi INTEGER, text TEXT
           )"""
    )
    if "peer" not in [r[1] for r in conn.execute("PRAGMA table_info(rx)")]:
        conn.execute("ALTER TABLE rx ADD COLUMN peer TEXT")   # DM / repeater thread key
    conn.execute(
        """CREATE TABLE IF NOT EXISTS outbox (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts INTEGER, target TEXT, text TEXT, sent INTEGER DEFAULT 0
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cmdq (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts INTEGER, kind TEXT, arg TEXT, done INTEGER DEFAULT 0, note TEXT
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wxlog (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL
           )"""
    )
    conn.commit()
    return conn


def log_row(source, sender, channel_idx, text, p=None, peer=None, count=True):
    """source: dm | chan<N> | tx (we sent) | cli (repeater reply) | clitx (our command).
    peer: 12-hex key prefix for DM and repeater threads. count=False keeps repeater
    console traffic out of the message counter (it would wake the screen)."""
    global _rx_count, _last_rx_text, _last_rx_mono
    p = p or {}
    if count:
        _rx_count += 1
        _last_rx_mono = time.monotonic()
        _last_rx_text = (text or "")[:80]
    _conn.execute(
        "INSERT INTO rx (ts,source,sender,channel_idx,path_len,snr,rssi,text,peer) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (int(time.time()), source, sender, channel_idx,
         p.get("path_len"), p.get("snr"), p.get("rssi"), text, (peer or "")[:12].lower() or None),
    )
    _conn.commit()


async def drain_outbox() -> None:
    rows = _conn.execute(
        "SELECT id, target, text FROM outbox WHERE sent=0 ORDER BY id LIMIT 5"
    ).fetchall()
    for row_id, target, text in rows:
        ok = False
        try:
            if str(target).startswith("@"):          # "@<key prefix>": a DM
                c = _mc.get_contact_by_key_prefix(target[1:].lower())
                if c is None:
                    raise ValueError(f"no contact {target[1:]}")
                res = await _mc.commands.send_msg(c, text[:MAX_REPLY])
                ok = res.type != EventType.ERROR
                if ok:
                    log_row("tx", "web", None, text, peer=c["public_key"])
            else:
                idx = int(target)
                res = await _mc.commands.send_chan_msg(idx, text[:MAX_REPLY])
                ok = res.type != EventType.ERROR
                if ok:
                    log_row("tx", "web", idx, text)
        except Exception as e:
            print(f"[bot] outbox send failed: {e}", flush=True)
        _conn.execute("UPDATE outbox SET sent=? WHERE id=?", (1 if ok else 2, row_id))
        _conn.commit()
        if ok:
            print(f"[bot] outbox -> {target}: {text!r}", flush=True)


UI_PORT = 5055               # lcd.py sends T114 screen keys here (joystick passthrough)
UI_KEYS = {"next", "prev", "enter", "select", "wake", "msgwake on", "msgwake off",
           "flip on", "flip off"}
GPS_KEYS = {"gps on": "1", "gps off": "0"}         # lcd Settings -> radio's "gps" custom var
SKEW_KEYS = {"gpsskew on": "gps skew on", "gpsskew off": "gps skew off"}   # gps-skew patch
SURVEY_KEYS = {"survey on": 1, "survey off": 0}     # lcd Settings -> wardrive discover survey
FOX_AUTO_KEYS = {"fox auto on": 1, "fox auto off": 0}   # lcd Fox menu -> #aerial auto-broadcast
FOX_BOT_KEYS = {"fox bot on": 1, "fox bot off": 0}      # lcd Fox menu -> the whole Fox bot
SCOPE_KEYS = {"scope on": 1, "scope off": 0}            # lcd Settings -> region scope
ADVPOS_KEYS = {"advpos on": 1, "advpos off": 0}         # lcd Settings -> position in adverts


class _UIKeys(asyncio.DatagramProtocol):
    """Forward joystick keys from lcd.py to the T114 as `ui <key>` (sms-uart-ui firmware)."""
    def datagram_received(self, data, addr):
        key = data.decode(errors="ignore").strip()
        if key == "wxping":
            asyncio.ensure_future(poll_weather(manual=True))
            return
        if key in ("wxchan", "wxdm"):
            asyncio.ensure_future(ask_weather(dm=key == "wxdm"))
            return
        if key in GPS_KEYS and _mc is not None:
            asyncio.ensure_future(set_gps(GPS_KEYS[key]))
            return
        if key in SKEW_KEYS and _mc is not None:
            asyncio.ensure_future(self._cli(SKEW_KEYS[key]))
            return
        if key in SURVEY_KEYS:
            save_config_key("wardrive_survey", SURVEY_KEYS[key])
            return
        if key in FOX_AUTO_KEYS:
            save_config_key("fox_auto", FOX_AUTO_KEYS[key])
            return
        if key in FOX_BOT_KEYS:
            save_config_key("fox_bot", FOX_BOT_KEYS[key])   # the main loop announces the switch-on
            return
        if key in ADVPOS_KEYS and _mc is not None:
            asyncio.ensure_future(set_adv_pos(ADVPOS_KEYS[key]))
            return
        if key in SCOPE_KEYS:
            save_config_key("region_scope_on", SCOPE_KEYS[key])
            return
        if key.startswith("scope set "):
            name = regions.normalize(key[10:])
            if name and regions.valid(name):
                save_config_key("region_scope", name)
            return
        if key == "fox send" and _mc is not None:
            asyncio.ensure_future(send_fox(fox_channels()[0], "screen"))
            return
        if (key in UI_KEYS or (key.startswith("bright ") and key[7:].isdigit())) and _mc is not None:
            asyncio.ensure_future(self._send(key))

    async def _send(self, key):
        await self._cli(f"ui {key}")

    async def _cli(self, cmd):
        try:
            await _mc.commands.run_cli_command(cmd)
        except Exception as e:
            print(f"[bot] {cmd} failed: {e}", flush=True)


# ── weather station telemetry (Cayenne LPP over the mesh) ───────────────────
# LPP channel conventions for the weather station (see docs/notes/weather.md):
#   temperature / humidity / barometer / illuminance / direction(wind) by type;
#   voltage / current / power = station battery;
#   analog input ch2 = wind mph, ch3 = rain rate in/h, ch4 = rain total in.
WX_ANALOG = {2: "wind_speed_mph", 3: "rain_rate_iph", 4: "rain_total_in"}
_COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
_wx = {"result": "", "try_at": 0.0, "ok_at": 0.0, "busy": False}


def lpp_to_weather(lpp) -> dict:
    d = {}
    for e in lpp or []:
        t, v, ch = e.get("type"), e.get("value"), e.get("channel")
        if isinstance(v, (list, tuple)) and len(v) == 1:
            v = v[0]
        if not isinstance(v, (int, float)) or ch == 1:
            continue    # ch 1 = the radio itself (its battery, MCU temperature), not the station
        if t == "temperature":
            d["temp_c"] = v
            d["temp_f"] = v * 9 / 5 + 32
        elif t == "humidity":
            d["humidity"] = v
        elif t == "barometer":
            d["pressure_hpa"] = v
            d["pressure_inhg"] = v * 0.0295300
        elif t == "illuminance":
            d["light_lux"] = v
        elif t == "voltage":
            d["battery_voltage"] = v
        elif t == "current":
            d["battery_current"] = v
        elif t == "power":
            d["battery_power"] = v
        elif t == "direction":
            d["wind_direction"] = _COMPASS16[int((v % 360) / 22.5 + 0.5) % 16]
        elif t in ("analog input", "generic sensor") and ch in WX_ANALOG:
            d[WX_ANALOG[ch]] = v
    if "temp_c" in d and "humidity" in d and d["humidity"] > 0:   # Magnus dew point
        import math
        a, b = 17.62, 243.12
        g = math.log(d["humidity"] / 100) + a * d["temp_c"] / (b + d["temp_c"])
        d["dewpoint_c"] = b * g / (a - g)
        d["dewpoint_f"] = d["dewpoint_c"] * 9 / 5 + 32
    return d


# ── the station bot's text reply, e.g.
#    "✈: 76F | 71%💧 | 💨 6MPH SW ☔ 0.00in | ⚡12.7V @12:27/6m | https://..."
# Until the station radio carries its sensors in telemetry, this is where the weather
# page's numbers come from.
STATION_TZ = ZoneInfo(os.environ.get("SMS_STATION_TZ") or os.environ.get("SMS_TZ", "UTC"))   # station stamps @HH:MM locally
_WX_TEXT = {
    "temp_f": re.compile(r"(-?\d+(?:\.\d+)?)\s*F\b"),
    "humidity": re.compile(r"(\d+(?:\.\d+)?)\s*%"),
    "wind_speed_mph": re.compile(r"(\d+(?:\.\d+)?)\s*MPH", re.I),
    "rain_total_in": re.compile(r"(\d+(?:\.\d+)?)\s*in\b"),
    "battery_voltage": re.compile(r"\u26a1\s*(\d+(?:\.\d+)?)\s*V"),
}
_WX_DIR = re.compile(r"MPH\s+([NSEW]{1,3})\b", re.I)
_WX_AT = re.compile(r"@(\d{1,2}):(\d{2})")


def parse_station_text(text: str):
    """Numbers out of a station summary line -> (weather dict, reading time), or None."""
    body = (text or "").strip()
    if ":" in body and not body.startswith("\u2708"):    # channel copy: "<sender>: ✈: ..."
        body = body.split(":", 1)[1].strip()
    if not body.startswith("\u2708") or "offline" in body or "no data" in body:
        return None
    body = body.split("http")[0]                          # the page link has nothing for us
    d = {}
    for key, rx in _WX_TEXT.items():
        m = rx.search(body)
        if m:
            d[key] = float(m.group(1))
    m = _WX_DIR.search(body)
    if m and m.group(1).upper() in _COMPASS16:
        d["wind_direction"] = m.group(1).upper()
    if "temp_f" not in d:
        return None
    d["temp_c"] = (d["temp_f"] - 32) * 5 / 9
    if d.get("humidity", 0) > 0:                          # Magnus dew point
        a, b = 17.62, 243.12
        g = math.log(d["humidity"] / 100) + a * d["temp_c"] / (b + d["temp_c"])
        d["dewpoint_c"] = b * g / (a - g)
        d["dewpoint_f"] = d["dewpoint_c"] * 9 / 5 + 32
    at = time.time()
    m = _WX_AT.search(body)
    if m:
        now = datetime.now(STATION_TZ)
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if t > now + timedelta(minutes=5):                # stamped before midnight
            t -= timedelta(days=1)
        at = t.timestamp()
    d["timestamp"] = at
    return d, int(at)


async def take_station_text(sender_name: str, prefix: str, text: str) -> bool:
    """A DM or channel line from the configured station: store its numbers. True if it was the station."""
    ref = CFG.get("wx_node", "").strip()
    if not ref:
        return False
    c = await _wx_contact(ref)
    key = ((c or {}).get("public_key") or ref).lower()
    name = (c or {}).get("adv_name") or ref
    if not ((prefix and key.startswith(prefix.lower())) or sender_name.strip() == name.strip()):
        return False
    parsed = parse_station_text(text)
    if parsed:
        data, at = parsed
        store_weather(data, recv_time=at)
        _wx["ok_at"] = time.time()
        _wx["result"] = "ok (text reply)"
        wx_log("text", f"text reply from {name}: {len(data)} values")
        print(f"[bot] weather from {name} text: {sorted(data)}", flush=True)
    return True


async def _wx_contact(ref: str):
    getter = getattr(_mc, "get_contact_by_name", None)
    for attempt in range(2):
        c = getter(ref) if getter else None
        if c is None and ref and all(ch in "0123456789abcdefABCDEF" for ch in ref):
            c = _mc.get_contact_by_key_prefix(ref.lower())
        if c is not None or attempt:
            return c
        await _mc.commands.get_contacts()


def wx_log(kind: str, text: str) -> None:
    """Weather events for the web Weather tab. kind: ok | text | ask | warn. Newest 200 kept."""
    if _conn is None:
        return
    try:
        _conn.execute("INSERT INTO wxlog (ts, kind, text) VALUES (?, ?, ?)", (int(time.time()), kind, text))
        _conn.execute("DELETE FROM wxlog WHERE id <= (SELECT MAX(id) FROM wxlog) - 200")
        _conn.commit()
    except sqlite3.Error:
        pass


async def poll_weather(manual: bool = False) -> None:
    """Ask the weather station for its telemetry and cache it (readings schema)."""
    ref = CFG.get("wx_node", "").strip()
    if _wx["busy"] or _mc is None:
        return
    _wx["try_at"] = time.time()
    if not ref:
        _wx["result"] = "no station set"
        return
    _wx["busy"] = True
    try:
        c = await _wx_contact(ref)
        if c is None:
            _wx["result"] = f"'{ref}' not in contacts"
            return
        lpp = await _mc.commands.req_telemetry_sync(c, min_timeout=10)
        if not lpp:
            _wx["result"] = "no reply"
            return
        data = lpp_to_weather(lpp)
        if not data:
            _wx["result"] = "reply had no weather"
            return
        data["timestamp"] = time.time()
        store_weather(data)
        _wx["ok_at"] = time.time()
        _wx["result"] = "ok"
        wx_log("ok", f"telemetry from {c.get('adv_name') or ref[:12]}: {len(data) - 1} values")
        print(f"[bot] weather from {ref}: {sorted(data)}", flush=True)
    except Exception as e:
        _wx["result"] = f"error: {e}"[:60]
    finally:
        _wx["busy"] = False
        if _wx["result"] != "ok":
            wx_log("warn", f"poll{' (manual)' if manual else ''}: {_wx['result']}")
        if manual or _wx["result"] != "ok":
            print(f"[bot] weather poll ({'manual' if manual else 'auto'}): {_wx['result']}", flush=True)


async def ask_weather(dm: bool, src: str = "screen") -> None:
    """Weather screen up/down: '<prefix>' on the weather channel, or as a DM to the
    station (floods until the station's reply teaches the radio a path, then direct)."""
    text = CFG.get("cmd_prefix", "wx")     # the station bot answers its own word, nothing else
    try:
        if not dm:
            idx = CFG["wx_channel_idx"]
            res = await _mc.commands.send_chan_msg(idx, text)
            ok = res.type != EventType.ERROR
            _wx["result"] = f"asked on {_chan_names.get(str(idx), f'ch{idx}')}" if ok else "channel send failed"
            if ok:
                log_row("tx", src, idx, text)
            return
        c = await _wx_contact(CFG.get("wx_node", "").strip())
        if c is None:
            _wx["result"] = "station not in contacts"
            return
        hops = c.get("out_path_len", -1)
        res = await _mc.commands.send_msg(c, text)
        ok = res.type != EventType.ERROR
        route = "flood" if hops is None or hops < 0 else f"direct {hops} hop{'s' if hops != 1 else ''}"
        _wx["result"] = f"DM sent ({route})" if ok else "DM failed"
        if ok:
            log_row("tx", src, None, text, peer=c.get("public_key"))
    except Exception as e:
        _wx["result"] = f"error: {e}"[:60]
    finally:
        wx_log("ask" if _wx["result"].startswith(("asked", "DM sent")) else "warn", _wx["result"])
        print(f"[bot] weather ask ({'dm' if dm else 'channel'}): {_wx['result']}", flush=True)


# ── own position (GPS on the radio, else the advert location) ───────────────
_pos = {"lat": None, "lon": None, "src": "", "at": 0.0}
PATH_RESET_KM = 1.609        # moved this far since paths were learned: flood again
_path_anchor = {"lat": None, "lon": None}


def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


async def reset_paths_if_moved() -> None:
    """A mobile node's learned routes go stale as it drives: after a mile, forget them
    so DMs and requests flood until the contacts answer and teach fresh paths."""
    if _pos["src"] != "gps" or _pos["lat"] is None:
        return
    if _path_anchor["lat"] is None:
        _path_anchor.update(lat=_pos["lat"], lon=_pos["lon"])
        return
    km = _km(_path_anchor["lat"], _path_anchor["lon"], _pos["lat"], _pos["lon"])
    if km < PATH_RESET_KM:
        return
    n = 0
    for c in list((getattr(_mc, "contacts", None) or {}).values()):
        if isinstance(c, dict) and (c.get("out_path_len") or -1) >= 0:
            try:
                await _mc.commands.reset_path(c["public_key"])
                n += 1
            except Exception:
                pass
    _path_anchor.update(lat=_pos["lat"], lon=_pos["lon"])
    print(f"[bot] moved {km:.1f} km: reset {n} direct paths to flood", flush=True)


_gps = {"on": None,         # radio's "gps" custom var: True/False, None = unknown / no GPS
        "fix": None,        # locked (gps-fix patch, "gps fix"): True/False, None = unknown
        "sats": None, "age": None}
GPS_FIX_SEC = 10


async def refresh_gps_state() -> None:
    try:
        res = await _mc.commands.get_custom_vars()
        v = (res.payload or {}).get("gps") if res.type != EventType.ERROR else None
        _gps["on"] = None if v is None else v == "1"
    except Exception:
        pass


async def set_gps(value: str) -> None:
    """GPS on/off from the lcd Settings menu. The radio saves it in its own prefs."""
    try:
        res = await _mc.commands.set_custom_var("gps", value)
        if res.type == EventType.ERROR:
            print(f"[bot] gps {value} refused: {res.payload}", flush=True)
    except Exception as e:
        print(f"[bot] gps {value} failed: {e}", flush=True)
    await refresh_gps_state()


_adv = {"loc": None}        # radio's advert position policy: 1 = adverts carry our position


async def refresh_adv_loc() -> None:
    try:
        res = await _mc.commands.send_appstart()
        v = None if res.type == EventType.ERROR else (res.payload or {}).get("adv_loc_policy")
        _adv["loc"] = None if v is None else int(bool(v))
    except Exception:
        _adv["loc"] = None


async def set_radio_name(name: str) -> str:
    """Rename the radio (web Config tab). The mesh sees it from the next advert."""
    global _node_name
    name = name.strip()
    if (err := names.check_radio_name(name)):
        return err
    res = await _mc.commands.set_name(name)
    if res.type == EventType.ERROR:
        return f"refused: {res.payload}"
    _node_name = name
    if _wd is not None:
        _wd.my_name = name
    return f"radio renamed to {name!r}"


async def set_adv_pos(on: int) -> bool:
    """Position in adverts on/off from the screen or web. The radio saves it in its own prefs."""
    try:
        res = await _mc.commands.set_advert_loc_policy(on)
        if res.type == EventType.ERROR:
            print(f"[bot] advert position refused: {res.payload}", flush=True)
            return False
    except Exception as e:
        print(f"[bot] advert position failed: {e}", flush=True)
        return False
    await refresh_adv_loc()
    return True


async def refresh_gps_fix() -> None:
    """Lock state from "gps fix" ("> fix 1 sats 12 age 0"); older firmware leaves it unknown."""
    fix = sats = age = None
    if _gps["on"]:
        try:
            res = await _mc.commands.run_cli_command("gps fix")
            text = str((res.payload or {}).get("text", "")) if isinstance(res.payload, dict) else ""
            m = re.search(r"fix (\d) sats (\d+) age (-?\d+)", text)
            if res.type != EventType.ERROR and m:
                fix, sats, age = m.group(1) == "1", int(m.group(2)), int(m.group(3))
        except Exception:
            pass
    _gps.update(fix=fix, sats=sats, age=age)


def wardrive_pos():
    """A fresh GPS position for the coverage log, else None."""
    if _pos["src"] != "gps" or _pos["lat"] is None or _gps["fix"] is False:
        return None
    if time.time() - _pos["at"] > wardrive.POS_MAX_AGE:
        return None
    return _pos["lat"], _pos["lon"]


async def refresh_position() -> None:
    try:
        res = await _mc.commands.get_self_telemetry()
        for e in (res.payload or {}).get("lpp", []) if res.type != EventType.ERROR else []:
            if e.get("type") == "gps" and isinstance(e.get("value"), dict):
                v = e["value"]
                if v.get("latitude") or v.get("longitude"):
                    _pos.update(lat=v["latitude"], lon=v["longitude"], src="gps", at=time.time())
                    return
    except Exception:
        pass
    si = getattr(_mc, "self_info", None) or {}
    if si.get("adv_lat") or si.get("adv_lon"):
        _pos.update(lat=si["adv_lat"], lon=si["adv_lon"], src="advert", at=time.time())


RADIO_TIME_SEC = 30          # how often to read the radio's (GPS-synced) clock
_radio_time = None           # radio epoch seconds at _radio_time_at (Pi time.time())
_radio_time_at = 0.0
_clock_warned = False


def _pi_ntp_synced() -> bool:
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "yes"
    except Exception:
        return False


async def refresh_radio_time() -> None:
    """Read the T114's clock (set from the GPS) for the screen; with no internet time,
    also set the Pi's clock from it."""
    global _radio_time, _radio_time_at, _clock_warned
    try:
        res = await _mc.commands.get_time()
    except Exception as e:
        print(f"[bot] get_time failed: {e}", flush=True)
        return
    t = (res.payload or {}).get("time") if res.type != EventType.ERROR else None
    if t and abs(t - time.time()) > 60 and _pi_ntp_synced():
        # radio clock wrong (e.g. pushed forward by a bad clock on the mesh, no GPS fix
        # yet): correct it from the Pi's internet time ("time force" = sms-uart-ui fw)
        try:
            r = await _mc.commands.run_cli_command(f"time force {int(time.time())}")
            print(f"[bot] radio clock {t} corrected from Pi time: {r.payload}", flush=True)
            t = int(time.time())
        except Exception as e:
            print(f"[bot] time force failed: {e}", flush=True)
    if not t or not 1_700_000_000 < t < 2_000_000_000:   # no fix / never set / garbage
        if t and not _clock_warned:
            _clock_warned = True
            print(f"[bot] radio clock looks wrong ({t}); using the Pi's clock", flush=True)
        _radio_time = None
        return
    _radio_time, _radio_time_at = t, time.time()
    if abs(t - time.time()) > 120 and not _pi_ntp_synced():
        r = subprocess.run(["sudo", "/usr/bin/date", "-s", f"@{int(t)}"], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"[bot] Pi clock set from radio/GPS time", flush=True)
        elif not _clock_warned:
            _clock_warned = True
            print(f"[bot] could not set Pi clock: {r.stderr.strip()}", flush=True)


def write_status() -> None:
    try:
        with open(STATUS_PATH + ".tmp", "w") as f:
            json.dump({
                "ts": int(time.time()),
                "rx_count": _rx_count,
                "last_rx_age": (None if _last_rx_mono is None
                                else int(time.monotonic() - _last_rx_mono)),
                "last_rx_text": _last_rx_text,
                "fw": _fw,
                "node_name": _node_name,
                "wx_node": CFG.get("wx_node", ""),
                "wx_result": _wx["result"],
                "wx_try_at": int(_wx["try_at"]),
                "wx_ok_at": int(_wx["ok_at"]),
                "lat": _pos["lat"], "lon": _pos["lon"], "pos_src": _pos["src"],
                "gps_on": _gps["on"],
                "region_scope": _scope["applied"] or "",
                "adv_loc": _adv["loc"],
                "gps_fix": _gps["fix"], "gps_sats": _gps["sats"], "gps_age": _gps["age"],
                "wardrive": (_wd.status(bool(CFG["wardrive_survey"])) if _wd else None),
                "radio_time": (None if _radio_time is None
                               else int(_radio_time + time.time() - _radio_time_at)),
                "cfg": CFG,
            }, f)
        os.replace(STATUS_PATH + ".tmp", STATUS_PATH)   # atomic: readers never see a half file
    except OSError:
        pass


MAX_CHANNEL_SLOTS = 40
_chan_names: dict = {}


async def write_channels_json() -> None:
    chans = {}
    try:
        res = await _mc.commands.get_channels()
        payload = getattr(res, "payload", None)
        if isinstance(payload, dict):
            for k, v in payload.items():
                name = v.get("channel_name") if isinstance(v, dict) else str(v)
                if name:
                    chans[str(k)] = name
    except Exception:
        pass
    if not chans:  # fallback: probe every slot (companion builds have up to 40)
        for i in range(MAX_CHANNEL_SLOTS):
            r = None
            for _try in range(2):         # a busy link can time a slot out: one retry
                try:
                    r = await _mc.commands.get_channel(i)
                except Exception:
                    r = None
                if r is not None and getattr(r, "type", None) != EventType.ERROR:
                    break
            if r is None or getattr(r, "type", None) == EventType.ERROR:
                break                     # past the last slot this firmware has
            nm = (r.payload or {}).get("channel_name")
            if nm:
                chans[str(i)] = nm
    _chan_names.clear()
    _chan_names.update(chans)
    try:
        with open(CHANNELS_PATH, "w") as f:
            json.dump(chans, f)
    except OSError:
        pass


async def write_contacts_json() -> None:
    out = []
    try:
        res = await _mc.commands.get_contacts(timeout=30)   # 240+ contacts over UART take > 5 s
        for _k, v in (getattr(res, "payload", {}) or {}).items():
            if isinstance(v, dict):
                out.append({
                    "name": v.get("adv_name", ""),
                    "public_key": v.get("public_key", ""),
                    "type": v.get("type", 1),
                    "last_advert": v.get("last_advert") or v.get("lastmod"),
                    "out_path_len": v.get("out_path_len"),
                    "lat": v.get("adv_lat") or None,
                    "lon": v.get("adv_lon") or None,
                })
    except Exception:
        return
    if not out:          # timed out / error: keep the last good list rather than blanking it
        return
    try:
        with open(CONTACTS_PATH, "w") as f:
            json.dump(out, f)
    except OSError:
        pass


async def drain_cmdq() -> None:
    """One-shot actions queued by the web Contacts tab."""
    rows = _conn.execute(
        "SELECT id, kind, arg FROM cmdq WHERE done=0 ORDER BY id LIMIT 3"
    ).fetchall()
    for rid, kind, arg in rows:
        note = ""
        try:
            if kind == "add_contact":
                d = json.loads(arg)
                await _mc.commands.add_contact({
                    "public_key": d["public_key"],
                    "adv_name": d.get("name", d["public_key"][:6]),
                    "type": int(d.get("type", 1)),
                    # no route known yet: flood until the contact answers
                    "out_path": "", "out_path_len": -1, "out_path_hash_mode": 0,
                    "flags": 0, "last_advert": 0, "adv_lat": 0.0, "adv_lon": 0.0,
                })
                await write_contacts_json()
                note = f"added {d.get('name', '')}"
            elif kind == "refresh_contacts":
                await _mc.commands.get_contacts()
                await write_contacts_json()
                note = "refreshed"
            elif kind == "flood_advert":
                await _mc.commands.send_advert(flood=True)
                note = "flood advert sent"
            elif kind == "advert":
                await _mc.commands.send_advert()
                note = "zero-hop advert sent"
            elif kind == "wx_ask":
                await ask_weather(dm=False, src="web")
                note = _wx["result"]
            elif kind == "set_name":
                note = await set_radio_name(arg)
            elif kind == "advert_pos":
                on = int(arg == "1")
                note = f"advert position {'on' if on else 'off'}" if await set_adv_pos(on) else "refused"
            elif kind.startswith("rpt_"):
                note = await repeater_action(kind, json.loads(arg or "{}"))
                if kind == "rpt_login":     # never leave the password sitting in the queue
                    _conn.execute("UPDATE cmdq SET arg='' WHERE id=?", (rid,))
        except Exception as e:
            note = f"{kind} failed: {e}"
        print(f"[bot] cmdq {kind}: {note}", flush=True)
        _conn.execute("UPDATE cmdq SET done=1, note=? WHERE id=?", (note[:200], rid))
        _conn.commit()


async def repeater_action(kind: str, d: dict) -> str:
    """Repeater admin from the web Messages tab: login / logout / status / CLI command.
    Results land in rx as 'cli' rows on the repeater's thread."""
    key = (d.get("key") or "").lower()
    c = _mc.get_contact_by_key_prefix(key[:12]) if key else None
    if c is None:
        return f"no contact {key[:12]}"
    name, peer = c.get("adv_name", key[:6]), c["public_key"]

    def say(text, src="cli"):
        log_row(src, name if src == "cli" else "me", None, text, peer=peer, count=False)

    if kind == "rpt_login":
        route = "flood" if (c.get("out_path_len", -1) or 0) < 0 else f"direct, {c['out_path_len']} hops"
        say(f"login… ({route})", "clitx")
        # wait for success OR failure (send_login_sync only listens for success, so a
        # wrong password and silence looked the same)
        waits = [asyncio.ensure_future(_mc.dispatcher.wait_for_event(t, timeout=30))
                 for t in (EventType.LOGIN_SUCCESS, EventType.LOGIN_FAILED)]
        sent = await _mc.commands._send_login_raw(c, d.get("pwd", ""))
        if sent is None or sent.type == EventType.ERROR:
            for w in waits:
                w.cancel()
            say(f"radio refused the login: {getattr(sent, 'payload', '')}")
            return "login not sent"
        done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        r = next(iter(done)).result()
        if r is None and pending:                  # first one timed out; give the other a beat
            done2, pending = await asyncio.wait(pending, timeout=1)
            r = next(iter(done2)).result() if done2 else None
        for w in pending:
            w.cancel()
        if r is not None and r.type == EventType.LOGIN_SUCCESS:
            perms = (r.payload or {}).get("acl_permissions")
            say("logged in" + (" (admin)" if (r.payload or {}).get("is_admin") or (perms or 0) & 3 == 3 else ""))
            return "login ok"
        if r is not None and r.type == EventType.LOGIN_FAILED:
            say("login refused by the repeater (wrong password)")
            return "login refused"
        say("no reply to the login in 30 s - checking whether the repeater answers at all…")
        p = await _mc.commands.send_path_discovery_sync(c, min_timeout=20)
        if p is None:
            say("no answer to path discovery either: the repeater isn't hearing us, or we "
                "aren't hearing it (range, or the repeater is down)")
        else:
            say(f"repeater answers path discovery ({(p.payload or {}).get('out_path_len', '?')} hops) "
                "but stayed silent on the login: a wrong password, or it once saw a newer timestamp "
                "from this node (a wrong clock) - clear that with "
                f"'setperm {_self_prefix[:16] or '<our key>'} 0' on the repeater from another login")
        return "login: no reply"
    if kind == "rpt_logout":
        await _mc.commands.send_logout(c)
        say("logged out")
        return "logout"
    if kind == "rpt_status":
        say("status…", "clitx")
        r = await _mc.commands.req_status_sync(c, min_timeout=20)
        if not r:
            say("no status reply")
            return "status: no reply"
        st = r.payload if hasattr(r, "payload") else r
        st = st if isinstance(st, dict) else {}
        keep = [(k, st[k]) for k in ("bat", "uptime", "noise_floor", "last_rssi", "last_snr",
                                     "nb_recv", "nb_sent", "airtime", "rx_airtime",
                                     "sent_flood", "sent_direct", "recv_flood", "recv_direct",
                                     "full_evts", "direct_dups", "flood_dups", "tx_queue_len")
                if k in st]
        if "bat" in st:
            keep[0] = ("battery", f"{st['bat'] / 1000:.2f} V")
        if "uptime" in st:
            u = int(st["uptime"])
            keep = [(k, f"{u // 86400}d {u % 86400 // 3600}h {u % 3600 // 60}m" if k == "uptime" else v)
                    for k, v in keep]
        say("\n".join(f"{k}: {v}" for k, v in keep) or str(st)[:300])
        return "status ok"
    if kind == "rpt_cmd":
        cmd = (d.get("cmd") or "").strip()[:120]
        if not cmd:
            return "empty command"
        say(cmd, "clitx")
        r = await _mc.commands.send_cmd(c, cmd)
        if r.type == EventType.ERROR:
            say(f"send failed: {r.payload}")
            return "cmd failed"
        return f"cmd sent: {cmd[:40]}"     # the reply arrives later as a CLI message
    return f"unknown {kind}"


def backfill_dm_peers() -> None:
    """DM rows logged before rx.peer existed: attach them to their contact by name."""
    for c in (getattr(_mc, "contacts", None) or {}).values():
        if isinstance(c, dict) and c.get("adv_name") and c.get("public_key"):
            _conn.execute("UPDATE rx SET peer=? WHERE source='dm' AND peer IS NULL AND sender=?",
                          (c["public_key"][:12].lower(), c["adv_name"]))
    # unresolved senders were logged as their key prefix
    _conn.execute("UPDATE rx SET peer=lower(sender) WHERE source='dm' AND peer IS NULL "
                  "AND length(sender)=12 AND lower(sender) GLOB '[0-9a-f]*'")
    _conn.commit()


# ── weather cache (readings schema) ────────────────────────────────────
def open_weather_db():
    if not WEATHER_DB:
        return None
    os.makedirs(os.path.dirname(WEATHER_DB) or ".", exist_ok=True)
    cols = ", ".join(f'"{k}" REAL' for k in WX_NUMERIC_KEYS)
    c = sqlite3.connect(WEATHER_DB, timeout=5)
    c.execute(f"CREATE TABLE IF NOT EXISTS readings ("
              f"recv_time INTEGER PRIMARY KEY, {cols}, wind_direction TEXT)")
    c.commit()
    return c


def store_weather(data: dict, recv_time: int | None = None) -> None:
    c = open_weather_db()
    if c is None:
        return
    row = {k: data.get(k) for k in WX_ALL_KEYS}
    row["recv_time"] = int(recv_time or time.time())
    ph = ", ".join(f":{k}" for k in ["recv_time"] + WX_ALL_KEYS)
    c.execute(f"INSERT OR REPLACE INTO readings (recv_time, {', '.join(WX_ALL_KEYS)}) "
              f"VALUES ({ph})", row)
    c.execute("DELETE FROM readings WHERE recv_time < ?", (int(time.time() - 14 * 86400),))
    c.commit()
    c.close()


def latest_weather() -> str:
    c = None
    try:
        if not WEATHER_DB or not os.path.exists(WEATHER_DB):
            return "wx: no source"
        c = sqlite3.connect(f"file:{WEATHER_DB}?mode=ro", uri=True, timeout=5)
        r = c.execute(
            "SELECT recv_time, temp_f, humidity, wind_speed_mph, wind_direction, "
            "rain_total_in, pressure_inhg FROM readings ORDER BY recv_time DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return "wx: no source"
    finally:
        if c is not None:
            c.close()
    if not r:
        return "wx: no data"
    age = int(time.time()) - int(r[0])
    if age > WEATHER_MAX_AGE_SEC:
        return f"wx: stale {age // 60}m"
    _rt, tf, rh, ws, wd, rain, inhg = r
    p = []
    if tf is not None:
        p.append(f"{tf:.0f}F")
    if rh is not None:
        p.append(f"{rh:.0f}%rh")
    if ws is not None:
        p.append(f"wind {ws:.0f}" + (f" {wd}" if wd else ""))
    if rain is not None:
        p.append(f"rain {rain:.2f}in")
    if inhg is not None:
        p.append(f"{inhg:.2f}inHg")
    return (f"{CFG.get('cmd_prefix', 'wx')}: " + " ".join(p))[:MAX_REPLY]


# ── Fox aerial repeater ─────────────────────────────────────────────────────
FOX_TRIGGER = "$fox"
AERIAL_CHANNEL = "#aerial"


def _chan_idx(name: str):
    for k, v in _chan_names.items():
        if v.strip().lower() == name:
            return int(k)
    return None


def fox_channels() -> tuple:
    """(#aerial index or None, {indices '$fox' is answered on}: #aerial, #bot, the bot channel)."""
    aerial = _chan_idx(AERIAL_CHANNEL)
    answer = {i for i in (aerial, _chan_idx("#bot"), CFG["bot_channel_idx"]) if i is not None and i >= 0}
    return aerial, answer


async def _slot_empty(i: int):
    """True / False from the radio itself for one slot; None if it didn't answer cleanly."""
    try:
        r = await _mc.commands.get_channel(i)
    except Exception:
        return None
    if getattr(r, "type", None) != EventType.CHANNEL_INFO:
        return None
    return not (r.payload or {}).get("channel_name")


async def ensure_aerial_channel() -> None:
    """Load #aerial (name-only hashtag channel) into a free slot if the radio lacks it.

    channels.json can be short (a slot read that timed out ends the probe), so every
    candidate slot is re-read and only written when the radio itself says it's empty;
    any doubt and nothing is written."""
    if _chan_idx(AERIAL_CHANNEL) is not None or not _chan_names:
        return
    free = None
    for i in range(MAX_CHANNEL_SLOTS):
        if str(i) in _chan_names:
            continue
        empty = await _slot_empty(i)
        if empty is None:
            print(f"[bot] slot {i} didn't answer; not adding #aerial this run", flush=True)
            return
        if empty:
            free = i
            break
        await write_channels_json()          # the list was short: re-read it before going on
        if _chan_idx(AERIAL_CHANNEL) is not None:
            return
    if free is None:
        print("[bot] no free channel slot for #aerial", flush=True)
        return
    try:
        res = await _mc.commands.set_channel(free, AERIAL_CHANNEL)
        if res.type == EventType.ERROR:
            print(f"[bot] adding #aerial in slot {free} refused: {res.payload}", flush=True)
            return
    except Exception as e:
        print(f"[bot] adding #aerial failed: {e}", flush=True)
        return
    print(f"[bot] added #aerial in slot {free}", flush=True)
    await write_channels_json()


_scope = {"applied": None, "retry": 0.0}    # applied: scope last confirmed on the radio ("" = none)


def want_scope() -> str:
    name = regions.normalize(CFG["region_scope"])
    return name if CFG["region_scope_on"] and regions.valid(name) else ""


async def sync_region_scope() -> None:
    """Hold the radio's default flood scope at the configured one. While never enabled
    this process, off leaves the radio alone (a phone app may own its scope)."""
    want, done = want_scope(), _scope["applied"]
    if want == done or (done is None and not want) or time.monotonic() < _scope["retry"]:
        return
    try:
        res = await _mc.commands.get_default_flood_scope()
        cur = None if res.type == EventType.ERROR else (res.payload or {}).get("scope_name", "").lstrip("#")
        if cur != want:
            if want:
                res = await _mc.commands.set_default_flood_scope(want)
            else:   # the library's reset frame is rejected by the firmware; a bare command clears
                res = await _mc.commands.send(bytes([63]), [EventType.OK, EventType.ERROR])
            if res.type == EventType.ERROR:
                raise RuntimeError(res.payload)
            print(f"[bot] region scope: {want or 'off'}", flush=True)
        _scope["applied"] = want
    except Exception as e:
        _scope["retry"] = time.monotonic() + 60
        print(f"[bot] region scope {want or 'off'} failed: {e}", flush=True)


FOX_ONLINE = "Great Fox Aerial Repeater Bot Online, use $fox for telemetry updates."
_fox_bot_was = None          # fox_bot as last seen; off -> on posts FOX_ONLINE (set once per process)


async def fox_bot_switched() -> None:
    """Announce on #aerial when the Fox bot is switched on (screen or web), never on a restart."""
    global _fox_bot_was
    on = bool(CFG["fox_bot"])
    if _fox_bot_was is None:
        _fox_bot_was = on
        return
    if on and not _fox_bot_was:
        await send_fox(fox_channels()[0], "online", FOX_ONLINE)
    _fox_bot_was = on


async def send_fox(idx, why: str, text: str = "") -> bool:
    if idx is None:
        print(f"[bot] fox ({why}): no #aerial channel on the radio", flush=True)
        return False
    text = (text or fox.fox_text(fox.load(FOX_JSON)))[:MAX_REPLY]
    try:
        res = await _mc.commands.send_chan_msg(idx, text)
    except Exception as e:
        print(f"[bot] fox ({why}) send failed: {e}", flush=True)
        return False
    if res.type == EventType.ERROR:
        print(f"[bot] fox ({why}) send failed: {res.payload}", flush=True)
        return False
    log_row("tx", why, idx, text)
    print(f"[bot] fox ({why}) -> {_chan_names.get(str(idx), idx)}: {text!r}", flush=True)
    return True


_fox_stale_logged = False


async def fox_auto_broadcast() -> None:
    """Timed #aerial post; skipped while the Mission Planner link is down (no 'no telemetry' spam)."""
    global _fox_stale_logged
    if not fox.fresh(fox.load(FOX_JSON)):
        if not _fox_stale_logged:
            _fox_stale_logged = True
            print("[bot] fox auto: no fresh telemetry, holding broadcasts", flush=True)
        return
    _fox_stale_logged = False
    await send_fox(fox_channels()[0], "auto")


# ── command handling ───────────────────────────────────────────────────────
def rate_limited(sender: str) -> bool:
    now = time.monotonic()
    if now - _last_seen.get(sender, 0.0) < RATE_LIMIT_SEC:
        return True
    _last_seen[sender] = now
    return False


def dispatch(text: str, sender: str, is_dm: bool) -> str | None:
    prefix = CFG["cmd_prefix"]
    low = (text or "").strip().lower()
    if not low:
        return None
    if low.startswith(prefix + "-"):
        low = prefix + " " + low[len(prefix) + 1:]
    if low == prefix:
        rest = "wx"
    elif low.startswith(prefix + " "):
        rest = low[len(prefix) + 1:].strip() or "wx"
    elif is_dm:
        rest = low
    else:
        return None
    if rate_limited(sender):
        return None
    cmd = rest.split()[0]
    if cmd in ("wx", "weather"):
        return latest_weather()[:MAX_REPLY]
    if cmd in ("help", "about", "?"):
        return f"Weather bot - channel: '{prefix} wx'  |  DM: 'wx'"
    return None


def _bump() -> None:
    global _last_activity
    _last_activity = time.monotonic()


async def _resolve_contact(prefix: str):
    if not prefix:
        return None
    c = _mc.get_contact_by_key_prefix(prefix)
    if c is None:
        await _mc.commands.get_contacts()
        c = _mc.get_contact_by_key_prefix(prefix)
    return c


def _is_self(prefix: str, sender_name: str) -> bool:
    if _self_prefix and prefix and prefix.startswith(_self_prefix[:len(prefix)]):
        return True
    return bool(_node_name) and sender_name.strip() == _node_name.strip()


async def on_dm(event) -> None:
    p = event.payload or {}
    _bump()
    prefix = p.get("pubkey_prefix", "")
    text = p.get("text", "")
    contact = await _resolve_contact(prefix)
    name = (contact or {}).get("adv_name") or prefix or "?"
    if _is_self(prefix, name):
        return
    if p.get("txt_type") == 1:      # CLI reply from a repeater we're administering
        log_row("cli", name, None, text, p, peer=prefix, count=False)
        return
    log_row("dm", name, None, text, p, peer=prefix)
    if await take_station_text(name, prefix, text):
        return      # never answer the station: its own reply could trigger it again
    reply = dispatch(text, sender=prefix or "dm", is_dm=True)
    print(f"[bot] (DM) {name}: {text!r} -> {reply!r}", flush=True)
    if not reply or contact is None:
        return
    res = await _mc.commands.send_msg(contact, reply)
    if res.type == EventType.ERROR:
        print(f"[bot] dm reply failed: {res.payload}", flush=True)


async def on_chan(event) -> None:
    p = event.payload or {}
    _bump()
    idx = p.get("channel_idx")
    raw = (p.get("text") or "").strip()
    sender, _, body = raw.partition(":") if ":" in raw else ("?", "", raw)
    sender, body = sender.strip(), body.strip()
    if _is_self(p.get("pubkey_prefix", ""), sender):
        return
    log_row(f"chan{idx}", sender, idx, raw, p)
    if await take_station_text(sender, "", body):
        return
    if idx is not None and str(idx) not in _chan_names:
        asyncio.create_task(write_channels_json())   # channel added since startup: pick up its name
    low = body.lower()
    if (CFG["fox_bot"] and (low == FOX_TRIGGER or low.startswith(FOX_TRIGGER + " "))
            and idx in fox_channels()[1]):
        if not rate_limited("fox:" + (sender or "chan")):
            await send_fox(idx, "fox")
        return
    if idx != CFG["bot_channel_idx"]:
        return
    reply = dispatch(body, sender=sender or "chan", is_dm=False)
    if not reply:
        return
    res = await _mc.commands.send_chan_msg(idx, reply)
    if res.type == EventType.ERROR:
        print(f"[bot] chan reply failed: {res.payload}", flush=True)


# ── host-fed battery ───────────────────────────────────────────────────────
_ext_batt_warned = False


async def push_ext_batt() -> None:
    """Send the pack voltage to the T114 as `set ext.batt <mV> [chg]`.

    No power.json (sms-power off) or a stale one -> send nothing; the T114 drops
    back to its own ADC 5 min after the last push. Stock firmware answers
    "Unknown command" - logged once, otherwise harmless.
    """
    global _ext_batt_warned
    try:
        with open(POWER_JSON) as f:
            p = json.load(f)
        if time.time() - float(p["ts"]) > EXT_BATT_MAX_AGE:
            return
        mv = int(float(p["volts"]) * 1000 / max(BATT_CELLS, 1))
        chg = float(p.get("milliamps", 0)) < BATT_CHG_MA
    except (OSError, ValueError, KeyError, TypeError):
        return
    if not hasattr(_mc.commands, "run_cli_command"):
        if not _ext_batt_warned:
            _ext_batt_warned = True
            print("[bot] meshcore lib too old for run_cli_command (need >= 2.3.16)", flush=True)
        return
    cmd = f"set ext.batt {mv}" + (" chg" if chg else "")
    try:
        res = await _mc.commands.run_cli_command(cmd)
    except Exception as e:
        print(f"[bot] ext.batt push failed: {e}", flush=True)
        return
    reply = str((res.payload or {}).get("text", "")) if isinstance(res.payload, dict) else str(res.payload)
    if (res.type == EventType.ERROR or "OK" not in reply) and not _ext_batt_warned:
        _ext_batt_warned = True
        print(f"[bot] radio rejected ext.batt ({reply!r}) - "
              "flash the sms-batt T114 build to mirror the Pi battery", flush=True)


# ── lifecycle ──────────────────────────────────────────────────────────────
async def serve(conn: sqlite3.Connection) -> None:
    global _mc, _conn, _fw, _node_name, _self_prefix, _wd
    _conn = conn
    if _wd is None:
        wardrive.open_table(conn)
        _wd = wardrive.Wardrive(conn, wardrive_pos)
    mc = await (MeshCore.create_tcp("127.0.0.1", MUX_LOCAL_PORT) if MUX
                else MeshCore.create_serial(SERIAL, BAUD))
    if mc is None:
        raise ConnectionError("radio link not up")
    _mc = mc
    try:
        info = await mc.commands.send_device_query()
        ip = getattr(info, "payload", {}) or {}
        _fw = str(ip.get("ver") or ip.get("fw_build") or "")
        si = getattr(mc, "self_info", None) or {}
        _node_name = si.get("name", "") or ip.get("name", "")
        _self_prefix = (si.get("public_key", "") or "")[:12]
        _wd.my_name = _node_name
        await mc.commands.get_contacts()
        await write_channels_json()
        await ensure_aerial_channel()
        await write_contacts_json()
        backfill_dm_peers()
        await mc.start_auto_message_fetching()
        subs = [
            mc.subscribe(EventType.CONTACT_MSG_RECV, on_dm),
            mc.subscribe(EventType.CHANNEL_MSG_RECV, on_chan),
            mc.subscribe(EventType.RX_LOG_DATA,
                         lambda e: _wd.on_rx(e) if CFG["wardrive_log"] else None),
            mc.subscribe(EventType.DISCOVER_RESPONSE, _wd.on_discover),
        ]
        try:
            await mc.commands.send_advert()
        except Exception as e:
            print(f"[bot] advert on startup failed: {e}", flush=True)
        print(f"[bot] up on {SERIAL} as {_node_name!r} fw={_fw}; "
              f"bot chan={CFG['bot_channel_idx']} prefix={CFG['cmd_prefix']!r}", flush=True)
        last_cfg = 0.0
        last_batt = 0.0
        last_rtime = 0.0
        last_pos = 0.0
        last_fix = 0.0
        last_prune = 0.0
        last_wx = 0.0
        last_fox = 0.0
        ui_tr = None
        try:
            ui_tr, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                _UIKeys, local_addr=("127.0.0.1", UI_PORT))
        except OSError as e:
            print(f"[bot] ui key port {UI_PORT} unavailable: {e}", flush=True)
        try:
            while True:
                await asyncio.sleep(2)
                if time.monotonic() - last_cfg > CONFIG_REFRESH_SEC:
                    load_config()
                    last_cfg = time.monotonic()
                await sync_region_scope()
                if time.monotonic() - last_batt > EXT_BATT_SEC:
                    await push_ext_batt()
                    last_batt = time.monotonic()
                if time.monotonic() - last_rtime > RADIO_TIME_SEC:
                    await refresh_radio_time()
                    last_rtime = time.monotonic()
                if time.monotonic() - last_fix > GPS_FIX_SEC:
                    await refresh_gps_fix()
                    if CFG["wardrive_log"] or CFG["wardrive_survey"]:
                        await refresh_position()     # coverage rows need a fresh fix
                    last_fix = time.monotonic()
                if time.monotonic() - last_pos > 60:
                    await refresh_position()
                    await refresh_gps_state()
                    await refresh_adv_loc()
                    await reset_paths_if_moved()
                    last_pos = time.monotonic()
                if time.monotonic() - last_prune > 3600:
                    _wd.prune(max(1, CFG["wardrive_max_gb"]) * 1_000_000_000)
                    last_prune = time.monotonic()
                if CFG["wardrive_survey"] and _wd.survey_due(
                        max(120, CFG["wardrive_survey_sec"]), CFG["wardrive_survey_m"]):
                    asyncio.ensure_future(_wd.survey(mc))
                if CFG.get("wx_node") and time.monotonic() - last_wx > max(1, CFG["wx_poll_min"]) * 60:
                    asyncio.ensure_future(poll_weather())
                    last_wx = time.monotonic()
                await fox_bot_switched()
                if not (CFG["fox_bot"] and CFG["fox_auto"]):
                    last_fox = 0.0                      # switching on posts straight away
                elif time.monotonic() - last_fox >= max(60, CFG["fox_auto_sec"]):
                    await fox_auto_broadcast()
                    last_fox = time.monotonic()
                await drain_outbox()
                await drain_cmdq()
                write_status()
                if CFG["idle_poweroff_sec"] and (
                        time.monotonic() - _last_activity) > CFG["idle_poweroff_sec"]:
                    print("[bot] idle - powering off; T114 wakes us via GPIO3", flush=True)
                    return
        finally:
            if ui_tr:
                ui_tr.close()
            for s in subs:
                mc.unsubscribe(s)
            await mc.stop_auto_message_fetching()
    finally:
        _mc = None
        await mc.disconnect()


async def main() -> None:
    conn = open_db()
    load_config()
    await fox_bot_switched()          # note the starting state: a restart doesn't announce
    if MUX:
        mux_task = asyncio.create_task(
            mux.supervise(SERIAL, BAUD, MUX_LOCAL_PORT, MUX_PORT, MUX_ALLOW, allow_lan=MUX_LAN))
    waiting_logged = False
    while True:
        if not os.path.exists(SERIAL):
            if not waiting_logged:
                print(f"[bot] waiting for radio at {SERIAL}", flush=True)
                waiting_logged = True
            write_status()
            await asyncio.sleep(RECONNECT_SEC)
            continue
        waiting_logged = False
        try:
            if MUX:
                await asyncio.wait_for(mux.ready.wait(), 20)
            await serve(conn)
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[bot] transport error ({type(e).__name__}: {e}); "
                  f"retry in {RECONNECT_SEC}s", flush=True)
            write_status()
            await asyncio.sleep(RECONNECT_SEC)
    conn.close()
    if CFG["idle_poweroff_sec"]:
        subprocess.run(["sudo", "-n", "systemctl", "poweroff"], check=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
