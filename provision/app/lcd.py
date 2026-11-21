#!/usr/bin/env python3
"""Mesh Stack status dashboard + joystick menu on the 0.96" ST7735 (160x80 landscape).

Dashboard by default. With the Qwiic Navigation Switch connected:
  center/right  open menu, select      up/down  move      left  back
Menu: Messages, Fox aerial, Send advert, Radio control, Wi-Fi, Settings, Restart, Shut down.
Runs without the joystick too (retries every SW_RETRY seconds).
"""
import json, signal, socket, sqlite3, subprocess, sys, threading, time
from datetime import datetime
from zoneinfo import ZoneInfo
import psutil
from PIL import Image, ImageDraw, ImageFont
from st7735 import ST7735
import navswitch as nav
import about
import fox
import names
import regions

FONT_DIR = '/usr/share/fonts/truetype/dejavu/'
F_HEAD = ImageFont.truetype(FONT_DIR + 'DejaVuSans-Bold.ttf', 11)
F_BODY = ImageFont.truetype(FONT_DIR + 'DejaVuSans.ttf', 11)
F_MONO = ImageFont.truetype(FONT_DIR + 'DejaVuSansMono.ttf', 11)

BG, FG, DIM = (0, 0, 0), (235, 238, 242), (120, 128, 140)
HEAD_BG = (20, 60, 120)
OK, BAD, WARN = (40, 200, 90), (230, 60, 60), (240, 180, 40)

NAS_EVERY = 5       # seconds between radio checks
REFRESH = 2         # seconds between dashboard redraws
MARQUEE_SPEED = 30  # px/s for a header title too long to fit before the clock
MARQUEE_PAUSE = 1.2 # s held at each end of the scroll
MARQUEE_DT = 0.08   # s between scroll steps (header only is repainted)
_mq = {'title': None, 'since': 0.0, 'active': False}   # scrolling title state
MENU_IDLE = 30      # seconds before the menu falls back to the dashboard
FLASH_S = 2.5       # how long a status message stays up
WX_FLASH_S = 1.5    # ...after a weather-page action
SW_RETRY = 30       # seconds between attempts to find the joystick
INT_CONFIRM_S = 10
ROWS = 4            # menu rows under the header
SCREEN_OFF_S = 15   # backlight off after this long idle - same as the T114's own screen (AUTO_OFF_MILLIS)
TZ = ZoneInfo(os.environ.get('SMS_TZ', 'UTC'))
STATUS_FILE = '/opt/mesh-stack/status.json'
POWER_FILE = '/opt/mesh-stack/power.json'
DB_FILE = '/opt/mesh-stack/telemetry.sqlite'
CHANNELS_FILE = '/opt/mesh-stack/channels.json'
NETMODE_FILE = '/var/lib/mesh-stack/netmode.conf'   # written by net/netmode.sh
MSG_LIMIT = 50      # messages listed in the Messages browser
BOT_CHANNELS = ('bot', 'wx', 'weather', 'test', 'wardriv')   # name fragments left out of the channel pages
WX_DB = '/opt/mesh-stack/weather.sqlite'
CONTACTS_FILE = '/opt/mesh-stack/contacts.json'
CONFIG_FILE = '/opt/mesh-stack/config.json'
FOX_FILE = '/opt/mesh-stack/fox.json'
ADVERT_HOLD_S = 1.5  # hold this long for a page action (weather requests, survey on/off)
MAP_ZOOMS_KM = [1, 2, 5, 10, 25, 50, 100, 250]   # outer ring radius
BRIGHT_STEPS = [100, 75, 50, 25, 10]
ANIM_FRAMES = 9     # frames per page slide / cursor glide (~15 ms each, ~120 ms total)
HEAD_L, HEAD_R = (200, 20, 20), (245, 200, 0)    # header gradient: red -> yellow
UI_PORT = 5055      # meshbot forwards "next/prev/enter/select/wake" here to the T114 ("ui ..." command)
EMOJI_FONT = '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf'
BATTERY, PLUG = '\U0001F50B', '\U0001F50C'    # dashboard battery row: on battery / charging

SETTINGS_FILE = '/opt/mesh-stack/lcd_settings.json'
DEFAULTS = {'input': 'poll', 'led': True, 'flip': False, 'flip_controls': False,
            'msg_wake': True, 'radio_msg_wake': True, 'radio_flip': False,
            'pi_bright': 100, 'radio_bright': 100, 'anim': True, 'gps_skew': True}

# ---------------------------------------------------------------- data
def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except OSError:
        return None

def get_ingest():
    try:
        return psutil.disk_usage('/')
    except OSError:
        return None

_ui_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def radio_key(key):
    """Press a key on the T114's own screen (needs the sms-uart-ui firmware)."""
    try:
        _ui_sock.sendto(key.encode(), ('127.0.0.1', UI_PORT))
    except OSError:
        pass

try:
    _EMOJI = ImageFont.truetype(EMOJI_FONT, 109)     # CBDT colour font: fixed 109 px strike
except OSError:
    _EMOJI = None
_emoji_cache = {}

def is_emoji(ch):
    o = ord(ch)
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF

def emoji_img(ch, size):
    key = (ch, size)
    if key not in _emoji_cache:
        im = Image.new('RGBA', (136, 128), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((0, 0), ch, font=_EMOJI, embedded_color=True)
        bbox = im.getbbox() or (0, 0, 1, 1)
        im = im.crop(bbox)
        im.thumbnail((size, size), Image.LANCZOS)
        _emoji_cache[key] = im
    return _emoji_cache[key]

def rich_text(img, d, xy, text, font, fill, size=12):
    """Draw text with colour emoji (DejaVu has no emoji glyphs)."""
    x, y = xy
    for ch in clean(text):
        if ch in '\ufe0f\u200d':
            continue
        if is_emoji(ch) and _EMOJI:
            e = emoji_img(ch, size)
            img.paste(e, (int(x), int(y + 1)), e)
            x += e.width + 1
        else:
            d.text((x, y), ch, font=font, fill=fill)
            x += d.textlength(ch, font=font)

def push_radio_settings():
    """Radio-side settings are RAM-only on the T114, so they're re-sent periodically."""
    radio_key('msgwake on' if S['radio_msg_wake'] else 'msgwake off')
    radio_key('flip on' if S['radio_flip'] else 'flip off')
    radio_key(f"bright {S['radio_bright']}")
    radio_key('gpsskew on' if S['gps_skew'] else 'gpsskew off')   # fw boots with skew on
    # Advert position lives in the radio's flash prefs (the web UI toggles it too), so only seed it once.
    if not S.get('adv_pos_init') and read_json(STATUS_FILE).get('adv_loc') is not None:
        radio_key('advpos off')
        S['adv_pos_init'] = True; save_settings()

def gps_badge(st):
    """Dashboard GPS: satellites in use, green = locked, yellow = no lock, grey = off / unknown."""
    if st.get('gps_on') is None:
        return "GPS ?", DIM
    if not st['gps_on']:
        return "GPS off", DIM
    fix, sats = st.get('gps_fix'), st.get('gps_sats')
    if fix is None:                     # firmware without "gps fix": satellite count unknown
        return "? sats", WARN
    return f"{sats or 0} sats", OK if fix else WARN

def survey_on():
    return bool((read_json(STATUS_FILE).get('wardrive') or {}).get('survey'))

def adv_pos_label():
    v = read_json(STATUS_FILE).get('adv_loc')
    return '?' if v is None else 'On' if v else 'Off'

def scope_cfg():
    """(on, name) of the region scope as saved in config.json."""
    c = read_json(CONFIG_FILE)
    return bool(int(c.get('region_scope_on', 0))), regions.normalize(c.get('region_scope', ''))

def gps_label():
    on = read_json(STATUS_FILE).get('gps_on')
    return '?' if on is None else 'On' if on else 'Off'

def recent_messages(n=MSG_LIMIT):
    """Newest first: (ts, source, sender, channel_idx, text) from meshbot's log (read-only)."""
    try:
        c = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True, timeout=1)
        rows = c.execute("SELECT ts, source, sender, channel_idx, text FROM rx "
                         "ORDER BY rowid DESC LIMIT ?", (n,)).fetchall()
        c.close()
        return rows
    except sqlite3.Error:
        return []

def chan_name(idx):
    return read_json(CHANNELS_FILE).get(str(idx), f"ch{idx}") if idx is not None else "DM"

def clean(text):
    """One-line safe text: PIL can't measure newlines/tabs/control chars."""
    return "".join(ch if ch.isprintable() else " " for ch in (text or ""))

def msg_body(sender, text):
    """Channel texts arrive as 'Name: msg' - drop the repeated name."""
    text = clean(text)
    if sender and text.startswith(sender + ": "):
        return text[len(sender) + 2:]
    return text

def wrap(d, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if d.textlength(trial, font=font) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            while d.textlength(word, font=font) > width:     # very long word: hard-split
                cut = len(word)
                while cut > 1 and d.textlength(word[:cut], font=font) > width:
                    cut -= 1
                lines.append(word[:cut]); word = word[cut:]
            line = word
    if line:
        lines.append(line)
    return lines or [""]

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def netmode_state():
    """Saved Wi-Fi settings from net/netmode.sh: {'AP': 'on'|'off', 'NET': 'auto'|'home'|'mobile'}."""
    try:
        with open(NETMODE_FILE) as f:
            return dict(l.strip().split('=', 1) for l in f if '=' in l)
    except OSError:
        return {}

def now_local():
    """Time from the radio's clock (synced from the GPS) when meshbot has one, else the Pi's."""
    st = read_json(STATUS_FILE)
    rt = st.get('radio_time')
    if rt and 1.7e9 < rt < 2.0e9:
        t = rt + max(0.0, time.time() - st.get('ts', time.time()))
        return datetime.fromtimestamp(t, TZ)
    return datetime.now(TZ)

def batt_pct(v):
    table = [(4.20, 100), (4.10, 90), (4.00, 80), (3.90, 65), (3.80, 50),
             (3.70, 35), (3.60, 20), (3.50, 10), (3.30, 3), (3.00, 0)]
    if v >= table[0][0]:
        return 100
    for (v1, p1), (v2, p2) in zip(table, table[1:]):
        if v >= v2:
            return round(p2 + (p1 - p2) * (v - v2) / (v1 - v2))
    return 0

def ago(sec):
    if sec is None:
        return "none yet"
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    return f"{sec // 3600}h ago"

def check_nas():
    """Radio up = meshbot wrote status.json recently with the radio's firmware filled in."""
    try:
        st = json.load(open('/opt/mesh-stack/status.json'))
        return bool(st.get('fw')) and time.time() - st.get('ts', 0) < 120
    except (OSError, ValueError):
        return False

nas = None
def nas_loop():                     # in a thread so the ssh timeout never stalls the joystick
    global nas
    while True:
        nas = check_nas()
        time.sleep(NAS_EVERY)

def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)

def save_settings():
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(S, f)

# ---------------------------------------------------------------- drawing
def frame(w, h, title):
    img = Image.new('RGB', (w, h), BG)
    d = ImageDraw.Draw(img)
    for x in range(w):
        f = x / (w - 1)
        d.line([x, 0, x, 14], fill=tuple(int(a + (b - a) * f) for a, b in zip(HEAD_L, HEAD_R)))
    paint_title(img, d, title)
    return img, d

def paint_title(img, d, title):
    """Title and clock. A title too long for the space before the clock scrolls across it:
    held at the start, scrolled to the end, held, then back to the start."""
    w = img.width
    clock = now_local().strftime('%-I:%M %p')
    room = int(w - 4 - d.textlength(clock, font=F_HEAD) - 8)
    tw = int(d.textlength(title, font=F_HEAD)) + 1
    if tw <= room:
        d.text((4, 1), title, font=F_HEAD, fill=(255, 255, 255))
    else:
        if title != _mq['title']:
            _mq.update(title=title, since=time.monotonic())
        over = tw - room
        t = (time.monotonic() - _mq['since']) % (2 * MARQUEE_PAUSE + over / MARQUEE_SPEED)
        off = int(min(over, max(0.0, t - MARQUEE_PAUSE) * MARQUEE_SPEED))
        strip = Image.new('RGBA', (tw, 15), (0, 0, 0, 0))
        ImageDraw.Draw(strip).text((0, 1), title, font=F_HEAD, fill=(255, 255, 255))
        part = strip.crop((off, 0, off + room, 15))
        img.paste(part, (4, 0), part)
        _mq['active'] = True
    d.text((w - 4, 1), clock, font=F_HEAD, fill=(0, 0, 0), anchor='ra')

def repaint_header(img):
    """Next scroll step of the current title on a copy of the last full frame."""
    f = img.copy()
    d = ImageDraw.Draw(f)
    for x in range(f.width):
        k = x / (f.width - 1)
        d.line([x, 0, x, 14], fill=tuple(int(a + (b - a) * k) for a, b in zip(HEAD_L, HEAD_R)))
    paint_title(f, d, _mq['title'])
    return f

def dot(d, x, y, color):
    d.ellipse([x, y, x + 7, y + 7], fill=color)

def get_title():
    return names.resolve_title(read_json(CONFIG_FILE).get('title'), os.environ.get('SMS_TITLE'))

def draw_msg(w, h, lines, title=None, color=WARN):
    """Centered message; every line word-wraps to the screen (max 4 rows shown)."""
    img, d = frame(w, h, title or get_title())
    rows = []
    for i, line in enumerate(lines):
        rows += [(r, color if i == 0 else DIM) for r in wrap(d, clean(str(line)), F_BODY, w - 8)]
    rows = rows[:4]
    y0 = 47 - (len(rows) - 1) * 7
    for i, (row, fill) in enumerate(rows):
        d.text((w // 2, y0 + i * 14), row, font=F_BODY, fill=fill, anchor='mm')
    return img

def draw(w, h, ip, nas, disk):
    img, d = frame(w, h, get_title())
    st, pw = read_json(STATUS_FILE), read_json(POWER_FILE)

    # Radio
    if nas is None:
        dot(d, 4, 21, DIM); d.text((16, 18), "Radio: checking", font=F_BODY, fill=DIM)
    else:
        dot(d, 4, 21, OK if nas else BAD)
        name = (st.get('node_name') or 'Radio online') if nas else 'Radio offline'
        rich_text(img, d, (16, 18), name[:18], F_BODY, FG)

    # Last message + count
    d.text((4, 33), f"Msg {ago(st.get('last_rx_age'))}", font=F_BODY, fill=FG)
    d.text((w - 4, 33), f"{st.get('rx_count', 0)} rx", font=F_BODY, fill=DIM, anchor='ra')

    # Battery (UPS HAT INA219 via sms-power): voltage only, coloured by level
    v, ma = pw.get('volts'), pw.get('milliamps')
    if v and time.time() - pw.get('ts', 0) < 120:
        pct = batt_pct(v)
        chg = ma is not None and ma < -20          # positive = discharging on this HAT
        col = OK if pct > 40 else WARN if pct > 15 else BAD
        rich_text(img, d, (4, 48), f"{PLUG if chg else BATTERY} {v:.2f}V", F_BODY, col)
    else:
        rich_text(img, d, (4, 48), f"{BATTERY} N/A", F_BODY, DIM)

    # GPS
    label, col = gps_badge(st)
    d.text((w - 4, 48), label, font=F_BODY, fill=col, anchor='ra')

    # Network + date
    d.text((4, 63), ip or "No network", font=F_MONO, fill=FG if ip else DIM)
    d.text((w - 4, 63), now_local().strftime('%b %-d'), font=F_BODY, fill=DIM, anchor='ra')
    return img

def menu_top(sel, n):
    return max(0, min(sel - 1, n - ROWS))

def draw_menu(w, h, title, labels, sel, hl_y=None):
    """hl_y: highlight bar position while it glides between rows (None = on sel)."""
    img, d = frame(w, h, title)
    top = menu_top(sel, len(labels))
    if hl_y is None:
        hl_y = 16 + (sel - top) * 16
    d.rectangle([0, hl_y, w - 1, hl_y + 15], fill=HEAD_BG)
    for i, label in enumerate(labels[top:top + ROWS]):
        y = 16 + i * 16
        rich_text(img, d, (6, y + 1), label, F_BODY, FG)
    return img

def draw_reader(w, h, msg, scroll, title="MESSAGE", with_sender=False):
    """One message: channel (or sender) + age line, then the wrapped text."""
    ts, source, sender, idx, text = msg
    img, d = frame(w, h, title)
    who = "me" if source == "tx" else (sender or "?")
    age = ago(int(time.time() - ts)) if ts else ""
    meta = f"{'Sent' if source == 'tx' else chan_name(idx)}  {age}"   # sender is on the list screen
    if with_sender:
        meta = f"{who[:14]}  {age}"
    lines = [None] + wrap(d, msg_body(sender, text), F_BODY, w - 8)
    rows = lines[scroll:scroll + 4]
    for i, line in enumerate(rows):
        y = 16 + i * 16
        if line is None:
            rich_text(img, d, (4, y + 1), meta, F_BODY, DIM)
        else:
            rich_text(img, d, (4, y + 1), line, F_BODY, FG)
    if len(lines) > 4:                       # scroll bar
        top = 16 + int((h - 18) * scroll / len(lines))
        d.rectangle([w - 3, top, w - 2, top + max(6, int((h - 18) * 4 / len(lines)))], fill=DIM)
    return img, len(lines)

F_BIG = ImageFont.truetype(FONT_DIR + 'DejaVuSans-Bold.ttf', 24)
F_SMALL = ImageFont.truetype(FONT_DIR + 'DejaVuSans.ttf', 9)
_COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

def latest_in(idx):
    """Newest message heard on one channel."""
    try:
        c = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True, timeout=1)
        row = c.execute("SELECT ts, source, sender, channel_idx, text FROM rx WHERE channel_idx = ? "
                        "AND source != 'tx' ORDER BY rowid DESC LIMIT 1", (idx,)).fetchone()
        c.close()
        return row
    except sqlite3.Error:
        return None

def chat_channels():
    """[(idx, last ts)] of channels with traffic, most recent first, bot channels left out."""
    names = read_json(CHANNELS_FILE)
    try:
        c = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True, timeout=1)
        rows = c.execute("SELECT channel_idx, MAX(ts) FROM rx WHERE channel_idx IS NOT NULL "
                         "AND source != 'tx' GROUP BY channel_idx ORDER BY MAX(rowid) DESC").fetchall()
        c.close()
    except sqlite3.Error:
        rows = []
    out = [(idx, ts) for idx, ts in rows
           if not any(b in names.get(str(idx), '').lower() for b in BOT_CHANNELS)]
    return out or [(0, None)]

def latest_weather():
    try:
        c = sqlite3.connect(f'file:{WX_DB}?mode=ro', uri=True, timeout=1)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM readings ORDER BY recv_time DESC LIMIT 1").fetchone()
        c.close()
        return dict(row) if row else None
    except sqlite3.Error:
        return None

def draw_hold(w, h, frac, title="ADVERT", prompt="Hold to send advert", secs=ADVERT_HOLD_S):
    """Charge-up bar while a key is held to confirm a send."""
    img, d = frame(w, h, title)
    d.text((w // 2, 27), prompt, font=F_BODY, fill=FG, anchor='mm')
    x0, x1, y0, y1 = 8, w - 9, 40, 56
    d.rectangle([x0, y0, x1, y1], outline=DIM)
    fill = int((x1 - x0 - 2) * min(1.0, frac))
    for x in range(fill):
        f = x / max(1, x1 - x0)
        d.line([x0 + 1 + x, y0 + 1, x0 + 1 + x, y1 - 1],
               fill=tuple(int(a + (b - a) * f) for a, b in zip(HEAD_L, HEAD_R)))
    d.text((w // 2, 68), f"{frac * secs:.1f} / {secs:g} s", font=F_BODY, fill=DIM, anchor='mm')
    return img

def station_name(ref):
    """Contact name for the configured station (config may hold a name or a key)."""
    r = (ref or '').strip().lower()
    for c in read_json(CONTACTS_FILE) or []:
        if isinstance(c, dict) and r and (c.get('name', '').lower() == r
                                          or c.get('public_key', '').lower().startswith(r)):
            return c.get('name') or ref[:12]
    return ref[:12] + ('…' if len(ref) > 12 else '')

def draw_weather(w, h):
    st, wx = read_json(STATUS_FILE), latest_weather()
    if not wx:
        node = st.get('wx_node') or ''
        lines = ["No weather yet", f"station: {station_name(node)}" if node else "station not set (web Config)"]
        if st.get('wx_result'):
            lines.append(f"last ping: {st['wx_result']}")
        lines.append("\u25b2 chan \u25bc DM \u25cf ping \u25b6 back")
        return draw_msg(w, h, lines, title="WEATHER")
    age = ago(int(time.time() - wx['recv_time']))
    img, d = frame(w, h, f"WX {age}")
    # big temperature
    t = wx.get('temp_f')
    d.text((4, 15), f"{t:.0f}\u00b0F" if t is not None else "--\u00b0F", font=F_BIG, fill=FG)
    # humidity / dew point, pressure / rain
    hum, dew = wx.get('humidity'), wx.get('dewpoint_f')
    d.text((4, 44), (f"Hum {hum:.0f}%" if hum is not None else "Hum --")
           + (f"  Dew {dew:.0f}\u00b0" if dew is not None else ""), font=F_BODY, fill=FG)
    pr, rain, bv = wx.get('pressure_inhg'), wx.get('rain_total_in'), wx.get('battery_voltage')
    parts = [f"{pr:.2f}in"] if pr is not None else []
    if rain is not None:
        parts.append(f"Rain {rain:.2f}")
    if pr is None and bv is not None:          # text replies carry no pressure: show the station battery
        parts.append(f"{bv:.1f}V")
    d.text((4, 60), "  ".join(parts) or "--", font=F_BODY, fill=DIM)
    # wind compass
    cx, cy, r = 138, 37, 13   # N label clears the 15 px header
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=DIM)
    d.text((cx, cy - r - 1), "N", font=F_SMALL, fill=DIM, anchor='ms')
    wd = wx.get('wind_direction')
    if wd in _COMPASS16:
        import math
        a = math.radians(_COMPASS16.index(wd) * 22.5)
        tip = (cx + (r - 3) * math.sin(a), cy - (r - 3) * math.cos(a))
        tail = (cx - (r - 6) * math.sin(a), cy + (r - 6) * math.cos(a))
        d.line([tail, tip], fill=HEAD_R, width=2)
        d.ellipse([tip[0] - 2, tip[1] - 2, tip[0] + 2, tip[1] + 2], fill=HEAD_L)
    ws = wx.get('wind_speed_mph')
    d.text((cx, 58), (f"{ws:.0f}mph" if ws is not None else "--mph"), font=F_BODY, fill=FG, anchor='mm')
    d.text((cx, 71), wd or "", font=F_SMALL, fill=DIM, anchor='mm')
    return img

def wardrive_today():
    """Today's coverage log: (rows by kind, repeaters heard, newest row with a repeater)."""
    midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        c = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=2)
        rows = c.execute("SELECT ts, kind, node, hops, snr FROM coverage WHERE ts >= ? "
                         "ORDER BY ts DESC", (int(midnight),)).fetchall()
        c.close()
    except sqlite3.Error:
        rows = []
    kinds = {}
    for r in rows:
        kinds[r[1]] = kinds.get(r[1], 0) + 1
    nodes = {r[2] for r in rows if r[2]}
    nodes = {n for n in nodes if not any(m != n and m.startswith(n) for m in nodes)}   # 1- vs 2-byte prefixes
    return kinds, len(nodes), next((r for r in rows if r[2]), None)

def node_name(prefix):
    hits = [c.get('name', '') for c in (read_json(CONTACTS_FILE) or [])
            if isinstance(c, dict) and (c.get('public_key') or '').startswith(prefix)]
    return hits[0] if len(hits) == 1 else prefix

def draw_wardrive(w, h):
    """Coverage log at a glance: who heard us last, today's totals, GPS and survey state."""
    st = read_json(STATUS_FILE)
    img, d = frame(w, h, "WARDRIVE")
    kinds, nrep, last = wardrive_today()
    if last:
        ts, kind, node, hops, snr = last
        rich_text(img, d, (4, 18), node_name(node)[:16], F_BODY, FG)
        col = DIM if snr is None else OK if snr > 0 else WARN if snr > -10 else BAD
        d.text((w - 4, 18), "--" if snr is None else f"{snr:+.0f}dB", font=F_BODY, fill=col, anchor='ra')
        d.text((4, 33), f"{ago(int(time.time() - ts))} · {hops} hop{'s' if hops != 1 else ''}",
               font=F_BODY, fill=DIM)
    else:
        d.text((4, 18), "Nothing heard today", font=F_BODY, fill=DIM)
    d.text((4, 48), f"{nrep} rptr{'' if nrep == 1 else 's'} · {kinds.get('echo', 0)} echo · "
                    f"{sum(kinds.values())} rows",
           font=F_BODY, fill=FG)
    label, col = gps_badge(st)
    d.text((4, 63), label, font=F_BODY, fill=col)
    on = survey_on()
    d.text((w - 4, 63), f"Survey {'on' if on else 'off'}", font=F_BODY, fill=OK if on else DIM, anchor='ra')
    return img

def coverage_points(days=7, limit=3000):
    """Recent wardrive rows (meshbot's coverage table): (lat, lon, kind, snr), newest first."""
    try:
        c = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=2)
        rows = c.execute("SELECT lat, lon, kind, snr FROM coverage WHERE ts > ? "
                         "ORDER BY ts DESC LIMIT ?", (int(time.time()) - days * 86400, limit)).fetchall()
        c.close()
        return rows
    except sqlite3.Error:
        return []

def draw_map(w, h, zoom_i, labels):
    """Contacts with a known position around us; we're the cross in the middle."""
    import math
    st = read_json(STATUS_FILE)
    lat0, lon0 = st.get('lat'), st.get('lon')
    contacts = [c for c in (read_json(CONTACTS_FILE) or []) if isinstance(c, dict) and c.get('lat') and c.get('lon')]
    if not (lat0 or lon0):
        return draw_msg(w, h, ["No position yet", "waiting on a GPS fix",
                               f"{len(contacts)} contacts have a location", "\u25b2 back"], title="MAP")
    km = MAP_ZOOMS_KM[zoom_i]
    img, d = frame(w, h, f"MAP {st.get('pos_src', '').upper()}")
    cx, cy, R = w // 2, 47, 30
    for rr in (R, R // 2):
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(45, 50, 60))
    kx = 111.32 * math.cos(math.radians(lat0))
    pts = []
    for c in contacts:
        dx, dy = (c['lon'] - lon0) * kx, (c['lat'] - lat0) * 110.57
        x, y = cx + dx / km * R, cy - dy / km * R
        if 0 <= x < w and 15 <= y < h:
            pts.append((math.hypot(dx, dy), x, y, c))
    for la, lo, kind, snr in coverage_points():
        x, y = cx + (lo - lon0) * kx / km * R, cy - (la - lat0) * 110.57 / km * R
        if 0 <= x < w and 15 <= y < h:
            col = ((110, 30, 30) if kind == 'drop' else OK if (snr or -99) > 0
                   else WARN if (snr or -99) > -10 else BAD)
            d.point((x, y), fill=col)
    for dist, x, y, c in sorted(pts, key=lambda p: -p[0]):
        col = {2: (255, 150, 40), 1: OK, 3: (80, 150, 255)}.get(c.get('type'), DIM)
        if c.get('type') == 2:
            d.rectangle([x - 1, y - 1, x + 1, y + 1], fill=col)
        else:
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=col)
    if labels:                  # nearest few, skipping any that would overlap another label
        taken = [(cx - 5, cy - 5, cx + 5, cy + 5)]
        for dist, x, y, c in sorted(pts, key=lambda p: p[0]):
            name = "".join(ch for ch in clean(c.get('name', '')) if not is_emoji(ch)).strip()[:9]
            bx = d.textbbox((x + 3, y - 5), name, font=F_SMALL)
            if not name or bx[2] > w or bx[1] < 15 or bx[3] > h - 12 or any(not (bx[2] < t[0] or bx[0] > t[2] or bx[3] < t[1] or bx[1] > t[3]) for t in taken):
                continue
            d.text((x + 3, y - 5), name, font=F_SMALL, fill=FG)
            taken.append(bx)
            if len(taken) > 4:
                break
    d.line([cx - 4, cy, cx + 4, cy], fill=FG)                 # us
    d.line([cx, cy - 4, cx, cy + 4], fill=FG)
    d.text((4, 16), "N\u2191", font=F_SMALL, fill=DIM)
    d.text((4, h - 11), f"{km}km", font=F_SMALL, fill=DIM)
    d.text((w - 4, h - 11), f"{len(pts)}/{len(contacts)}", font=F_SMALL, fill=DIM, anchor='ra')
    return img

# ---------------------------------------------------------------- joystick + menu
S = load_settings()
sw, sw_retry = None, 0.0

def open_switch():
    global sw, sw_retry
    sw_retry = time.monotonic() + SW_RETRY
    try:
        sw = nav.NavSwitch()
    except OSError:
        sw = None
        return
    if S['input'] == 'int':
        try:
            sw.int_enable()
        except OSError:
            S['input'] = 'poll'; save_settings()

def drop_switch():
    global sw
    try:
        sw.close()
    except OSError:
        pass
    sw = None

class UI:
    def __init__(self):
        self.stack = []             # [(title, items_fn, sel)]; empty = dashboard
        self.confirm = None         # (question, action)
        self.flash, self.flash_until = None, 0.0
        self.last_input = 0.0
        self.radio = False          # joystick drives the T114's screen
        self.reader = None          # [message row, scroll, line count] while reading one
        self.page = None            # 'wx' | 'pub' | 'map' | 'wd' - pages off the dashboard
        self.pub_scroll, self.pub_lines = 0, 1
        self.pub_chan = 0           # channel shown on the 'pub' page
        self.map_zoom, self.map_labels = 4, False

    def show_flash(self, *lines, secs=FLASH_S):
        self.flash, self.flash_until = lines, time.monotonic() + secs

    # menus: lists of (label, action)
    def main_items(self):
        return [("Messages", lambda: self.push("Messages", self.message_items)),
                ("Fox aerial", lambda: self.push("FOX AERIAL", self.fox_items)),
                ("Send advert", lambda: self.push("Send advert", self.advert_items)),
                ("Radio control", self.enter_radio),
                ("Wi-Fi", lambda: self.push("Wi-Fi", self.wifi_items)),
                ("Settings", lambda: self.push("Settings", self.settings_items)),
                ("About", lambda: self.push("ABOUT", self.about_items)),
                ("Restart", lambda: self.ask("Restart?", ['sudo', '/usr/bin/systemctl', 'reboot'])),
                ("Shut down", lambda: self.ask("Shut down?", ['sudo', '/usr/bin/systemctl', 'poweroff']))]

    def about_items(self):
        return [(f"v{about.VERSION}", lambda: self.show_flash(about.NAME, f"version {about.VERSION}", f"{about.LICENSE} license")),
                ("Credits", lambda: self.push("CREDITS", self.credits_items))]

    def credits_items(self):
        return [(name[:20], lambda n=name, r=role, li=lic: self.show_flash(n, r, li) if li else self.show_flash(n, r))
                for name, role, lic, _ in about.CREDITS]

    def settings_items(self):
        return [(f"Screen: {'Flipped' if S['flip'] else 'Normal'}", self.toggle_flip),
                (f"Controls: {'Flipped' if S['flip_controls'] else 'Normal'}", self.toggle_controls),
                (f"Msg wakes Pi: {'On' if S['msg_wake'] else 'Off'}", self.toggle_msg_wake),
                (f"Msg wakes radio: {'On' if S['radio_msg_wake'] else 'Off'}", self.toggle_radio_msg_wake),
                (f"Radio screen: {'Flipped' if S['radio_flip'] else 'Normal'}", self.toggle_radio_flip),
                (f"Pi brightness: {S['pi_bright']}%", self.cycle_pi_bright),
                (f"Radio brightness: {S['radio_bright']}%", self.cycle_radio_bright),
                (f"GPS: {gps_label()}", self.toggle_gps),
                (f"GPS skew 200m: {'On' if S['gps_skew'] else 'Off'}", self.toggle_gps_skew),
                (f"Advert pos: {adv_pos_label()}", self.toggle_adv_pos),
                (f"Survey: {'On' if survey_on() else 'Off'}", self.toggle_survey),
                (f"Region scope: {'On' if scope_cfg()[0] else 'Off'}", self.toggle_region),
                (f"Scope: {scope_cfg()[1] or 'none'}", lambda: self.push("Region scope", self.region_items)),
                (f"Animations: {'On' if S['anim'] else 'Off'}", self.toggle_anim),
                (f"Input: {'Interrupt' if S['input'] == 'int' else 'Poll'}", self.toggle_input),
                (f"Status LED: {'On' if S['led'] else 'Off'}", self.toggle_led)]

    def toggle_flip(self):
        S['flip'] = not S['flip']; save_settings()
        lcd.set_flip(S['flip'])

    def message_items(self):
        rows = recent_messages()
        if not rows:
            return [("No messages yet", lambda: None)]
        items = []
        for row in rows:
            ts, source, sender, idx, text = row
            who = "me" if source == "tx" else (sender or "?")
            items.append((f"{who[:8]}: {msg_body(sender, text)}"[:40],
                          lambda r=row: self.open_msg(r)))
        return items

    def fox_items(self):
        """Auto-broadcast toggle, send now, then the live telemetry (fox.py via Mission Planner)."""
        cfg, d = read_json(CONFIG_FILE), fox.load(FOX_FILE)
        auto, every = bool(cfg.get('fox_auto')), max(60, int(cfg.get('fox_auto_sec', 120)))
        age = fox.link_age(d)
        link = ("Link: none yet" if age is None else
                f"Link: live {age}s" if age < fox.STALE_SEC else f"Link: down {ago(age)}")
        bot = bool(cfg.get('fox_bot', 0))
        items = [(f"Fox bot: {'On' if bot else 'Off'}", self.toggle_fox_bot),
                 (f"Auto #aerial {every // 60}m: {'On' if auto else 'Off'}", self.toggle_fox_auto),
                 ("Send to #aerial now", self.fox_send),
                 (link, lambda: None)]
        if age is not None:
            batt, up, left, spd, pos = fox.parts(d)
            items += [(f"{batt} {left}", lambda: None), (f"{spd} {up}", lambda: None),
                      (pos, lambda: None)]
        return items

    def toggle_fox_bot(self):
        on = not read_json(CONFIG_FILE).get('fox_bot', 0)
        radio_key('fox bot on' if on else 'fox bot off')
        self.show_flash(f"Fox bot {'on' if on else 'off'}",
                        "announcing on #aerial" if on else "$fox and auto posts off")

    def toggle_fox_auto(self):
        cfg = read_json(CONFIG_FILE)
        on, every = not cfg.get('fox_auto'), max(60, int(cfg.get('fox_auto_sec', 120)))
        radio_key('fox auto on' if on else 'fox auto off')
        self.show_flash(f"Fox auto {'on' if on else 'off'}",
                        f"#aerial now, then every {every // 60}m" if on else "$fox replies still on")

    def fox_send(self):
        radio_key('fox send')
        self.show_flash("Fox telemetry sent", "on #aerial")

    def wifi_items(self):
        """AP on/off toggle plus the client network; '*' marks the saved choice."""
        st = netmode_state()
        ap_on = st.get('AP') == 'on'
        net = st.get('NET', 'auto')
        mark = lambda n: '*' if not ap_on and net == n else ' '
        return [(f"AP mode: {'ON' if ap_on else 'off'}",
                 lambda: self.netmode(["ap", "off" if ap_on else "on"],
                                      "AP mode off" if ap_on else "AP mode on",
                                      f"joining {net} Wi-Fi" if ap_on else "SSID from sms-netmode")),
                (f"{mark('home')}Home Wi-Fi", lambda: self.netmode(["home"], "Wi-Fi: home", "mobile if home not seen")),
                (f"{mark('mobile')}Mobile router", lambda: self.netmode(["mobile"], "Wi-Fi: mobile", "home if mobile not seen")),
                (f"{mark('auto')}Auto", lambda: self.netmode(["auto"], "Wi-Fi: auto", "home, else mobile"))]

    def netmode(self, args, *msg):
        """Hands off to net/netmode.sh (allowed in sudoers); settings persist across reboots."""
        subprocess.Popen(['sudo', '/opt/mesh-stack/net/netmode.sh', *args],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.stack.clear()
        self.show_flash(*msg)

    def advert_items(self):
        return [("Zero-hop (nearby)", lambda: self.queue_cmd("advert", "Advert queued")),
                ("Flood (whole mesh)", lambda: self.queue_cmd("flood_advert", "Flood advert queued"))]

    def queue_cmd(self, kind, msg):
        """Hand a one-shot radio action to meshbot (same queue as the web Contacts tab)."""
        try:
            c = sqlite3.connect(DB_FILE, timeout=2)
            c.execute("INSERT INTO cmdq (ts, kind, arg) VALUES (?, ?, '')", (int(time.time()), kind))
            c.commit(); c.close()
            self.stack.clear()
            self.show_flash(msg, "sent within a few s")
        except sqlite3.Error as e:
            self.show_flash("Advert failed", str(e)[:24])

    def channel_items(self):
        return [(f"{chan_name(idx)}  {ago(int(time.time() - ts)) if ts else ''}",
                 lambda i=idx: self.open_channel(i)) for idx, ts in chat_channels()]

    def open_channel(self, idx):
        self.stack.clear()
        self.page, self.pub_chan, self.pub_scroll = 'pub', idx, 0

    def open_msg(self, row):
        self.reader = [row, 0, 1]

    def enter_radio(self):
        self.stack.clear()
        self.radio = True
        radio_key('wake')

    def toggle_msg_wake(self):
        S['msg_wake'] = not S['msg_wake']; save_settings()

    def toggle_radio_msg_wake(self):
        S['radio_msg_wake'] = not S['radio_msg_wake']; save_settings()
        push_radio_settings()

    def cycle_pi_bright(self):
        i = BRIGHT_STEPS.index(S['pi_bright']) if S['pi_bright'] in BRIGHT_STEPS else 0
        S['pi_bright'] = BRIGHT_STEPS[(i + 1) % len(BRIGHT_STEPS)]; save_settings()
        lcd.set_brightness(S['pi_bright'])

    def cycle_radio_bright(self):
        i = BRIGHT_STEPS.index(S['radio_bright']) if S['radio_bright'] in BRIGHT_STEPS else 0
        S['radio_bright'] = BRIGHT_STEPS[(i + 1) % len(BRIGHT_STEPS)]; save_settings()
        push_radio_settings()

    def hold(self, key, title, prompt, secs=ADVERT_HOLD_S):
        """Charge-up bar while `key` (logical direction) stays held; True once held `secs`."""
        flip = {nav.UP: nav.DOWN, nav.DOWN: nav.UP, nav.LEFT: nav.RIGHT, nav.RIGHT: nav.LEFT}
        phys = flip.get(key, key) if S['flip_controls'] else key
        t0 = time.monotonic()
        while sw:
            try:
                held = sw._read() & phys
            except OSError:
                return False
            el = time.monotonic() - t0
            if not held:
                return False
            if el >= secs:
                return True
            lcd.show(draw_hold(W, H, el / secs, title, prompt, secs))
            time.sleep(0.05)
        return False

    def toggle_gps(self):
        on = read_json(STATUS_FILE).get('gps_on')
        if on is None:
            self.show_flash("GPS state unknown", "radio not answering")
            return
        radio_key('gps off' if on else 'gps on')
        self.show_flash("GPS off" if on else "GPS on", "applied within a few s")

    def toggle_adv_pos(self):
        on = read_json(STATUS_FILE).get('adv_loc')
        if on is None:
            self.show_flash("Advert pos unknown", "radio not answering")
            return
        radio_key('advpos off' if on else 'advpos on')
        self.show_flash(f"Advert pos {'off' if on else 'on'}",
                        "adverts carry no position" if on else "adverts carry our position")

    def toggle_region(self):
        on, name = scope_cfg()
        if not on and not name:
            self.push("Region scope", self.region_items)
            return
        radio_key('scope off' if on else 'scope on')
        self.show_flash(f"Region scope {'off' if on else 'on'}", "applied within a few s")

    def region_items(self):
        cur = scope_cfg()[1]
        items = [(f"{'*' if s == cur else ' '}{label}", lambda s=s: self.pick_region(s))
                 for s, label in regions.PRESETS]
        if cur and cur not in dict(regions.PRESETS):
            items.insert(0, (f"*{cur}", lambda: None))
        return items

    def pick_region(self, scope):
        radio_key(f'scope set {scope}')
        self.stack.pop()
        self.show_flash(f"Scope: {scope}", "applied within a few s" if scope_cfg()[0]
                        else "now turn Region scope on")

    def toggle_survey(self):
        on = not survey_on()
        radio_key('survey on' if on else 'survey off')
        self.show_flash(f"Survey {'on' if on else 'off'}",
                        "pings repeaters as you move" if on else "listen-only logging")

    def toggle_gps_skew(self):
        S['gps_skew'] = not S['gps_skew']; save_settings()
        push_radio_settings()
        self.show_flash(f"GPS skew {'on' if S['gps_skew'] else 'off'}", "from the next fix")

    def toggle_radio_flip(self):
        S['radio_flip'] = not S['radio_flip']; save_settings()
        push_radio_settings()

    def toggle_controls(self):
        S['flip_controls'] = not S['flip_controls']; save_settings()

    def push(self, title, items_fn):
        self.stack.append([title, items_fn, 0])

    def ask(self, question, cmd):
        self.confirm = (question, cmd)

    def toggle_input(self):
        if S['input'] == 'int':
            sw.int_disable()
            S['input'] = 'poll'; save_settings()
            self.show_flash("Poll mode")
            return
        lcd.show(draw_msg(W, H, ["Press the joystick", f"to test INT ({INT_CONFIRM_S}s)"], title="INTERRUPT"))
        try:
            ok = sw.int_confirm(INT_CONFIRM_S)
        except OSError:                     # INT line busy / not exported
            ok = False
        if ok:
            S['input'] = 'int'; save_settings()
            self.show_flash("Interrupt mode on")
        else:
            self.show_flash("No INT signal", "staying on Poll")

    def toggle_anim(self):
        S['anim'] = not S['anim']; save_settings()

    def toggle_led(self):
        S['led'] = not S['led']; save_settings()
        if not S['led']:
            sw.led(0)

    def handle(self, pressed):
        if S['flip_controls']:     # joystick turned 180 deg: swap up/down and left/right
            swaps = ((nav.UP, nav.DOWN), (nav.DOWN, nav.UP), (nav.LEFT, nav.RIGHT), (nav.RIGHT, nav.LEFT))
            pressed = (pressed & nav.CENTER) | sum(b for a, b in swaps if pressed & a)
        self.key = pressed          # logical direction, for the slide animation
        self.last_input = time.monotonic()
        self.flash = None
        if self.reader:             # up/down scroll, left/center back to the list
            if pressed & nav.UP:
                self.reader[1] = max(0, self.reader[1] - 1)
            elif pressed & nav.DOWN:
                self.reader[1] = min(max(0, self.reader[2] - 4), self.reader[1] + 1)
            elif pressed & (nav.LEFT | nav.CENTER):
                self.reader = None
            return
        if self.radio:              # left/right page, press select, up/down back to the Pi
            if pressed & (nav.UP | nav.DOWN):
                self.radio = False
            elif pressed & nav.LEFT:
                radio_key('prev')
            elif pressed & nav.RIGHT:
                radio_key('next')
            elif pressed & nav.CENTER:
                radio_key('enter')
            return
        if self.confirm:
            if pressed & nav.CENTER:
                question, cmd = self.confirm
                self.confirm = None
                lcd.show(draw_msg(W, H, [question.rstrip('?') + "..."]))
                subprocess.run(cmd)
            elif pressed & nav.LEFT:
                self.confirm = None
            return
        if self.page == 'wx':       # press: ping the station, right: back
            if pressed & nav.CENTER:
                if self.hold(nav.CENTER, "WEATHER", "Hold: ping station"):
                    radio_key('wxping')
                    self.show_flash("Pinging station...", "answer in ~10-30 s", secs=WX_FLASH_S)
                else:
                    self.show_flash("Cancelled", f"hold press {ADVERT_HOLD_S:g} s", secs=WX_FLASH_S)
            elif pressed & nav.UP:
                if self.hold(nav.UP, "WEATHER", "Hold: ask on channel"):
                    radio_key('wxchan')
                    self.show_flash("Asked on channel", "reply shows in Messages", secs=WX_FLASH_S)
                else:
                    self.show_flash("Cancelled", f"hold up {ADVERT_HOLD_S:g} s", secs=WX_FLASH_S)
            elif pressed & nav.DOWN:
                if self.hold(nav.DOWN, "WEATHER", "Hold: DM the station"):
                    radio_key('wxdm')
                    self.show_flash("DM sent to station", "flood until path known", secs=WX_FLASH_S)
                else:
                    self.show_flash("Cancelled", f"hold down {ADVERT_HOLD_S:g} s", secs=WX_FLASH_S)
            elif pressed & nav.RIGHT:
                self.page = None
            return
        if self.page == 'pub':      # up/down: next channel, press: read it all, right: channel list, left: back
            chans = [idx for idx, _ in chat_channels()]
            i = chans.index(self.pub_chan) if self.pub_chan in chans else 0
            if pressed & (nav.UP | nav.DOWN):
                self.pub_chan = chans[(i + (1 if pressed & nav.DOWN else -1)) % len(chans)]
            elif pressed & nav.CENTER:
                row = latest_in(self.pub_chan)
                if row:
                    self.reader = [row, 0, 1, True]
            elif pressed & nav.RIGHT:
                self.page = None
                self.push("CHANNELS", self.channel_items)
                self.stack[-1][2] = i
            elif pressed & nav.LEFT:
                self.page = None
            return
        if self.page == 'map':      # left/right zoom, press: names, down: back
            if pressed & nav.LEFT:
                self.map_zoom = min(len(MAP_ZOOMS_KM) - 1, self.map_zoom + 1)
            elif pressed & nav.RIGHT:
                self.map_zoom = max(0, self.map_zoom - 1)
            elif pressed & nav.CENTER:
                self.map_labels = not self.map_labels
            elif pressed & nav.DOWN:
                self.page = None
            return
        if self.page == 'wd':       # hold press: survey on/off, up: back
            if pressed & nav.CENTER:
                on = not survey_on()
                if self.hold(nav.CENTER, "SURVEY", f"Hold: survey {'on' if on else 'off'}"):
                    self.toggle_survey()
                else:
                    self.show_flash("Cancelled", f"hold press {ADVERT_HOLD_S:g} s", secs=WX_FLASH_S)
            elif pressed & nav.UP:
                self.page = None
            return
        if not self.stack:          # dashboard
            if pressed & nav.CENTER:
                self.push("MENU", self.main_items)
            elif pressed & nav.RIGHT:
                self.page, self.pub_scroll = 'pub', 0
                self.pub_chan = chat_channels()[0][0]   # most recent chat channel
            elif pressed & nav.LEFT:
                self.page = 'wx'
            elif pressed & nav.UP:
                self.page = 'map'
            elif pressed & nav.DOWN:
                self.page = 'wd'
            return
        entry = self.stack[-1]
        if entry[0] == "CHANNELS" and pressed & nav.LEFT:
            self.stack.pop()
            self.page = 'pub'       # back to the channel page it came from
            return
        items = entry[1]()
        if pressed & nav.UP:
            entry[2] = (entry[2] - 1) % len(items)
        elif pressed & nav.DOWN:
            entry[2] = (entry[2] + 1) % len(items)
        elif pressed & (nav.CENTER | nav.RIGHT):
            items[entry[2]][1]()
        elif pressed & nav.LEFT:
            self.stack.pop()

    def render(self, ip, disk):
        if self.radio and time.monotonic() - self.last_input > SCREEN_OFF_S:
            self.radio = False      # radio screen has timed out too
        if self.radio:
            return draw_msg(W, H, ["Joystick -> radio", "\u25c0\u25b6 page  \u25cf select", "\u25b2\u25bc exit"],
                            title="RADIO CONTROL", color=OK)
        if self.reader and time.monotonic() - self.last_input > MENU_IDLE:
            self.reader = None
        if self.reader:
            img, self.reader[2] = draw_reader(W, H, self.reader[0], self.reader[1],
                                              with_sender=len(self.reader) > 3)
            return img
        if self.flash and time.monotonic() < self.flash_until:
            return draw_msg(W, H, list(self.flash))
        if self.page and time.monotonic() - self.last_input > MENU_IDLE:
            self.page = None
        if self.page == 'wx':
            return draw_weather(W, H)
        if self.page == 'map':
            return draw_map(W, H, self.map_zoom, self.map_labels)
        if self.page == 'wd':
            return draw_wardrive(W, H)
        if self.page == 'pub':
            chans = [idx for idx, _ in chat_channels()]
            name = chan_name(self.pub_chan)
            pos = f" {chans.index(self.pub_chan) + 1}/{len(chans)}" if self.pub_chan in chans else ""
            row = latest_in(self.pub_chan)
            if not row:
                return draw_msg(W, H, [f"No {name} messages yet", "◀ back"], title=name.upper())
            img, self.pub_lines = draw_reader(W, H, row, 0, title=name.upper() + pos, with_sender=True)
            return img
        if self.confirm:
            return draw_msg(W, H, [self.confirm[0], "● yes    ◀ no"], title="CONFIRM")
        if self.stack and time.monotonic() - self.last_input > MENU_IDLE:
            self.stack.clear()
        if self.stack:
            title, items_fn, sel = self.stack[-1]
            return draw_menu(W, H, title, [label for label, _ in items_fn()], sel)
        return draw(W, H, ip, nas, disk)

def ease(i):
    f = i / ANIM_FRAMES
    return 1 - (1 - f) ** 3            # ease-out cubic

def slide(old, new, key):
    """New screen pushes in from the side of the joystick direction pressed."""
    dx = -W if key & nav.RIGHT else W if key & nav.LEFT else 0
    dy = -H if key & nav.DOWN else H if key & nav.UP else 0
    if not dx and not dy:              # press: forward, in from the right
        dx = -W
    for i in range(1, ANIM_FRAMES):
        e = ease(i)
        ox, oy = int(dx * e), int(dy * e)
        f = Image.new('RGB', (W, H), BG)
        f.paste(old, (ox, oy))
        f.paste(new, (ox - dx, oy - dy))
        lcd.show(f)

def glide(title, labels, sel_from, sel_to):
    """Highlight bar slides between two rows in the same visible window."""
    top = menu_top(sel_to, len(labels))
    y0, y1 = 16 + (sel_from - top) * 16, 16 + (sel_to - top) * 16
    for i in range(1, ANIM_FRAMES):
        lcd.show(draw_menu(W, H, title, labels, sel_to, int(y0 + (y1 - y0) * ease(i))))

def view_key(ui):
    return (len(ui.stack), ui.stack[-1][0] if ui.stack else None, ui.page, ui.reader is not None,
            ui.radio, ui.confirm is not None, ui.pub_chan if ui.page == 'pub' else None)

def status_color(ip):
    if not S['led']:
        return 0
    if not ip:
        return nav.RED
    return nav.BLUE if nas is None else nav.GREEN if nas else nav.YELLOW

# ---------------------------------------------------------------- main
lcd = ST7735(flip=S['flip'])
lcd.set_brightness(S['pi_bright'])
W, H = lcd.width, lcd.height

def on_term(*_):
    lcd.show(draw_msg(W, H, ["Shutting down..."]))
    if sw:
        drop_switch()
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)

lcd.show(draw_msg(W, H, ["Booting..."]))
threading.Thread(target=nas_loop, daemon=True).start()
time.sleep(1)

def wake_in(drawn_at):
    """Seconds until the next redraw: the full refresh, or the next title scroll step."""
    t = max(0.0, drawn_at + REFRESH - time.monotonic())
    return min(t, MARQUEE_DT) if _mq['active'] and screen_on else t

ui = UI()
led_color, drawn_at, last_img = None, 0.0, None
screen_on, last_act, last_rx = True, time.monotonic(), None
radio_cfg_at = 0.0
while True:
    try:
        if sw is None and time.monotonic() >= sw_retry:
            open_switch(); led_color = None

        pressed = 0
        if sw:
            try:     # fast scroll: up/down repeat while held in a list or a long message
                sw.repeat_mask = (nav.UP | nav.DOWN) if (ui.stack and not ui.confirm) or ui.reader else 0
                pressed = sw.wait(wake_in(drawn_at))
                if sw.int_dead:
                    sw.int_disable()
                    S['input'] = 'poll'; save_settings()
                    ui.show_flash("No INT signal", "switched to Poll")
            except OSError:                 # unplugged / bus error
                drop_switch(); ui.stack.clear(); ui.confirm = None
        else:
            time.sleep(wake_in(drawn_at))

        if time.monotonic() - radio_cfg_at > 60:   # re-apply after a radio reboot (setting is RAM-only)
            push_radio_settings(); radio_cfg_at = time.monotonic()
        rx = read_json(STATUS_FILE).get('rx_count')
        if rx is not None and rx != last_rx:   # new message (skip half-written reads)
            if last_rx is not None and S['msg_wake']:
                last_act = time.monotonic()
            last_rx = rx
        if pressed:
            last_act = time.monotonic()
            if not screen_on:
                pressed = 0                 # first press only wakes the screen
                radio_key('wake')           # ...and the T114's, so they time out together
            else:
                before = view_key(ui)
                sel_before = ui.stack[-1][2] if ui.stack else None
                ui.handle(pressed)
        want_on = not sw or time.monotonic() - last_act < SCREEN_OFF_S
        if want_on != screen_on:
            screen_on = want_on
            if screen_on:
                lcd.reinit()                # panel may have glitched while dark
            lcd.backlight(screen_on)
            drawn_at = 0.0
        if not screen_on:
            continue
        if not pressed and _mq['active'] and last_img is not None and time.monotonic() - drawn_at < REFRESH:
            lcd.show(repaint_header(last_img))      # scrolling title: header only
            continue
        if pressed or time.monotonic() - drawn_at >= REFRESH:
            ip = get_ip()
            _mq['active'] = False                   # set again by a title that has to scroll
            img = ui.render(ip, get_ingest())
            if pressed and S['anim'] and last_img is not None and not sw.repeating:
                if view_key(ui) != before:
                    slide(last_img, img, ui.key)
                elif ui.stack and sel_before is not None and ui.stack[-1][2] != sel_before:
                    title, items_fn, sel = ui.stack[-1]
                    labels = [label for label, _ in items_fn()]
                    if menu_top(sel_before, len(labels)) == menu_top(sel, len(labels)):
                        glide(title, labels, sel_before, sel)
            lcd.show(img)
            last_img = img
            drawn_at = time.monotonic()
            if sw and status_color(ip) != led_color:
                led_color = status_color(ip)
                sw.led(led_color)
    except Exception as e:
        lcd.show(draw_msg(W, H, [f"Error: {e}"]))
        time.sleep(REFRESH)
        drawn_at = time.monotonic()
