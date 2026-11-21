# MeshCore Pi Build Guide

Reference notes for two builds: a fixed **weather station** and a **portable/carry Pi** for testing. Connector type resolved as **RP-SMA** throughout (not standard SMA), based on the TUOLNK U.FL-to-RP-SMA-female pigtails in use.

---

## 1. Weather Station Build

**Base:** Raspberry Pi 3B + Weather HAT PRO (The Pi Hut) — BME280 sensor, RJ11 ports compatible with SparkFun Weather Meters (wind/rain/anemometer), onboard fan header for cooling the sensor away from CPU heat, external 5V power input.

**MeshCore radio:** Heltec T114 nRF52840 SX1262 LoRa Mesh Node, Bluetooth 5.0. Used as a USB companion radio, not a Pi HAT — plugs into a spare USB port, flashed with MeshCore's Companion Radio firmware. 915MHz US variant.

**Antenna (mast-mounted):**
- Waterproof 5.8dBi Meshtastic LoRa Fiberglass Antenna, N-Male connector, includes U.FL-to-N pigtail and mount bracket. Clamps to the wind vane mast.
- **Connector chain note:** since the T114's U.FL output routes through an RP-SMA-female pigtail (see TUOLNK cable below), the mast run needs an **RP-SMA-male to N-type** extension cable to bridge the pigtail to the fiberglass antenna's N-male end. A standard-SMA-to-N cable would NOT work here; RP-SMA and standard SMA are not interchangeable even though similarly labeled.
- U.FL-to-RP-SMA-female pigtail: one per build.

**Cable loss reference (915MHz, per 100ft):**
| Cable | Loss/100ft |
|---|---|
| RG58 | ~20-25 dB |
| LMR195 | 11.1 dB |
| LMR400 | 6.6 dB |

At ~10ft (3m) mast run: LMR195-equivalent ≈ 1.1dB loss — acceptable given LoRa's large link budget (~-130 to -140dBm sensitivity).

**Realistic MeshCore range (typical suburban terrain, decent antennas):** likely 1-3km single-hop with the fiberglass antenna at height; height and antenna quality matter more than transmit power.

**Power:** FORIOT 18650 Battery Holder. See section 3 for wiring notes (shared design with carry Pi power system).

**Charging cable:** Right-angle USB-A to Micro-5Pin cable — for charging the FORIOT battery board where a straight cable won't fit the enclosure.

**MQTT/5G bridge:** MeshCore has native MQTT bridge support (Room Server or Repeater firmware with MQTT enabled), but bridging requires either an ESP32-class radio (has WiFi) or an nRF52 radio (like the T114) connected via USB/serial to a host with the actual internet connection. A USB 5G dongle needs a full Linux host (the Pi) to manage drivers/networking — this can't run on the T114 alone. Note Austin Mesh guidance discourages internet gateways; see `provision/README.md` and the bot guidelines.

---

## 2. Carry / Testing Pi Build

**Base:** Raspberry Pi 3B + **Argon Controllable Fan HAT v1.5** — onboard **Nuvoton MCU** (`U2`), I2C fan control (target `0x1a`, pins 3/5), 4 fan-speed LEDs, dedicated **POWER button**, stacking 40-pin passthrough, powered **from** the Pi's 5V header. The old reason to drop a fan HAT (GPIO stacking vs a GPIO LoRa HAT) is moot now the T114 is USB. Install Argon's daemon (`argononed` / `argon-config`) for the temp-driven fan curve + button handling — worth keeping for a sealed enclosure in direct sun.

**Power on/off — the Fan HAT button does most of it:**
- **Documented button behavior** (Argon MCU-version Fan HAT manual, ManualsLib "Argon Audio Fan HAT" — same Nuvoton-MCU generation as this v1.5 board):

  | State | Press | Action |
  |---|---|---|
  | OFF | Short press | **Turn ON** |
  | ON | Short press | Nothing |
  | ON | Long press (>3 s) | Soft shutdown |
  | — | Double tap | Reboot |

  The Nuvoton MCU stays alive on the halted-Pi 5V rail and catches the short press to wake it. Confirmed after Argon's software is installed: `sudo poweroff` → wait for halt → short press boots it. (The *older passive* Argon Fan HAT — no MCU — only does shutdown/reboot, not power-on. This board has the MCU (`U2`), so power-on works.)
- **It can't hard-cut input power.** The manual notes soft shutdown happens "without cutting power" — the HAT is header-powered, not in-line with the 5V input, so "off" = halted state (~20–25 mA / ~0.1 W), not zero draw. Fine here — <3 % of the pack over a session. True zero-draw needs a latching controller (Witty Pi / PiJuice); not worth it.
- **Automated wake:** wire one T114 GPIO → **Pi pin 5 (GPIO3)**, the only hardware wake-from-halt pin. The nRF52 has an RTC and sips power, so it can pulse the Pi awake on a schedule or on a mesh keyword (`wx`), letting the Pi sleep between field queries. Pin 5 is shared 3 ways (fan-MCU I2C SCL, Argon button wake line, this jumper): use **open-drain / a transistor, brief LOW pulse only**; a pulse while the Pi is running just glitches one fan-I2C transaction (self-recovers). Tap pin 5 on top of the HAT's stacking header. The Argon button covers manual wake if you'd rather leave the Pi running for the session.

**MeshCore radio:** Heltec T114 nRF52840 SX1262 LoRa Mesh Node, via USB. Abandoned two earlier LoRa HAT options: the Waveshare SX1262 Pi HAT (poor reviews/compatibility) and an XYGStudy SX1262 HAT (confirmed incompatible — hides the SX1262 behind a proprietary onboard-MCU UART protocol, no direct SPI/GPIO access, can't run MeshCore firmware).

**Antenna (handheld/portable):** Long Range Meshtastic 915MHz LoRa Whip Antenna, **RP-SMA Male** connector. Correct connector to mate with the RP-SMA-female U.FL pigtail (below).

- **muzi works 915MHz Whip Antenna (standard SMA male)** — superseded, cancelled. Wrong connector polarity: standard SMA male has a pin, and the T114's pigtail is RP-SMA female, which also has a pin under the reverse-polarity spec, so the two do not mate. Do not use it if it ships anyway.

**U.FL-to-RP-SMA-female pigtail cables:** TUOLNK 5PCS RG178 U.FL/IPX to RP-SMA Female Pigtail, 6". 5-pack covers both T114 boards (weather station + carry Pi) plus spares.

**Power — 18650 battery solution:**
Uses existing 2000-3000mAh 18650 cells (any brand) in a **1S2P (parallel) configuration** — no balance charging needed, since parallel cells share the same voltage node by design; nothing to drift/balance.

- **FORIOT 18650 Battery Holder/Shield (2-Holder version)** — dual 18650 compartment, onboard boost converter to regulated 5V/3A output, Micro-USB and USB-C charging inputs, built-in protection IC.
- Solder/wire the board's 5V and GND output to the Pi's 5V pin (physical pin 2 or 4) and GND (physical pin 6) on the 40-pin header — NOT the 3.3V pins, which would bypass the Pi's onboard regulator and risk damage.

**Why 1S2P instead of 2S:** Parallel cells are electrically forced to the same voltage — nothing to balance. Gives more total capacity (~4000-6000mAh) at 3.7V nominal vs. 2S's higher voltage/same capacity; irrelevant either way since output is regulated to a fixed 5V regardless of pack topology.

---

## 3. Software Notes

- **MeshCore firmware:** flash the T114 via [flasher.meshcore.io](https://flasher.meshcore.io) or PlatformIO with **Companion Radio** firmware. T114s often ship pre-flashed with Meshtastic by default, not MeshCore — confirm/reflash before assuming MeshCore is already running.
- **Companion Radio firmware supports BLE, USB serial, and WiFi** (board-dependent) — once flashed, the T114 is reachable over Bluetooth, not just USB.
- **Carry Pi software — meshcore-cli (`meshcore-cli` / `meshcli`):** Python CLI/lib that talks to a Companion Radio over **BLE, USB serial, or TCP**. On a headless always-attached T114, use **USB serial** (`--serial /dev/ttyACM0`) — no BLE pairing flakiness, no bluetooth stack. Installed in the node venv at `/opt/mesh-stack/venv/bin/meshcore-cli`. It drives the two priority use cases:
  - **Weather-on-command:** `meshbot.py` watches the `#bot` channel / DMs for a `wx` / `weather` keyword, reads the latest row from the **local** weather-station DB, and replies with one compact line (< ~180 bytes). No internet weather API (Austin Mesh guideline 3).
  - **Scheduled telemetry logging:** the daemon logs every RX with rssi/snr/hops/timestamp to SQLite; a cron `meshcli` one-shot can also dump self/neighbor telemetry.
  - **Bonus:** interactive TUI for field messaging without pulling out the phone; contact import/export; radio-param tweaks.
  - **Caveat:** fast-moving project — pin a version; for the long-running daemon prefer importing the `meshcore` library over parsing CLI stdout.
- **meshcore-pi** (github.com/brianwiddas/meshcore-pi) — separate, lower-level Python implementation of the MeshCore protocol for running directly on a host with **direct SPI radio access** (e.g. a Waveshare-style HAT). Not the right tool for talking to a Companion Radio device like the T114 — meshcore-cli is the correct match for this setup.
- **MQTT bridge:** configure via MeshCore app/CLI or dedicated bridge firmware (e.g., `Meshcore-Repeater-MQTT-Gateway` on GitHub) — needs NTP sync for TLS if using secure MQTT. Austin Mesh discourages MQTT/internet gateways on the mesh; keep this off unless deliberately bridging.

---

## 4. Bill of Materials

| Item | Assigned to | Notes |
|---|---|---|
| Heltec T114 nRF52840 SX1262 (×2) | carry Pi + weather station | 915 MHz US; USB companion radio |
| RP-SMA-male 915 MHz whip antenna | carry Pi | mates with RP-SMA-female U.FL pigtail |
| 5.8dBi fiberglass antenna, N-male + U.FL-to-N pigtail | weather station | mast-mounted |
| RP-SMA-male to N-type extension cable | weather station | bridges pigtail to fiberglass antenna |
| TUOLNK U.FL-to-RP-SMA-female pigtails (5-pack) | shared | covers both T114s + spares |
| FORIOT 18650 holder/shield (2-cell) | shared design | boost to 5V/3A, USB-C + Micro-USB charge |
| Argon Controllable Fan HAT v1.5 | carry Pi | Nuvoton MCU, fan curve + power button |
| Weather HAT PRO (The Pi Hut) | weather station | BME280 + SparkFun weather-meter ports |
| Right-angle USB-A to Micro-5Pin cable | weather station | low-profile charge lead |
| muzi works standard-SMA whip | — | wrong connector polarity, cancelled |
| ELEGOO ESP32 (×3) | — | unrelated to this project |

---

## 5. Open Items / To-Verify

- [x] Mast extension cable (RP-SMA-male to N-type) — confirmed working, sourced separately
- [x] ELEGOO ESP32 3-pack — unrelated to this project, exclude
- [ ] Confirm the muzi works antenna order actually cancels before it ships
- [ ] Flash/confirm Companion Radio firmware (not default Meshtastic) on both T114 boards
- [ ] Wire `meshbot.py` to the T114 over USB serial and point it at the local weather DB
- [ ] Check FORIOT board's charging behavior — some cheap boards cut output during charge; verify pass-through (charge + power simultaneously) if the carry Pi needs to run while charging
- [ ] Weather HAT PRO fan: source a compatible 5V fan for the onboard header (The Pi Hut links one)
- [ ] Verify Pi 3B has clean shutdown handling to protect the SD card — abrupt power loss (not simply being powered off) is what damages SD cards

---

## 6. SBC + T114 Use Cases

A bare T114 is already a full mesh node (client / repeater / room server). The reason to bolt a Linux host onto it is everything the nRF52 **can't** do alone: real storage, a filesystem and database, IP networking (WiFi/Ethernet/cellular), USB peripherals, GPIO, a screen, and arbitrary code. Grouped by what the Pi actually contributes.

> Austin Mesh bot guidelines (https://www.austinmesh.org/join/bot-guidelines/) apply to anything that talks on the mesh: act only on `#bot` / DMs, keep long exchanges in DMs, avoid internet dependencies (local data + fallback), battery+solar for grid-down utility, keep the source public.

### 6.1 Bridging / backhaul — Pi supplies connectivity the T114 lacks
| Use case | Notes |
|---|---|
| **Internet ↔ mesh MQTT bridge** | Technically the canonical SBC use, but Austin Mesh discourages internet gateways — only do this deliberately and sparingly. |
| **Chat bridges** (Discord / Matrix / Telegram / SMS) | Small daemon on the Pi relays `#bot` ↔ external service. Same caution. |
| **WiFi captive-portal chat** | Pi runs an AP + tiny web chat page; phones with no MeshCore app join and post to the LoRa mesh through the Pi. Local, no internet needed. |

### 6.2 Data / observability — Pi remembers and analyzes
| Use case | Notes |
|---|---|
| **Packet + telemetry logger** | `meshcore-cli` → SQLite/InfluxDB → Grafana. Node last-heard, battery curves, SNR/RSSI/hop-count history. A bare node keeps almost none of this. |
| **Coverage survey rig** (portable build) | Pi + USB GPS + T114, walk/drive logging position vs. link quality → GeoJSON heatmap. Best justification for the carry Pi existing at all. |
| **Network health monitor / alerting** | Watchdog that notifies you (locally) when a repeater drops off or a battery node sags. |

### 6.3 Automation / services living on the mesh
| Use case | Notes |
|---|---|
| **Message-triggered GPIO** | `#bot` command → relay/gate/pump/lights. Auth with a shared token in the payload. |
| **Scheduled broadcasts** | Cron on the Pi posts the weather-station summary or announcements to `#bot` on an interval — keep it infrequent. |
| **Query bot** | `wx`, FAQ/help responder, unit conversions — request/response, answered from local data or a local LLM. |
| **Store-and-forward bulletin board** | Pi holds an offline message queue / notice board and replays to nodes when they reappear. |

### 6.4 Sensor / asset hub
| Use case | Notes |
|---|---|
| **Sensor aggregation** | Pi collects I2C/1-Wire/GPIO sensors, compresses, and publishes a summarized telemetry frame on a schedule — exactly the weather-station pattern, reusable for soil, tank level, power. |
| **Remote monitoring where there's no cell** | PIR / door / temperature at a cabin or gate; event alerts trickled over LoRa. |

### 6.5 Field / dev utility
| Use case | Notes |
|---|---|
| **meshcore-cli automation & integration tests** | Scripted send/receive, regression checks against firmware builds. |
| **Firmware build / flash server** | See 6.5.1 — primary value is **field recovery**, not bench convenience. |
| **Traffic generator** | Synthetic load / soak testing of the mesh under controlled conditions. |

**6.5.1 Flash server details**
- `pip install platformio` (already in the node venv); clone MeshCore and **pin a git tag** for reproducible builds. `pio run -e <t114_env> -t upload` flashes over USB.
- nRF52840 = **UF2 bootloader**: double-tap RESET → a `T114BOOT` USB drive mounts → `cp firmware.uf2` to it. A udev rule that auto-copies a staged UF2 on mount gives "double-tap and it self-flashes" fully headless.
- **Field recovery is the point:** keep a known-good `.uf2` **and** a known-good config backup on the carry Pi. A botched config or bad flash in the field becomes a 60-second fix instead of a trip back to the bench. Works with no internet.

### 6.6 Off-grid / emergency kit
Solar + Li-Ion + Pi + T114 in one box: local web chat (6.1), message logging (6.2), self-contained comms drop. Matches Austin Mesh guideline 4 (battery + solar for grid-down utility).

### 6.7 Power — session use vs. deployment
For **session use** (bring it out, use it for a few hours, recharge between times) the
Pi 3B's few-hour battery life is a non-issue and no solar or deep-sleep engineering is
needed. For a **fixed deployment**, size a solar panel and pack to the numbers below,
or switch to a lower-draw host.

| Host | Draw (LoRa RX idle + occasional TX + logging) | Runtime |
|---|---|---|
| Pi 3B | ~4–5 W | 2× 5000 mAh 1S2P (~37 Wh) → **~7–9 h**; 2000–3000 mAh cells → ~3–5 h |
| Pi 3B, halted | ~0.1 W | negligible — just a safe "off" between queries |
| T114 alone | mA-range | weeks — holds mesh presence while the Pi is off |

- **Extending a session:** carry spare charged 18650s and hot-swap, or feed the charge
  input from a power bank.
- **End of session:** clean `poweroff` first (SD-card safety), *then* cut the pack
  output. Bind the shutdown to the Argon button or a mesh command (see §2).
- **Duty-cycling:** for weather-on-command, let the T114 wake the Pi via GPIO3 (§2) so
  the Pi isn't drawing ~5 W while it waits between queries.

---

## 7. SD / OS provisioning

Full procedure in [`provision/README.md`](provision/README.md). Summary:

- **OS:** Raspberry Pi OS Lite **64-bit** (Bookworm), fully headless. Flash with Raspberry Pi Imager; the README covers the settings (hostname, key-only SSH, a 2.4 GHz network with the country set, your time zone).
- **Boot-partition edit:** append [`provision/config.txt.append`](provision/config.txt.append) to `config.txt` on the `bootfs` partition — enables I²C (joystick 0x20 + Argon MCU 0x1a + UPS HAT 0x43), SPI0 (TFT), UART0 (T114 link), `disable-bt`, trims GPU/audio/camera.
- **First boot:** `provision/setup.sh` (idempotent) — `apt full-upgrade`, strips `triggerhappy`/`modemmanager`/`hciuart`, SD hardening (swap→zram, volatile journald, `/var/log` tmpfs, `noatime`), runs Argon's Fan HAT installer, builds `/opt/mesh-stack/venv` with `meshcore-cli` + screen/joystick libs + `platformio`, installs `sms-lcd`, `sms-meshbot` and `sms-web` services.
- **Automated wake:** T114 GPIO → Pi **pin 5 (GPIO3)**, ~200 ms LOW pulse (open-drain/transistor) = hardware boot from halt, no software. `sms-meshbot` powers the box back down when idle (`IDLE_POWEROFF_SEC`). App code under `provision/app/` is scaffolded — the meshcore serial client and weather source still need wiring.
