# Features and hardware reference

A portable [MeshCore](https://meshcore.co.uk/) node built on a Raspberry Pi 3B and a
Heltec T114 (nRF52840 / SX1262) companion radio, powered from 18650 cells for use
away from mains power.

## Overview

The node provides two services on the mesh:

- **Weather on command** — replies to `wx` on the bot channel or via direct message,
  using data from a local weather station on the mesh. No external weather API.
- **Telemetry logging** — records received-packet metadata (RSSI, SNR, hop count,
  timestamp) to a local SQLite database.

It also has its own screen and joystick (status, message browser, adverts, and
control of the T114's screen), and carries a PlatformIO toolchain for building and
flashing T114 firmware in the field, plus `meshcore-cli` for interactive use.

## Screen and joystick

The 0.96" TFT (`sms-lcd` service, [`lcd.py`](../provision/app/lcd.py)) shows a dashboard:
radio name and status, time since the last message and message count, battery
voltage (green, yellow or red by level; 🔌 while charging), GPS satellites in use (green
when locked, yellow without a lock, grey when off), IP and date. The header shows 12-hour local time (`SMS_TZ`), taken
from the radio's GPS-synced clock when it has one.

On the dashboard:

| Joystick | Opens |
|---|---|
| Press | The menu (below) |
| Right | Latest message on the most recent chat channel. Up/down: next channel. Press: read it in full. Right: channel list, newest first. Left: back. Bot channels (names with `bot`, `wx`, `weather`, `test` or `wardriv`) are left out. |
| Left | Weather station page: temperature, humidity, dew point, pressure or station battery, rain, wind compass. Hold 1.5 s: up = the command word on the weather channel, down = the same word as a DM to the station, press = telemetry ping. Right: back. |
| Up | Map: contacts with a known position and the last week's coverage dots around our GPS fix (left/right zoom, press = names, down back) |
| Down | Wardrive: last repeater that heard us (SNR, hops, age), today's repeaters / echoes / rows, GPS satellites, survey state. Hold press 1.5 s: survey on/off. Up: back |

Page changes slide in from the direction pushed, and the menu highlight glides between
rows (Settings → Animations turns both off). Holding up/down in a list or a long message
scrolls fast.

The weather page reads the station bot's text reply (`✈: 76F | 71%💧 | 💨 6MPH SW …`)
from a DM or a channel and shows its numbers. A telemetry reply fills the same page
once the station's radio carries its sensors. Requests to the station flood until it answers,
then go direct. After the node moves more than a mile, every learned route is dropped so
messages flood again.

Joystick menu:

| Item | What it does |
|---|---|
| Messages | Last 50 messages, newest first; open one to read it wrapped and scrolled |
| Fox aerial | Fox aerial repeater telemetry from Mission Planner: Fox bot on/off (switching on announces the bot on `#aerial`), auto-broadcast on `#aerial` every 2 min on/off, send it to `#aerial` now, link state, then battery and flight time left, speed, altitude and up time, and position. See [provision/README.md](../provision/README.md#8a-fox-aerial-repeater-telemetry) |
| Send advert | Zero-hop (nearby) or flood (whole mesh) |
| Radio control | Joystick drives the T114's own screen: left/right page, press select, up or down back to the Pi |
| Settings | Screen flip, control flip, message-wake for the Pi and the radio, radio screen flip, Pi and radio brightness, animations, input mode, status LED |
| Wi-Fi | AP mode on/off toggle; client network Home / Mobile router / Auto (all kept across reboots) |
| About | Version and license, plus a scrolling list of credits; press a credit for what it provides |
| Restart / Shut down | Reboot or power off the Pi |

The screen turns off after 15 s idle, the same as the T114's screen. A joystick press or
a new message (if enabled) wakes both screens.

## Hardware

| Component | Part |
|---|---|
| Host | Raspberry Pi 3B |
| Radio | Heltec T114 nRF52840 SX1262, 915 MHz, patched companion firmware; companion protocol over a P1-header UART to the Pi (`/dev/serial0`, pins 8/10), USB for flashing |
| GPS | Foxeer M10Q-180 (u-blox M10) on the T114's P1 header |
| Cooling / power button | Argon Controllable Fan HAT v1.5 (button on GPIO4, pin 7 — keep free) |
| Display | 0.96" 80×160 ST7735S IPS on SPI0: DC pin 22, RES 18, BLK 12, CS 24 (CE0), SCL 23, SDA 19 |
| Input | SparkFun Qwiic Navigation Switch (PCA9554, I²C `0x20`); INT on pin 11 (optional) |
| Antenna | U.FL → RP-SMA bulkhead pigtail, mounted on the case lid |
| Power | Waveshare UPS HAT (D) with 21700 cells; INA219 at `0x43` feeds `sms-power` |
| Wake | T114 GPIO → Pi GPIO3 (pin 5), hardware wake-from-halt |
| Case | Printable T114 case on the Argon HAT standoffs — see [`hardware/t114-case/`](../hardware/t114-case/) |

Full hardware notes, including the fixed weather-station variant, are in
[`hardware-notes.md`](hardware-notes.md).

## Repository layout

| Path | Contents |
|---|---|
| [`hardware-notes.md`](hardware-notes.md) | Hardware reference for both builds; wake wiring; power budget; use cases |
| [`provision/`](../provision/) | OS image setup, `config.txt` additions, `setup.sh`, systemd units, application code |
| [`provision/README.md`](../provision/README.md) | Flashing procedure, first boot, `meshcore-cli` usage, updating the node |
| [`provision/app/meshbot.py`](../provision/app/meshbot.py) | Bot daemon: `wx` / `help`, RX logging, optional idle-poweroff |
| [`provision/app/wardrive.py`](../provision/app/wardrive.py) | Coverage log: repeaters that hear us, by GPS position (Coverage tab, map dots) |
| [`provision/maps/fetch_tiles.py`](../provision/maps/fetch_tiles.py) | Offline maps for the Coverage tab: a Protomaps extract (dark) and USGS topo, any bounding box, into `/opt/mesh-stack/maps/*.mbtiles` |
| [`provision/app/lcd.py`](../provision/app/lcd.py) | TFT dashboard and joystick menu (`sms-lcd`); drivers [`st7735.py`](../provision/app/st7735.py), [`navswitch.py`](../provision/app/navswitch.py) |
| [`provision/firmware/t114/`](../provision/firmware/t114/) | Patched T114 companion build (MeshCore 1.18.0): patches, `build.sh`, `.uf2`/`.zip` |
| [`hardware/t114-case/`](../hardware/t114-case/) | Parametric CadQuery case (`t114_case.py`), printable `step/` and `stl/`, view-only `reference/` assemblies |

### T114 firmware patches

| Patch | Adds |
|---|---|
| `ext-batt.patch` | `set ext.batt <mV> [chg]`: the Pi feeds its pack voltage to the radio |
| `gps-p1-header.patch` | u-blox M10 GPS on the P1 header |
| `pi-uart.patch` | Companion protocol on a P1-header UART to the Pi |
| `ui-inject.patch` | `ui next\|prev\|enter\|select\|wake`, `ui msgwake on\|off`, `time force <epoch>` |
| `portrait.patch` | T114 screen used vertically (72×128 layout); `ui flip on\|off` turns it 180° |
| `clock-sanity.patch` | Ignores far-future contact times at boot; pulls them back when the clock is set back |
| `brightness.patch` | `ui bright <5-100>` dims the T114's backlight (PWM) |
| `gps-skew.patch` | `gps skew on\|off`: shared position moved 200 m |
| `ble-pin.patch` | BLE pairing pin stays on screen while the Pi is connected |
| `gps-fix.patch` | `gps fix`: lock, satellite count and fix age, for the dashboard |

Flash over USB: serial DFU with `adafruit-nrfutil dfu serial -pkg <zip> -p <port> -b 115200 --singlebank --touch 1200`, or copy the `.uf2` to the boot drive.

## Getting started

See [`provision/README.md`](../provision/README.md). In short: flash Raspberry Pi OS
Lite 64-bit with Raspberry Pi Imager, append `provision/config.txt.append` to the
boot partition, then run `provision/setup.sh` over SSH.

## MeshCore app over the network

`meshbot` runs a radio multiplexer ([`mux.py`](../provision/app/mux.py)) that owns the T114's
serial port, so the official MeshCore app can connect (Network/TCP, port **5000**) while the bot,
telemetry and wardrive log keep running. Peers must be on the tailnet (`100.64.0.0/10`) or
loopback; add more with `SMS_MUX_ALLOW=192.168.50.0/24`. `SMS_MUX=0` restores direct serial, and
`SMS_MUX_PORT=0` keeps the multiplexer but closes the app port. Commands are serialized, so a
big contacts download from the app delays the bot's commands for a few seconds. Tests:
`python -m unittest provision/tests/test_mux.py`.

## Mesh conduct

The node follows the
[Austin Mesh bot guidelines](https://www.austinmesh.org/join/bot-guidelines/):

- operates only on the bot channel and direct messages, and only when addressed
- moves multi-message exchanges to direct messages
- serves local data with a graceful fallback rather than depending on the internet
- keeps replies to a single packet, with per-sender rate limiting

## Roadmap

- [x] OS provisioning, hardening, and service scaffolding
- [x] `meshbot.py`: T114 serial transport, command handling, RX logging
- [x] T114 ↔ Pi over the P1-header UART (no USB needed in normal use)
- [x] GPS (u-blox M10) in the T114 firmware; Pi clock set from the radio when offline
- [x] TFT + joystick UI: dashboard, message browser, adverts, settings, power menu
- [x] Joystick passthrough to the T114 screen; paired screen timeout (`ui-inject.patch`)
- [x] Radio clock correction from the Pi (`time force`)
- [x] Portrait layout for the T114 screen (`portrait.patch`, firmware `1.18.0-sms-portrait`)
- [x] Old OLED/button UI removed; its Wi-Fi AP toggle moved to the joystick menu
- [x] Printable case (CadQuery source, STEP/STL), test-printed and fit-checked
- [x] Services default to the UART link (`/dev/serial0`)
- [x] GPS fix confirmed (the radio's `gps` setting had been off); lock shown on the dashboard
- [x] Coverage log for wardriving: echoes of our `#wardriving` pings, repeaters heard, opt-in discover survey
- [ ] Bench test against a second radio (DM + bot channel)
- [x] UPS HAT undervoltage dips: caused by the T114's USB inrush on attach; gone since the
  radio moved to the UART link (no undervoltage at full 4-core load, on charger or battery)
- [x] Low-battery shutdown raised to 3.4 V (3.3 V immediate) after a flat-battery death
- [x] Weather station polling over the mesh (telemetry → `readings` cache) and a weather page
- [x] Map page from GPS + contact positions; Pi and radio brightness
- [x] Weather page from the station bot's text reply; channel pages; animations; fast scroll
- [x] Web UI with fonts bundled for offline AP use
- [x] Routes reset to flood after moving a mile
- [ ] Field test of scheduled wake (T114 → Pi GPIO3)
- [ ] T114 firmware: RTC / keyword-triggered wake pulse

## License

Released under the [MIT License](../LICENSE).
