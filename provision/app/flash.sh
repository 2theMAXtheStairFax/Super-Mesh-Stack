#!/usr/bin/env bash
# T114 firmware flasher for the Flash tab, run from the Pi with no PC:
#   stop the bot -> pick a .uf2 -> flash by the chosen method -> restart the bot.
#
# Usage: flash.sh <usb|swd> [sfm|stock]
#   usb    USB cable from a Pi port to the T114. A 1200-baud touch drops the T114
#          into its UF2 bootloader, whose boot drive takes the .uf2.
#   swd    Internal wiring: Pi GPIO -> T114 SWD pads, driven by openocd. Needs no USB,
#          and still works when the app won't boot. Writes only the app region, so
#          the bootloader, contacts and settings stay.
#   sfm    newest bundled patched build in /opt/mesh-stack/firmware (default)
#   stock  latest upstream T114 companion release (download)
#
# SWD pins (BCM numbers) can be changed in /opt/mesh-stack/swd.env:
#   SWD_CLK=21  (header pin 40)   SWD_IO=20  (pin 38)   GND: pin 39
set -uo pipefail
LOG=/opt/mesh-stack/flash.log
FW_DIR=/opt/mesh-stack/firmware
APP_DIR=/opt/mesh-stack
METHOD=${1:-usb}
WHICH=${2:-sfm}
SWD_CLK=21
SWD_IO=20
[ -f "$APP_DIR/swd.env" ] && . "$APP_DIR/swd.env"
exec >"$LOG" 2>&1
echo "[flash] $(date -Is) method=$METHOD firmware=$WHICH"

restart_bot() { echo "[flash] restarting sms-meshbot"; systemctl start sms-meshbot; }
trap restart_bot EXIT

case "$METHOD" in usb|swd) ;; *) echo "[flash] unknown method '$METHOD' (usb|swd)"; exit 1 ;; esac

echo "[flash] stopping sms-meshbot"
systemctl stop sms-meshbot
sleep 1

fetch_stock() {
  echo "[flash] finding the latest T114 companion firmware..."
  API=https://api.github.com/repos/meshcore-dev/MeshCore/releases/latest
  URLS=$(curl -fsSL "$API" | grep -oE 'https://[^"]+\.uf2' || true)
  URL=$(echo "$URLS" | grep -iE 't114' | grep -iE 'companion' | grep -iE 'usb' | head -1)
  [ -z "$URL" ] && URL=$(echo "$URLS" | grep -iE 't114' | grep -iE 'companion' | head -1)
  if [ -z "$URL" ]; then
    echo "[flash] no T114 companion .uf2 in the latest release (no internet?)"; exit 1
  fi
  echo "[flash] $URL"
  curl -fsSL "$URL" -o /tmp/t114.uf2 || { echo "[flash] download failed"; exit 1; }
}

case "$WHICH" in
  sfm)   SRC=$(ls -t "$FW_DIR"/Heltec_t114_companion_radio_ble_usb-*sfm*.uf2 2>/dev/null | head -1)
         [ -n "$SRC" ] || { echo "[flash] no patched build in $FW_DIR"; exit 1; }
         echo "[flash] bundled $(basename "$SRC")"
         cp "$SRC" /tmp/t114.uf2 ;;
  stock) fetch_stock ;;
  *)     echo "[flash] unknown firmware '$WHICH' (sfm|stock)"; exit 1 ;;
esac
echo "[flash] got $(stat -c%s /tmp/t114.uf2) bytes"

flash_usb() {
  PORT=$(ls /dev/serial/by-id/*Heltec* 2>/dev/null | head -1)
  [ -z "$PORT" ] && PORT=$(ls /dev/ttyACM* 2>/dev/null | head -1)
  if [ -z "$PORT" ]; then
    echo "[flash] no T114 on USB. Connect the cable from a Pi USB port, or use SWD."; exit 1
  fi
  echo "[flash] 1200-baud touch on $PORT -> UF2 bootloader"
  python3 -c "import serial,time; s=serial.Serial('$PORT',1200); time.sleep(0.3); s.close()" 2>/dev/null \
    || stty -F "$PORT" 1200 2>/dev/null
  echo "[flash] waiting up to 30 s for the boot drive..."
  MP=""
  for i in $(seq 1 30); do
    DEV=$(lsblk -rno NAME,LABEL | awk '/BOOT/{print "/dev/"$1; exit}')
    if [ -n "$DEV" ]; then
      MP=$(lsblk -rno MOUNTPOINT "$DEV" | head -1)
      if [ -z "$MP" ]; then
        MP=/mnt/t114boot; mkdir -p "$MP"; mount "$DEV" "$MP" || MP=""
      fi
      [ -n "$MP" ] && break
    fi
    sleep 1
  done
  [ -n "$MP" ] || { echo "[flash] no boot drive appeared. Is it a data cable?"; exit 1; }
  echo "[flash] copying to $MP ($DEV)"
  cp /tmp/t114.uf2 "$MP"/ && sync
  sleep 3
  umount "$MP" 2>/dev/null || true
  echo "[flash] UF2 written - the T114 reboots into the new firmware"
}

flash_swd() {
  command -v openocd >/dev/null || { echo "[flash] openocd not installed (sudo apt install openocd)"; exit 1; }
  ADDR=$(python3 "$APP_DIR/uf2bin.py" /tmp/t114.uf2 /tmp/t114.bin) || { echo "[flash] bad UF2"; exit 1; }
  echo "[flash] app image $(stat -c%s /tmp/t114.bin) bytes at $ADDR; SWD clk=GPIO$SWD_CLK io=GPIO$SWD_IO"
  OCD=(openocd -f interface/raspberrypi2-native.cfg
       -c "adapter gpio swclk $SWD_CLK" -c "adapter gpio swdio $SWD_IO"
       -c "transport select swd" -c "adapter speed 1000" -f target/nordic/nrf52.cfg)
  # APPROTECTSTATUS (CTRL-AP 0x0C) is 1 when the debug port is open. Locked chips can only be
  # opened by a full erase (nrf52_recover), which would wipe the bootloader too - so stop.
  ST=$("${OCD[@]}" -c "init" -c "echo [nrf52.dap apreg 1 0x0c]" -c "shutdown" 2>&1)
  echo "$ST" | tail -3
  if ! echo "$ST" | grep -qE '^(0x0*)?1$'; then
    echo "[flash] no SWD connection, or the chip is locked (APPROTECT). Check the wiring; not erasing."; exit 1
  fi
  "${OCD[@]}" -c "program /tmp/t114.bin $ADDR verify reset exit" || { echo "[flash] SWD programming failed"; exit 1; }
  echo "[flash] programmed and verified over SWD - the T114 is restarting"
}

"flash_$METHOD"
sleep 8
echo "[flash] done"
