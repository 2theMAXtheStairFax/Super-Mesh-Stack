# Provisioning

Turns a blank SD card into a headless mobile MeshCore node.

**Target:** Raspberry Pi 3B, Raspberry Pi OS Lite (64-bit, Bookworm).
**Interfaces:** SSH, the web UI, plus a 0.96" TFT and a 5-way joystick for field use.

Names used below are the shipped defaults — change them to suit your deployment:

| Thing | Default | Set via |
|---|---|---|
| hostname | `mesh-stack` | Raspberry Pi Imager |
| login user | the account you create in Imager | Imager; `setup.sh` adopts it |
| install path | `/opt/mesh-stack` | fixed |
| services | `sms-lcd`, `sms-meshbot`, `sms-web`, `sms-power` | rename the unit files if desired |

---

## 1. Flash the base image

Raspberry Pi Imager (≥ 1.8):

- **Device:** Raspberry Pi 3
- **OS:** Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)
- **Storage:** your card

**Edit Settings → General**

- Hostname — your choice
- Username / password — your choice (password is a console fallback; SSH uses a key)
- Wireless LAN — your SSID and passphrase. **The Pi 3B is 2.4 GHz only**, so pick a
  2.4 GHz network. Set the Wireless LAN country or the radio stays disabled.
- Locale — your time zone and keyboard layout

**Edit Settings → Services**

- Enable SSH → *Allow public-key authentication only*, and paste your public key.
  Generate one first if needed: `ssh-keygen -t ed25519`; the public key is
  `~/.ssh/id_ed25519.pub`.

**Edit Settings → Options**

- Disable telemetry
- Enable "eject when finished" (re-insert afterwards for step 2)

Write the image.

---

## 2. Apply the boot-partition config

Re-insert the card. On the `bootfs` partition, append
[`config.txt.append`](config.txt.append) to the end of `config.txt`.

Linux/macOS:
```bash
cat config.txt.append >> /path/to/bootfs/config.txt
```
Windows PowerShell (adjust the drive letter):
```powershell
Get-Content .\config.txt.append | Add-Content D:\config.txt
```

This enables I²C (joystick, Argon MCU, UPS HAT), SPI0 for the TFT, and UART0 for the
radio link, disables the Bluetooth stack, and trims unused GPU/audio/camera options.

Eject, move the card to the Pi, and power on. First boot takes 1–2 minutes.

---

## 3. First boot

```bash
ssh <user>@<hostname>.local          # or ssh <user>@<node-ip>
git clone <repo-url> ~/super-mesh-stack
# copy stack.env and channels.txt from the configurator bundle into ~/super-mesh-stack/provision/
cd ~/super-mesh-stack/provision
./setup.sh
sudo reboot
```

`setup.sh` is idempotent and does:

- `apt full-upgrade`; installs a minimal stack (Python venv, `i2c-tools`, `git`,
  `zram-tools`)
- removes `triggerhappy`, `modemmanager`, `hciuart`
- SD-wear / power-loss hardening: swap → zram only, `journald` volatile and capped,
  `/var/log` on tmpfs, `noatime`
- runs Argon's Fan HAT installer (temperature fan curve + power button)
- creates the app venv with `meshcore-cli`, `gpiod`/`spidev`/`Pillow` (screen + joystick), `platformio`
- adds a sudoers rule so the app may `poweroff`/`reboot`, switch Wi-Fi mode, flash, and set the clock
- installs and enables the `sms-lcd`, `sms-meshbot` and `sms-web` services

Settings come from `stack.env` (see the configurator); setup installs it to `/etc/mesh-stack/stack.env`,
which every service reads.

**If Wi-Fi doesn't come up:** confirm the SSID is 2.4 GHz and the country is set, then
reconfigure with `sudo nmtui` or `nmcli`. A pre-created connection profile from Imager
is named `preconfigured`.

---

## 4. Automated wake

The Pi's "off" state is a halt: the 5 V rail stays live (~0.1 W) and USB ports stay
powered, so the radio keeps running on USB and keeps its RTC.

- **Wake:** radio GPIO → Pi **pin 5 (GPIO3)**, pulsed **LOW ~200 ms**, boots the Pi
  from halt. This is a SoC hardware feature — no overlay or daemon. Use an open-drain
  output or a transistor: pin 5 is also I²C SCL and the Argon button line, so a pulse
  while the Pi is running glitches one I²C frame (it recovers).
- **Sleep:** `sms-meshbot` calls `systemctl poweroff` after handling a request, or
  after `IDLE_POWEROFF_SEC` with no traffic. No GPIO needed.
- **Manual wake:** the Argon power button (short press when halted).

Radio firmware side: on an RTC schedule or on receiving a designated message, drive
the pin low for ~200 ms then release (hi-Z). Ground is shared over the USB cable.

---

## 5. Pin map

| Signal | BCM | Pin | Notes |
|---|---|---|---|
| I²C SDA | GPIO2 | 3 | joystick `0x20`, Argon MCU `0x1a`, UPS HAT `0x43` / `0x2d` |
| I²C SCL / wake | GPIO3 | 5 | I²C clock + radio wake pulse |
| Argon button | GPIO4 | 7 | Argon power button — keep free |
| UART TX → T114 | GPIO14 | 8 | T114 P1-8 (RX) |
| UART RX ← T114 | GPIO15 | 10 | T114 P1-6 (TX) |
| Joystick INT | GPIO17 | 11 | optional, active low |
| TFT BLK | GPIO18 | 12 | backlight |
| TFT RES | GPIO24 | 18 | reset |
| TFT SDA | GPIO10 | 19 | SPI0 MOSI |
| TFT DC | GPIO25 | 22 | data/command |
| TFT SCL | GPIO11 | 23 | SPI0 clock |
| TFT CS | GPIO8 | 24 | SPI0 CE0 |
| Argon Fan HAT | GPIO2/3/4 | — | managed by `argononed` |

The radio talks to the Pi over the UART (`/dev/serial0`); its USB is only needed for
flashing.

---

## 6. Using meshcore-cli

Installed in the app venv. Plug the radio in first (`ls /dev/ttyACM*`).

```bash
VENV=/opt/mesh-stack/venv/bin

$VENV/meshcore-cli --serial /dev/serial0                 # interactive TUI
$VENV/meshcore-cli --serial /dev/serial0 infos           # device / firmware info
$VENV/meshcore-cli --serial /dev/serial0 contacts        # known contacts
$VENV/meshcore-cli --serial /dev/serial0 send "<name>" "hi"
$VENV/meshcore-cli --serial /dev/serial0 chan_send 0 "hi"
```

- The login user is in `dialout`, so `/dev/serial0` (or `/dev/ttyACM0` on USB) needs no `sudo`.
- Stop `sms-meshbot` first; only one program can hold the radio's serial port.
- `sms-meshbot` also holds the serial port — `sudo systemctl stop sms-meshbot` to
  free it for interactive use.
- If the radio still runs Meshtastic, reflash it to MeshCore **Companion Radio**
  first (see [`../docs/hardware-notes.md`](../docs/hardware-notes.md) §3).

---

## 6a. Network bridge — use the official app over Wi-Fi instead of USB/BLE

`sms-bridge.service` is a passive TCP↔serial bridge: it forwards raw bytes between
a TCP port and the radio's serial port, with zero protocol awareness — unlike
full-management tools (e.g. RemoteTerm), it never touches contacts or channels,
so it doesn't care which radio happens to be plugged in.

**Exclusive with `sms-meshbot`** — both want the serial port. The two unit files
declare `Conflicts=` on each other, so starting one automatically stops the other:

```bash
sudo systemctl start sms-bridge     # stops sms-meshbot, opens :5000
sudo systemctl start sms-meshbot    # stops sms-bridge, back to bot mode
```

In the MeshCore app, add a connection over **Network/TCP** to this node's
address, port `5000` (same default `meshcore-cli -t <host> -p <port>` uses).
`SMS_BRIDGE_PORT` in the unit file to change it.

---

## 7. Updating

```bash
cd ~/super-mesh-stack && git pull
./provision/setup.sh                # re-syncs services, app code, and the venv
```

For a code-only change, skip the full run:
```bash
sudo install -m0644 provision/systemd/*.service /etc/systemd/system/
sudo install -m0644 provision/app/*.py /opt/mesh-stack/
sudo systemctl daemon-reload && sudo systemctl restart sms-meshbot sms-lcd
```

---

## 8. Configuring the bot

Runtime settings live in **`/opt/mesh-stack/config.json`** and are edited from the
web UI's **Config** tab (or by hand). `sms-meshbot` re-reads it every ~5 s — no
restart needed.

| Key | Meaning |
|---|---|
| `bot_channel_idx` | channel the bot replies on; `-1` = DMs only |
| `cmd_prefix` | on a channel the bot only answers `<prefix> wx` / `<prefix>-wx` / bare `<prefix>`; DMs also take bare `wx` |
| `wx_channel_idx` | channel the Weather tab's Refresh button sends `<prefix> wx` on |
| `web_send_default_idx` | channel pre-selected in the Messages send box |
| `idle_poweroff_sec` | silence before the bot powers the Pi off; `0` = never |
| `fox_mavlink` | where `sms-fox` reads the Fox's MAVLink (default `udpin:0.0.0.0:14550`, see 8a) |
| `fox_bot` | Fox bot on/off (default off). Off: no `$fox` replies and no auto posts. Switching it on posts the online announcement on `#aerial` |
| `fox_auto` / `fox_auto_sec` | post Fox telemetry on `#aerial` on a timer (default off, every 120 s, 60 s minimum) |
| `fox_reserve_pct` | battery % the flight time estimate counts as empty (default 20) |
| `region_scope_on` / `region_scope` | flood scope for everything the radio floods, e.g. `us-tx-aus` (default off, see below) |

The systemd unit still carries these as **defaults** (env vars `SMS_*`), plus the
fixed paths: `SMS_SERIAL` (`/dev/serial0`; `/dev/ttyACM0` if the T114 is on USB), `SMS_DB`
(`/opt/mesh-stack/telemetry.sqlite`), `SMS_WEATHER_DB`
(`/opt/mesh-stack/weather.sqlite`, `readings` schema — see
[`../docs/notes/weather.md`](../docs/notes/weather.md)).

### Region scopes

Off by default: meshes that don't use scopes need unscoped floods. For
a locale that does, pick a scope from the screen (**Settings → Scope**, then **Region
scope** on/off) or the Config tab, where a custom name also works. Presets are the
Texas draft at [comchan.net/region-scopes](https://comchan.net/region-scopes/).

`sms-meshbot` sets the radio's *default flood scope*, so it covers channel posts,
adverts and DMs sent without a known path. Only repeaters carrying that scope forward
them, so enable it only where nearby repeaters do. Turning it off clears the scope,
but only one this bot set; a scope chosen in the phone app is left alone. Check what
the radio is using in the Config tab footer or `journalctl -u sms-meshbot | grep scope`.

### Position in adverts

Whether the radio's adverts carry its position (the map pin other nodes see) is a
setting on the radio itself, saved in its prefs: **Settings → Advert pos** on the
screen, or the toggle under the Config tab's form. Off sends adverts with no position;
coverage logging and the GPS clock still use the GPS. Telemetry location sharing is a
separate radio permission and is not changed here.

### Hashtag channels (name-only, no shared secret)

A MeshCore channel whose name starts with `#` derives its key deterministically:
`key = SHA256("#name")[:16]`. Anyone who adds the same name is on the same channel —
no key to exchange. `meshcore-cli` adds them into the first free slot:

```bash
meshcore-cli -s /dev/serial0 add_channel "#bot"
meshcore-cli -s /dev/serial0 add_channel "#testing"
meshcore-cli -s /dev/serial0 get_channels          # list slots + keys
```

Their docs write the bot channel both ways (`#bot` / `#bots`); Discord traffic
uses `#bot`, so this node treats `#bot` as primary and keeps `#bots` loaded too.
Add the same names in the phone app to see the same traffic.

This node ships with:

| idx | channel | role |
|---|---|---|
| 0 | Public | default, everyone |
| 1 | `#bot` | bot answers here; Weather Refresh sends here (Discord traffic uses `#bot`) |
| 2 | `#testing` | scratch |
| 3–5 | `#meetup` `#jokes` `#music` | logged/visible, no bot action |

Bring-up check with a second radio (same `#bot` name loaded): send `wx wx` in
`#bot`, or DM the bot node bare `wx` / `help`, and confirm a reply.
`journalctl -u sms-meshbot -f` shows every received packet.

## 8a. Fox aerial repeater telemetry

The Fox aerial repeater flies on an FMS Fox 3M glider. On the mesh it is an ordinary
repeater that relays traffic back to the ground. Its flight controller's telemetry
reaches Mission Planner on a laptop, and Mission Planner mirrors that stream over the
local network to this Pi. `sms-fox` ([`fox.py`](app/fox.py)) reads it and writes
`fox.json`. It **only listens**: nothing is sent towards the aircraft.

**Mission Planner (laptop, same network as the Pi, or joined to the Pi's AP):**
press **Ctrl+F**, choose **MAVLink Mirror**, pick **UDP Client**, then enter the
Pi's IP (`10.42.0.1` on the AP) and port `14550`. Leave **Write access** unticked.
To pull from a Mission Planner TCP host instead, set `fox_mavlink` to
`tcp:<laptop ip>:<port>` in the Config tab.

**On the mesh:** anyone who sends `$fox` on `#aerial` or `#bot` gets one line back
(each sender is rate-limited to once every 45 s):

```
🦊Fox: 11.8V 64% | up 23m | ~28m left | 34mph 410ft | 30.26812,-97.74301
```

The **Fox bot** switch (screen **Fox aerial** menu, or the Config tab) turns all of
this on or off. It starts **off**, so nothing is answered or posted until you turn it
on for a flight. Switching it on posts once on `#aerial`:

```
Great Fox Aerial Repeater Bot Online, use $fox for telemetry updates.
```

A reboot or service restart doesn't repeat the announcement. Only the off-to-on switch does.

The fields are pack voltage and %, flight controller up time, estimated flight time
left (down to `fox_reserve_pct`), ground speed and height above home, and GPS position.
If no MAVLink has arrived for 15 s, the reply is `no telemetry for …`. Turning on
**auto-broadcast** (screen **Fox aerial** menu or the Config tab) posts the same line
on `#aerial` straight away and then every `fox_auto_sec`. Posts are skipped while the
link is down, so a dropped laptop doesn't flood the channel.

The flight time estimate uses, in order: the autopilot's own `time_remaining`, then
mAh used ÷ time since boot (an average, so motor climbs and glides even out; needs a
current sensor and `BATT_CAPACITY` set), then the battery % slope over the last 10 min.

`#aerial` is a name-only hashtag channel. `sms-meshbot` loads it into the first free
slot at startup if the radio doesn't have it. Add `#aerial` in the phone app to follow
along. Check it with `journalctl -u sms-fox -f`; `[fox] autopilot is system 1` means
the stream is arriving.

---

## 9. Wi-Fi mode + walk-up web UI

The Pi 3B has one Wi-Fi radio, so it is **either** a client **or** an access point.

Two settings, saved in `/var/lib/mesh-stack/netmode.conf` and kept across reboots:

- **AP** `on` / `off` — host the AP (`SMS_AP_SSID` and `SMS_AP_PASS` in `/etc/mesh-stack/stack.env`;
  the passphrase is required, setup generates one if you leave it blank)
- **NET** `home` / `mobile` / `auto` — client network while AP is off. Home is the
  `preconfigured` connection, mobile is `sms-mobile` (a travel router or phone hotspot, created from
  `STACK_MOBILE_SSID` at setup). `home`/`mobile` is a preference — the
  other is tried if it isn't in range; `auto` tries home first. NetworkManager
  autoconnect priorities are set to match, so reconnects after a dropout follow it too.

`sms-netmode.service` runs `netmode.sh boot` to apply them. With AP off and no known
network joinable, the AP comes up anyway as a **rescue** (the saved setting stays off,
so the next boot or `ap off` tries the client networks again).

By hand (`sudo` allowed for the app user):

```bash
sudo /opt/mesh-stack/net/netmode.sh ap on       # AP mode on (persists)
sudo /opt/mesh-stack/net/netmode.sh ap off      # AP off, join the saved client network
sudo /opt/mesh-stack/net/netmode.sh ap toggle
sudo /opt/mesh-stack/net/netmode.sh home        # prefer home Wi-Fi (AP off)
sudo /opt/mesh-stack/net/netmode.sh mobile      # prefer the mobile router (AP off)
sudo /opt/mesh-stack/net/netmode.sh auto        # home, else mobile (AP off)
sudo /opt/mesh-stack/net/netmode.sh status
```

or the joystick menu: **Wi-Fi → AP mode: ON/off** (toggle), **Home Wi-Fi**,
**Mobile router**, **Auto** (`*` marks the saved choice).

**Pi 3B + WPA3:** the BCM43430 radio can't join a router in WPA2/WPA3 mixed mode — the
association is rejected with `status_code=16` (`journalctl -u wpa_supplicant`). Set the
router's 2.4 GHz security to WPA2-PSK only.

### The web UI (`sms-web`, port 80)

`http://<hostname>.local/` on the home Wi-Fi, or `http://10.42.0.1/` on the AP.
Built on `bottle`, styled with Oswald / Bungee Hairline fonts served
from `static/`, so it looks the same offline on the AP). Five tabs:

| Tab | Does |
|---|---|
| **Messages** | Discord-style: sidebar of channels, DM threads and repeaters/rooms (search finds any contact, or starts a DM); the open conversation refreshes every 3 s. Repeaters open a console: **Login** (admin password, never stored), **Status**, **Logout**, and CLI commands from the message box, with replies shown inline |
| **Weather** | last cached reading, parsed; **Refresh** sends `<prefix>` (default `wx`) on `wx_channel_idx`; the station's reply is parsed into the reading |
| **Contacts** | node's contacts + a copy-paste `meshcore://contact/add?…` URI for each (import into the station radio via the phone app) and an import box for URIs coming back |
| **Config** | edit `config.json` (channels shown by name); bot applies it in ~5 s |
| **Flash** | shows the radio's firmware; flashes the bundled **patched build** or the **stock** latest release over a **USB cable** (Pi → T114) or **internal SWD wiring** (Pi GPIO → T114 debug pads), with no button presses: stops the bot, flashes, restarts the bot. See [`firmware/t114/`](firmware/t114/README.md#flashing) |

`/events` is still a plain-text tail. Swap `/opt/mesh-stack/static/logo.png` for a
different logo.

**Walk-up UI** — `sms-web.service` serves on port 80:

- client mode: `http://<hostname>.local/` or `http://<pi-ip>/`
- AP mode: join the AP on your phone, then `http://10.42.0.1/`

Shows node status and the last 25 mesh messages; `/events` is a plain-text tail.
A send box appears only when `SMS_WEB_SEND_TARGET` is set to a channel index in
`sms-web.service` — submitted lines go into the `outbox` table and `sms-meshbot`
transmits them on that channel.

Android will warn "network has no internet" when you join the AP — stay connected;
the phone keeps cellular for data and uses Wi-Fi for the node.

---

## 10. Battery monitor (INA219 / UPS HAT)

> Status: `sms-power` is installed but **disabled** pending a Waveshare UPS HAT (D).
> That board has its own INA219 at `0x40` and does charge-and-output pass-through,
> so `power.py` will target it directly (no series shunt to wire). Re-enable with
> `sudo systemctl enable --now sms-power` once the hardware is in.

`sms-power.service` reads pack voltage/current from an **INA219 at `0x40`** on the
I²C bus, logs to `telemetry.sqlite`, writes `power.json` (shown on the screen
dashboard and the web header), warns once on the mesh at `SMS_BATT_WARN_V`, and runs a
clean `poweroff` on a sustained dip (`< SMS_BATT_CRIT_V` for `SMS_BATT_CRIT_COUNT`
polls) or immediately below `SMS_BATT_FLOOR_V`. Defaults: warn 3.6 V, crit 3.4 V,
floor 3.3 V (1S Li-ion). They were 3.5 / 3.2 / 3.0 until 2026-10-23, when the node ran flat
overnight without shutting down: at this setup's 0.7-1.5 A draw the 21700s fall off
a cliff below ~3.4 V and the HAT's output gave out before the Pi ever read 3.2 V. Tune in `/etc/systemd/system/sms-power.service`.

With the patched T114 build flashed, `sms-meshbot` also pushes the pack voltage to the
radio every 60 s, so the T114's battery icon, the phone app, and its telemetry show
the **Pi pack** instead of the T114's empty battery input. See
[`firmware/t114/README.md`](firmware/t114/README.md) (`SMS_BATT_CELLS` for a 2S pack).

### Wiring — series only, cold, fused

The INA219 goes **in series on battery +**, not across the battery (that shorts the
pack through the 0.1 Ω shunt and kills the board).

```
cells(+) --> Vin+ --[shunt]-- Vin- --> FORIOT B+ input
```

| INA219 | to |
|---|---|
| Vin+ | pack + |
| Vin- | FORIOT B+ / battery input pad |
| VCC | Pi 3V3 (pin 1) — not 5 V |
| GND | Pi GND (pin 9) |
| SDA / SCL | Pi pin 3 / pin 5 |

1. Cells out, Pi off, for all wiring.
2. Easiest tap without cutting a trace: feed the pack in through an external
   holder → INA219 → the board's `B+/B-` pads, onboard holders empty.
3. Ohmmeter `Vin+`↔`Vin-` should read ~0.1 Ω. A short means it's wired parallel — stop.
4. Triple-check `VCC`→3V3 and `GND`→GND (reversing kills the chip).
5. Power the Pi with **no battery**; `i2cdetect -y 1` shows `0x40`.
6. Switch the pack on; watch for heat. Then check: bus ~3.6–4.1 V, current positive.
7. Put a **3–5 A fuse in `B+` at the pack**.

### Refuelling

The FORIOT board briefly drops its output when a charger is connected, so there is
no live pass-through. Refuel with the system off: let the cutoff shut it down (or
do it by hand), swap a **matched, freshly-charged pair**, and boot (a Pi 3B starts
on power-apply). Charging the cells in place also works once the Pi is off.
