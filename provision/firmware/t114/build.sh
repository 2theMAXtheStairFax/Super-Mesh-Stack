#!/usr/bin/env bash
# Rebuild the SFM T114 companion firmware: upstream MeshCore at a pinned tag +
# ext-batt.patch (host-fed battery) + gps-p1-header.patch (Foxeer M10Q-180 /
# u-blox M10 GPS wired to the P1 header, BLE+USB companion env; GPS=l76k swaps it for
# gps-l76k.patch, which keeps the stock L76K pins) +
# pi-uart.patch (companion protocol also on a P1-header UART to the Pi) +
# ui-inject.patch ("ui ..." CLI command so the Pi joystick drives the T114 screen) +
# portrait.patch (vertical 64x128 screen layout, runtime 180-degree flip) +
# gps-skew.patch ("gps skew on|off": shared GPS position offset 200 m) +
# ble-pin.patch (BLE pairing pin stays on screen while the Pi is connected) +
# gps-fix.patch ("gps fix": lock, satellites and fix age for the Pi).
# Outputs .uf2 files next to this script.
#
#   ./build.sh                    # uses TAG below, M10 GPS
#   GPS=l76k ./build.sh           # stock L76K GPS pins instead of the M10
#   TAG=companion-v1.19.0 ./build.sh
#
# Needs git + PlatformIO (`pio`). On Windows, set a short workspace to dodge
# long-path failures: PLATFORMIO_WORKSPACE_DIR=C:/Users/<you>/.platformio/wst114
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TAG=${TAG:-companion-v1.18.0}
GPS=${GPS:-m10}      # m10 | l76k
case "$GPS" in
  m10)  GPS_PATCH=gps-p1-header.patch; SUFFIX=sfm-portrait ;;
  l76k) GPS_PATCH=gps-l76k.patch;      SUFFIX=sfm-l76k ;;
  *)    echo "GPS must be m10 or l76k" >&2; exit 2 ;;
esac
VER=${TAG#companion-v}-$SUFFIX      # <= 19 chars (device-info field)
SRC=${SRC:-$HERE/.src}
ENVS=(Heltec_t114_companion_radio_ble_usb)

rm -rf "$SRC"
git clone -q --depth 1 --branch "$TAG" https://github.com/meshcore-dev/MeshCore.git "$SRC"
git -C "$SRC" apply "$HERE/ext-batt.patch"
git -C "$SRC" apply "$HERE/$GPS_PATCH"
git -C "$SRC" apply "$HERE/pi-uart.patch"
git -C "$SRC" apply "$HERE/ui-inject.patch"     # "ui next|prev|enter|select|wake" from the Pi
git -C "$SRC" apply "$HERE/portrait.patch"      # screen used vertically + "ui flip on|off"
git -C "$SRC" apply "$HERE/clock-sanity.patch"  # no far-future contact times; fix them when the clock is set back
git -C "$SRC" apply "$HERE/brightness.patch"    # "ui bright <5-100>" screen backlight level
git -C "$SRC" apply "$HERE/gps-skew.patch"      # "gps skew on|off" - shared GPS position offset 200 m
git -C "$SRC" apply "$HERE/ble-pin.patch"       # show the BLE pin while the Pi holds the USB link
git -C "$SRC" apply "$HERE/gps-fix.patch"       # "gps fix" - lock state for the Pi

export PLATFORMIO_BUILD_FLAGS="-DFIRMWARE_VERSION='\"$VER\"'"
(cd "$SRC" && pio run $(printf -- '-e %s ' "${ENVS[@]}"))

BUILD=${PLATFORMIO_WORKSPACE_DIR:-$SRC/.pio}/build
for e in "${ENVS[@]}"; do   # same hex -> uf2 step as upstream build.sh
  rm -f "$HERE/$e"-*-"$SUFFIX".uf2 "$HERE/$e"-*-"$SUFFIX".zip   # old builds of this GPS variant only
  python3 "$SRC/bin/uf2conv/uf2conv.py" "$BUILD/$e/firmware.hex" -c -f 0xADA52840 \
    -o "$HERE/$e-$VER.uf2"
  cp "$BUILD/$e/firmware.zip" "$HERE/$e-$VER.zip"   # BLE OTA DFU package
done
ls -l "$HERE"/*.uf2
