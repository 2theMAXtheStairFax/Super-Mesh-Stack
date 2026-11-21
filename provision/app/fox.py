#!/usr/bin/env python3
"""
Mesh Stack - Fox aerial telemetry.

The Fox aerial repeater rides on an FMS Fox 3M glider. Its flight controller talks
MAVLink to Mission Planner on a laptop; Mission Planner mirrors that stream over the
LAN to this Pi (Ctrl+F -> MAVLink Mirror -> UDP Client -> <pi ip>:14550, write access
off). Read-only: nothing here ever sends towards the aircraft.

Writes fox.json (latest battery / uptime / flight time left / speed / position) for
meshbot ($fox replies on #aerial and #bot, auto-broadcast on #aerial) and the screen's
Fox menu. fox_text() builds the mesh message; meshbot and lcd import this module, so
pymavlink is only imported by main().

config.json keys read here: fox_mavlink (pymavlink connection string), fox_reserve_pct
(battery % treated as empty for the flight time estimate).
"""
import json
import math
import os
import time
from collections import deque

APP_DIR = os.environ.get("SMS_DIR", "/opt/mesh-stack")
FOX_JSON = os.environ.get("SMS_FOX_JSON", f"{APP_DIR}/fox.json")
CONFIG_PATH = os.environ.get("SMS_CONFIG", f"{APP_DIR}/config.json")
DEFAULT_URL = os.environ.get("SMS_FOX_MAVLINK", "udpin:0.0.0.0:14550")
DEFAULT_RESERVE = 20

STALE_SEC = 15               # no MAVLink for this long: the link is down
WRITE_SEC = 1
CONFIG_SEC = 10
PCT_SAMPLE_SEC = 10          # battery % history for the no-current-sensor estimate
PCT_WINDOW_SEC = 600


# ── shared with meshbot / lcd ──────────────────────────────────────────────
def load(path=FOX_JSON) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def link_age(d: dict, now=None):
    """Seconds since the last MAVLink message, None if never."""
    at = (d or {}).get("msg_at")
    return None if not at else max(0, int((now or time.time()) - at))


def fresh(d: dict, now=None) -> bool:
    age = link_age(d, now)
    return age is not None and age < STALE_SEC


def dur(sec) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


def parts(d: dict, now=None) -> list:
    """[battery, up time, time left, speed + altitude, location] as short strings."""
    now = now or time.time()
    v, pct = d.get("volts"), d.get("pct")
    batt = (f"{v:.1f}V" if v is not None else "?V") + (f" {pct}%" if pct is not None else "")
    up = d.get("boot_s")
    up = f"up {dur(up + now - d['msg_at'])}" if up is not None else "up ?"
    left = d.get("left_s")
    left = f"~{dur(left)} left" if left is not None else "left ?"
    gs, alt = d.get("gs"), d.get("alt")
    spd = (f"{gs * 2.23694:.0f}mph" if gs is not None else "?mph") + \
          (f" {alt * 3.28084:.0f}ft" if alt is not None else "")
    if d.get("lat") is not None and (d.get("fix") or 0) >= 2:
        pos = f"{d['lat']:.5f},{d['lon']:.5f}"
    else:
        pos = "no GPS fix"
    return [batt, up, left, spd, pos]


def fox_text(d: dict, now=None) -> str:
    """The mesh message, e.g. '🦊Fox: 11.8V 64% | up 23m | ~18m left | 34mph 410ft | 30.26812,-97.74301'."""
    now = now or time.time()
    age = link_age(d, now)
    if age is None:
        return "\U0001F98AFox: no telemetry (Mission Planner not linked)"
    if age >= STALE_SEC:
        return f"\U0001F98AFox: no telemetry for {dur(age)}"
    return "\U0001F98AFox: " + " | ".join(parts(d, now))


# ── MAVLink reader ─────────────────────────────────────────────────────────
def read_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Fox:
    def __init__(self):
        self.d = {"msg_at": None, "sysid": None}
        self.pct_hist = deque()          # (time, battery %)
        self.reserve = DEFAULT_RESERVE

    def take(self, m) -> None:
        t = m.get_type()
        if t == "BAD_DATA":
            return
        src = m.get_srcSystem()
        if t == "HEARTBEAT":
            if m.type == 6 or m.autopilot == 8:   # MAV_TYPE_GCS / MAV_AUTOPILOT_INVALID: not the plane
                return
            if self.d["sysid"] is None:
                self.d["sysid"] = src
                print(f"[fox] autopilot is system {src}", flush=True)
        if src != self.d["sysid"]:
            return                               # Mission Planner's own traffic, other vehicles
        d, now = self.d, time.time()
        d["msg_at"] = now
        tb = getattr(m, "time_boot_ms", None)
        if tb:
            d["boot_s"] = tb / 1000
            d["boot_at"] = now
        if t == "HEARTBEAT":
            from pymavlink import mavutil
            d["armed"] = bool(m.base_mode & 128)        # MAV_MODE_FLAG_SAFETY_ARMED
            d["mode"] = mavutil.mode_string_v10(m)
        elif t == "SYS_STATUS":
            if m.voltage_battery not in (0, 65535):
                d["volts"] = m.voltage_battery / 1000
            d["amps"] = m.current_battery / 100 if m.current_battery >= 0 else None
            if m.battery_remaining >= 0:
                self._pct(m.battery_remaining, now)
        elif t == "BATTERY_STATUS" and m.id == 0:
            d["mah"] = m.current_consumed if m.current_consumed >= 0 else None
            d["fc_left_s"] = getattr(m, "time_remaining", 0) or None    # 0 = autopilot doesn't know
            if m.battery_remaining >= 0:
                self._pct(m.battery_remaining, now)
        elif t == "GLOBAL_POSITION_INT":
            if m.lat or m.lon:
                d["lat"], d["lon"] = m.lat / 1e7, m.lon / 1e7
            d["alt"] = m.relative_alt / 1000
            if "vfr_at" not in d or now - d["vfr_at"] > 5:
                d["gs"] = math.hypot(m.vx, m.vy) / 100
        elif t == "VFR_HUD":
            d["gs"], d["vfr_at"] = m.groundspeed, now
        elif t == "GPS_RAW_INT":
            d["fix"], d["sats"] = m.fix_type, m.satellites_visible

    def _pct(self, pct, now):
        self.d["pct"] = int(pct)
        if not self.pct_hist or now - self.pct_hist[-1][0] >= PCT_SAMPLE_SEC:
            self.pct_hist.append((now, pct))
        while self.pct_hist and now - self.pct_hist[0][0] > PCT_WINDOW_SEC:
            self.pct_hist.popleft()

    def time_left(self):
        """Seconds of flight until the reserve, and where the number came from.

        1. the autopilot's own estimate (BATTERY_STATUS.time_remaining) when it gives one;
        2. mAh used since boot over time since boot - an average, so motor bursts on
           the climbs and long glides even out;
        3. no current sensor: the battery % slope over the last 10 minutes."""
        d = self.d
        pct, reserve = d.get("pct"), self.reserve
        if d.get("fc_left_s"):
            return d["fc_left_s"], "fc"
        if pct is None:
            return None, ""
        if pct <= reserve:
            return 0, "reserve"
        mah, boot = d.get("mah"), d.get("boot_s")
        if mah and boot and boot > 60 and pct < 100:
            cap = mah / (1 - pct / 100)                  # pack size implied by % left
            avg_ma = mah / (boot / 3600)
            return int((cap * (pct - reserve) / 100) / avg_ma * 3600), "avg"
        if len(self.pct_hist) >= 2:
            (t0, p0), (t1, p1) = self.pct_hist[0], self.pct_hist[-1]
            if p0 - p1 >= 2 and t1 - t0 >= 120:
                return int((pct - reserve) / ((p0 - p1) / (t1 - t0))), "slope"
        return None, ""

    def write(self) -> None:
        out = {k: v for k, v in self.d.items() if k not in ("vfr_at", "boot_at")}
        if out.get("boot_s") is not None:              # as of msg_at, like everything else
            out["boot_s"] += (out["msg_at"] or 0) - self.d.get("boot_at", out["msg_at"] or 0)
        out["left_s"], out["left_src"] = self.time_left()
        out["ts"] = int(time.time())
        try:
            with open(FOX_JSON + ".tmp", "w") as f:
                json.dump(out, f)
            os.replace(FOX_JSON + ".tmp", FOX_JSON)
        except OSError as e:
            print(f"[fox] write failed: {e}", flush=True)


def main() -> None:
    os.environ.setdefault("MAVLINK20", "1")          # Mission Planner mirrors MAVLink 2 frames
    from pymavlink import mavutil
    fox = Fox()
    while True:
        cfg = read_config()
        url = (cfg.get("fox_mavlink") or DEFAULT_URL).strip()
        try:
            conn = mavutil.mavlink_connection(url, autoreconnect=True)
        except Exception as e:
            print(f"[fox] can't open {url}: {e}; retry in 15 s", flush=True)
            fox.write()
            time.sleep(15)
            continue
        print(f"[fox] listening on {url}", flush=True)
        last_write = last_cfg = 0.0
        try:
            while True:
                m = conn.recv_match(blocking=True, timeout=1)
                if m is not None:
                    fox.take(m)
                if time.monotonic() - last_write >= WRITE_SEC:
                    fox.write()
                    last_write = time.monotonic()
                if time.monotonic() - last_cfg >= CONFIG_SEC:
                    cfg = read_config()
                    fox.reserve = int(cfg.get("fox_reserve_pct", DEFAULT_RESERVE))
                    last_cfg = time.monotonic()
                    if (cfg.get("fox_mavlink") or DEFAULT_URL).strip() != url:
                        print("[fox] MAVLink source changed, reconnecting", flush=True)
                        fox.d["sysid"] = None
                        break
        except Exception as e:
            print(f"[fox] link error ({type(e).__name__}: {e}); reconnecting", flush=True)
            time.sleep(2)
        finally:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
