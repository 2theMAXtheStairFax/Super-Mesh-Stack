# T114 firmware: patched companion build

Upstream MeshCore companion radio, pinned to `companion-v1.18.0`, env
`Heltec_t114_companion_radio_ble_usb`, with three patches applied in order:

| Patch | What it adds |
|---|---|
| [`ext-batt.patch`](ext-batt.patch) | The Pi pushes its pack voltage to the T114 (`set ext.batt`), below |
| [`gps-p1-header.patch`](gps-p1-header.patch) | Foxeer M10Q-180 (u-blox M10) GPS on P1-12/13 (`0.09`/`0.10`, `Serial1`, 115200). Forces GPS + Galileo + BeiDou + GLONASS + QZSS with UBX-CFG-VALSET on every GPS reset |
| [`pi-uart.patch`](pi-uart.patch) | Companion protocol on a hardware UART to the Pi, in addition to USB and BLE |

Version string: `1.18.0-sfm-gps-uart`, shown by `ver`, device info and the web
Flash tab. It's 19 characters, the device-info field's limit, so "batt" is
left out of the name. The ext.batt feature is still in this build.

## GPS skew (`gps-skew.patch`)

`gps skew on|off` (and `gps skew` to read it) moves the GPS position 200 m
before it's stored in `node_lat`/`node_lon`, so adverts, the app's self info and
location telemetry all carry the moved position. The T114's own GPS page still
shows the raw fix. The offset is fixed, on a bearing picked each time skew turns
on, so averaging many adverts doesn't recover the real spot. It applies from the
next GPS fix.

The setting lives in RAM and is **on at boot**, so nothing unskewed goes out
before the Pi re-sends its setting (lcd Settings → *GPS skew 200m*, re-pushed
every 60 s). GPS on/off itself is the stock `gps` custom var, which the radio
saves in its own prefs (lcd Settings → *GPS*).

## BLE pin with the Pi connected (`ble-pin.patch`)

With a screen fitted, the T114 picks a random BLE pin each boot and shows it on
the home page only while nothing is connected. The Pi's USB/UART link counts as
a connection, so the pin never appeared and phones couldn't pair. The patch
shows the pin whenever BLE is on and no phone is connected.

## Pi UART link (`pi-uart.patch`)

The companion protocol runs on `Serial2` (UARTE1) at 115200 8N1, alongside USB
and BLE. Frames go out on every interface, and commands are accepted from any
of them, so USB keeps working while the wires are being fitted.

| T114 | nRF pin | Pi |
|---|---|---|
| P1-6 (TX) | P0.16 | pin 10, GPIO15 RXD |
| P1-8 (RX) | P1.01 | pin 8, GPIO14 TXD |
| P1-4 (GND) | | pin 14 |

GPIO9/10 are the GPS's, so `PIN_SERIAL2_*` moves to 33/16. Upstream's
`SERIAL_RX` path in `companion_radio/main.cpp` uses the ESP32-only
`HardwareSerial(1)`. On nRF52 the patch uses the core's `Serial2` instead and
turns on RX's pull-up, so an unwired RX pin idles high instead of picking up
noise.

On the Pi, `config.txt.append` sets `enable_uart=1`, and `setup.sh` removes the
login console from `/dev/serial0`. To switch a service to the wires, set
`SMS_SERIAL=/dev/serial0` in `sms-meshbot.service` or `sms-bridge.service`.
With both USB and the wires connected, the two services can run at the same
time, one on each port.

## Host-fed battery (`ext-batt.patch`)

One change in `variants/heltec_t114/T114Board.h`: the Pi can push its own
battery voltage to the T114. The T114 then reports that value instead of its
own battery reading. That reading is meaningless here because the T114 runs
from the Pi with no cell attached.

## What the patch does

New CLI commands, handled in `T114Board::handleCommand()`, which 1.18 added for
variant-specific commands. They are reachable over USB (`CMD_RUN_CLI_COMMAND`) and
by remote-CLI contacts:

| Command | Effect |
|---|---|
| `set ext.batt <mV>` | use `<mV>` as the battery voltage (0–6000) |
| `set ext.batt <mV> chg` | same, and show the charging icon |
| `get ext.batt` | current value and age, or `(none, using onboard ADC)` |

While a value is less than 5 minutes old:

- `getBattMilliVolts()` returns it. That one function feeds the display battery
  icon, the app's battery readout (`CMD_GET_BATT_AND_STORAGE`), core stats and
  telemetry, including the 1.18 telemetry push.
- `isExternalPowered()` returns the `chg` flag. This drives the 1.18 charging
  icon, which would otherwise always show charging, because USB VBUS is always
  present.

When the value is stale (the Pi is halted or meshbot has stopped), both functions
fall back to the stock ADC / VBUS behaviour. The value is RAM only and is cleared
on reboot. The T114 build sets no `AUTO_SHUTDOWN_MILLIVOLTS`, so a low pushed value
can never shut the radio down. Shutdown decisions stay with `power.py`.

## Pi side

`meshbot.py` reads `power.json` (written by `sms-power`) every 60 s and sends
`set ext.batt <mV per cell> [chg]`:

| Env (sms-meshbot.service) | Default | |
|---|---|---|
| `SMS_BATT_CELLS` | `1` | cells in series; voltage is divided so 3.0–4.2 V/cell maps to 0–100% |
| `SMS_BATT_CHG_MA` | `-30` | INA219 current below this = charging (series wiring: discharge reads positive) |

When `power.json` is missing or more than 2 minutes old, meshbot sends nothing.
On stock firmware the command is rejected once with a log line and is otherwise
harmless.

## Flashing

From the Pi, web UI **Flash** tab (or `sudo /opt/mesh-stack/flash.sh <usb|swd> [sfm|stock]`),
with either link:

| Method | Wiring | How |
|---|---|---|
| **USB cable** | Pi USB port → T114 USB (a data cable) | 1200-baud touch drops the T114 into its UF2 bootloader; the `.uf2` is copied to the boot drive. No button presses |
| **Internal wiring (SWD)** | Pi pin 40 (GPIO21) → T114 SWCLK, pin 38 (GPIO20) → SWDIO, pin 39 → GND | `openocd` on the Pi's GPIO writes the app region at 0x26000 (`uf2bin.py` turns the `.uf2` into a binary). Bootloader, contacts and settings stay. Works even when the app won't boot |

SWD pins can be moved in `/opt/mesh-stack/swd.env` (`SWD_CLK=`, `SWD_IO=`, BCM numbers). The SWD
script checks the debug port is open first and stops if the chip is locked (APPROTECT): opening
it takes a full erase, which would also wipe the bootloader.

The flasher takes the newest `Heltec_t114_companion_radio_ble_usb-*sfm*.uf2` in
`/opt/mesh-stack/firmware/` (`setup.sh` installs them there).

From a Windows PC over USB, **shut the Pi down first** (see the "Loading..." problem in
`docs/hardware-notes.md`): 1200-baud touch on the app COM port, then
`adafruit-nrfutil dfu serial -pkg <zip> -p <bootloader COM port> -b 115200 --singlebank`.

## Rebuilding (new upstream release)

```bash
TAG=companion-v1.18.0 ./build.sh
```

If the patch no longer applies, re-make the same edit in the new `T114Board.h`
and regenerate the patch with `git diff > ext-batt.patch`.
