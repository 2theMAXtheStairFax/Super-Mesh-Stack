"""
Wardrive coverage log for meshbot: which repeaters hear us, and where.

Listens only, unless the survey is turned on:
  echo - our own #wardriving message (MeshMapper's ping, sent from the phone over
         BLE through this same radio) repeated back to us; credited to the last hop
  rx   - any flood packet that reached us through a repeater: that last hop is in
         range here (one row per repeater per RX_EVERY_S / RX_EVERY_M)
  disc - an answer to our own zero-hop discover request (survey on)
  drop - a survey request nobody answered

Positions come from the radio's GPS through meshbot; nothing is logged without a
fresh fix. With the radio's GPS skew on, positions are off by 200 m.
Nothing is uploaded: MeshMapper and MeshWars only take data from the MeshMapper app.
"""
import asyncio
import hashlib
import hmac
import math
import random
import sqlite3
import time

from Crypto.Cipher import AES

CHANNEL = "#wardriving"
_KEY = hashlib.sha256(CHANNEL.encode()).digest()[:16]     # hashtag channel secret
_HASH = hashlib.sha256(_KEY).digest()[0]                  # 1-byte channel hash on the air
PAYLOAD_GRP_TXT = 5
FLOOD_ROUTES = (0, 1)        # transport flood, flood: the path is the hops taken
POS_MAX_AGE = 30             # seconds; older fixes aren't logged
RX_EVERY_S = 60
RX_EVERY_M = 100
DISC_FILTER = (1 << 2) | (1 << 3)   # repeaters + room servers (ADV_TYPE_*)
DISC_WAIT_S = 7              # repeaters answer after a random delay


def open_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS coverage (
               ts INTEGER NOT NULL, lat REAL, lon REAL, kind TEXT, node TEXT,
               hops INTEGER, snr REAL, rssi INTEGER, snr_in REAL
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS coverage_ts ON coverage(ts)")
    conn.commit()


def _m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 12_742_000 * math.asin(math.sqrt(a))


def parse_packet(raw: bytes):
    """(route_type, payload_type, hops, hash_size, path, body) of a raw mesh packet."""
    if len(raw) < 2:
        return None
    header = raw[0]
    route, ptype = header & 0x03, (header >> 2) & 0x0F
    i = 5 if route in (0, 3) else 1          # transport codes
    if len(raw) <= i:
        return None
    hs, hops = (raw[i] >> 6) + 1, raw[i] & 0x3F
    path = raw[i + 1:i + 1 + hops * hs]
    return route, ptype, hops, hs, path, raw[i + 1 + hops * hs:]


def decode_wardrive(body: bytes):
    """Plain text of a #wardriving GRP_TXT body ("<sender>: <text>"), else None."""
    if len(body) < 3 + 16 or body[0] != _HASH:
        return None
    mac, ct = body[1:3], body[3:]
    if len(ct) % 16 or hmac.new(_KEY, ct, hashlib.sha256).digest()[:2] != mac:
        return None
    pt = AES.new(_KEY, AES.MODE_ECB).decrypt(ct)
    return pt[5:].split(b"\0")[0].decode("utf-8", "replace")


class Wardrive:
    def __init__(self, conn: sqlite3.Connection, get_pos):
        """get_pos() -> (lat, lon) of a fresh GPS fix, or None."""
        self.conn = conn
        self.get_pos = get_pos
        self.my_name = ""
        self._rx_last: dict[str, tuple] = {}     # node -> (time, lat, lon)
        self._pending = None                    # (tag hex, lat, lon, answered nodes)
        self._survey_at = (0.0, None, None)     # last survey: (monotonic, lat, lon)
        self.counts = {"echo": 0, "rx": 0, "disc": 0, "drop": 0}
        self.last_at = 0

    def _log(self, kind, lat, lon, node="", hops=0, snr=None, rssi=None, snr_in=None):
        self.conn.execute(
            "INSERT INTO coverage (ts, lat, lon, kind, node, hops, snr, rssi, snr_in)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (int(time.time()), lat, lon, kind, node, hops, snr, rssi, snr_in))
        self.conn.commit()
        self.counts[kind] += 1
        self.last_at = int(time.time())

    def on_rx(self, event) -> None:
        p = event.payload or {}
        try:
            pkt = parse_packet(bytes.fromhex(p.get("payload", "")))
        except ValueError:
            return
        if not pkt:
            return
        route, ptype, hops, hs, path, body = pkt
        if route not in FLOOD_ROUTES or hops == 0:
            return                               # straight from the sender: no repeater
        pos = self.get_pos()
        if pos is None:
            return
        lat, lon = pos
        node = path[-hs:].hex()                  # the repeater we heard it from
        snr, rssi = p.get("snr"), p.get("rssi")
        if ptype == PAYLOAD_GRP_TXT and self.my_name:
            text = decode_wardrive(body)
            if text is not None and text.startswith(f"{self.my_name}: "):
                self._log("echo", lat, lon, node, hops, snr, rssi)
                return
        t, plat, plon = self._rx_last.get(node, (0.0, None, None))
        if (time.monotonic() - t < RX_EVERY_S
                and plat is not None and _m(plat, plon, lat, lon) < RX_EVERY_M):
            return
        self._rx_last[node] = (time.monotonic(), lat, lon)
        self._log("rx", lat, lon, node, hops, snr, rssi)

    def on_discover(self, event) -> None:
        p = event.payload or {}
        if not self._pending or p.get("tag") != self._pending[0]:
            return
        tag, lat, lon, seen = self._pending
        node = p.get("pubkey", "")
        if node in seen:
            return
        seen.add(node)
        self._log("disc", lat, lon, node, 0, p.get("SNR"), p.get("RSSI"), p.get("SNR_in"))

    def survey_due(self, every_s: int, every_m: int) -> bool:
        pos = self.get_pos()
        if pos is None or self._pending:
            return False
        t, lat, lon = self._survey_at
        if time.monotonic() - t < every_s:
            return False
        return lat is None or _m(lat, lon, *pos) >= every_m

    async def survey(self, mc) -> None:
        """One zero-hop discover request; repeaters in direct range answer."""
        pos = self.get_pos()
        if pos is None:
            return
        tag = random.randint(1, 0xFFFFFFFF)
        self._survey_at = (time.monotonic(), *pos)
        self._pending = (tag.to_bytes(4, "little").hex(), *pos, set())
        try:
            await mc.commands.send_node_discover_req(DISC_FILTER, prefix_only=True, tag=tag)
            await asyncio.sleep(DISC_WAIT_S)
            if not self._pending[3]:
                self._log("drop", *pos)
        except Exception as e:
            print(f"[wardrive] survey failed: {e}", flush=True)
        finally:
            self._pending = None

    def used_bytes(self) -> int:
        """Bytes in use in the database file (free pages left by pruning don't count).
        Counts the whole file, so it slightly overstates the coverage table."""
        ps = self.conn.execute("PRAGMA page_size").fetchone()[0]
        pages = self.conn.execute("PRAGMA page_count").fetchone()[0]
        free = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
        return (pages - free) * ps

    def prune(self, max_bytes: int) -> int:
        """Over the cap: drop the oldest coverage rows to 5% under it. SQLite reuses the
        freed pages, so the file stops growing instead of shrinking."""
        used = self.used_bytes()
        if used <= max_bytes:
            return 0
        n = self.conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0]
        drop = min(n, int(n * (1 - 0.95 * max_bytes / used)) + 1)
        self.conn.execute("DELETE FROM coverage WHERE rowid IN "
                          "(SELECT rowid FROM coverage ORDER BY ts LIMIT ?)", (drop,))
        self.conn.commit()
        print(f"[wardrive] {used / 1e9:.1f} GB over the {max_bytes / 1e9:.0f} GB cap: "
              f"dropped the oldest {drop} rows", flush=True)
        return drop

    def status(self, survey_on: bool) -> dict:
        return {"counts": self.counts, "last_at": self.last_at, "survey": survey_on}
