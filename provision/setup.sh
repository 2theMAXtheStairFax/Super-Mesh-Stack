#!/usr/bin/env bash
# Super Mesh Stack - post-first-boot provisioning. Idempotent; safe to re-run.
# Reads the configurator's stack.env (STACK_ENV=<path>, default ./stack.env).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo -E bash "$0" "$@"

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR=/opt/mesh-stack
CONF_DIR=/etc/mesh-stack
STACK_ENV="${STACK_ENV:-$HERE/stack.env}"
APP_USER="${SUDO_USER:-}"
[ -n "$APP_USER" ] && [ "$APP_USER" != root ] || { echo "run as a normal user with sudo, not as root" >&2; exit 1; }

[ -f "$STACK_ENV" ] || { echo "no $STACK_ENV - build one at the configurator, or copy stack.env.example" >&2; exit 1; }
set -a
# shellcheck source=/dev/null
. "$STACK_ENV"
set +a

: "${STACK_LCD:=1}" "${STACK_POWER:=0}" "${STACK_FOX:=0}" "${STACK_BRIDGE:=0}" "${STACK_ARGON:=0}"
: "${STACK_HARDEN:=1}" "${STACK_AVAHI:=1}" "${STACK_UART:=1}" "${STACK_PLATFORMIO:=0}"
: "${SMS_AP_SSID:=mesh-stack}" "${SMS_AP_PASS:=}" "${STACK_WIFI_COUNTRY:=}"
: "${STACK_HOME_SSID:=}" "${STACK_HOME_PSK:=}" "${STACK_MOBILE_SSID:=}" "${STACK_MOBILE_PSK:=}"

if [ -z "$SMS_AP_PASS" ]; then
  SMS_AP_PASS="$(tr -dc 'a-km-np-zA-HJ-NP-Z2-9' </dev/urandom | head -c 14)"
  GENERATED_AP_PASS=1
fi

echo ">> base packages"
apt-get update
apt-get -y full-upgrade
apt-get -y install --no-install-recommends \
    python3-venv python3-dev python3-pip python3-serial \
    i2c-tools git curl ca-certificates openocd zram-tools

echo ">> strip unused services"
PURGE="triggerhappy modemmanager"
[ "$STACK_AVAHI" = 1 ] || PURGE="$PURGE avahi-daemon"
for s in $PURGE; do systemctl disable --now "$s" 2>/dev/null || true; done
apt-get -y purge $PURGE 2>/dev/null || true
[ "$STACK_UART" = 1 ] && systemctl disable --now hciuart 2>/dev/null || true
apt-get -y autoremove --purge

if [ "$STACK_HARDEN" = 1 ]; then
  echo ">> SD-card wear / power-loss hardening"
  if dpkg -l dphys-swapfile >/dev/null 2>&1; then
    dphys-swapfile swapoff || true
    systemctl disable --now dphys-swapfile || true
    apt-get -y purge dphys-swapfile || true
  fi
  printf 'ALGO=zstd\nPERCENT=50\nPRIORITY=100\n' >/etc/default/zramswap
  systemctl enable --now zramswap

  mkdir -p /etc/systemd/journald.conf.d
  printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\n' >/etc/systemd/journald.conf.d/00-mesh-stack.conf
  systemctl restart systemd-journald

  grep -q '[[:space:]]/var/log[[:space:]]' /etc/fstab ||
    echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,nodev,size=32M 0 0' >>/etc/fstab
  sed -i 's#\(\s/\s.*ext4\s\+\)defaults#\1defaults,noatime#' /etc/fstab || true
fi

echo ">> groups"
usermod -aG i2c,gpio,spi,dialout "$APP_USER"

if [ "$STACK_UART" = 1 ]; then
  echo ">> UART0 (/dev/serial0) belongs to the radio link, not a login console"
  CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
  sed -i -E 's/ ?console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' "$CMDLINE"
  systemctl disable --now serial-getty@ttyAMA0.service serial-getty@serial0.service 2>/dev/null || true
fi

echo ">> sudoers"
cat >/etc/sudoers.d/mesh-stack <<EOF
$APP_USER ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/systemctl reboot, /usr/bin/date -s *, $APP_DIR/net/netmode.sh, $APP_DIR/flash.sh
EOF
chmod 0440 /etc/sudoers.d/mesh-stack
visudo -cf /etc/sudoers.d/mesh-stack >/dev/null

if [ "$STACK_ARGON" = 1 ] && ! systemctl list-unit-files 2>/dev/null | grep -q '^argononed'; then
  echo ">> Argon Fan HAT"
  curl -fsSL https://download.argon40.com/argonfanhat.sh -o /tmp/argonfanhat.sh
  bash /tmp/argonfanhat.sh </dev/null || echo "!! Argon installer needs a re-run interactively"
fi

if [ "$STACK_LCD" = 1 ]; then
  apt-get install -y fonts-dejavu-core fonts-noto-color-emoji
fi

echo ">> python venv"
PIP="gpiod spidev numpy pillow psutil gpiozero lgpio smbus2 bottle meshcore-cli"
[ "$STACK_FOX" = 1 ] && PIP="$PIP pymavlink"
[ "$STACK_PLATFORMIO" = 1 ] && PIP="$PIP platformio"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
[ -x "$APP_DIR/venv/bin/python" ] || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade $PIP

install -o "$APP_USER" -g "$APP_USER" -m0644 "$HERE"/app/*.py "$APP_DIR"/
install -o "$APP_USER" -g "$APP_USER" -m0755 "$HERE"/app/flash.sh "$HERE"/app/add-channels.sh "$APP_DIR/"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR/static" "$APP_DIR/firmware" "$APP_DIR/net"
cp -r "$HERE"/app/static/. "$APP_DIR/static/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/static"
install -o "$APP_USER" -g "$APP_USER" -m0644 "$HERE"/firmware/t114/*.uf2 "$APP_DIR/firmware/"
install -o "$APP_USER" -g "$APP_USER" -m0755 "$HERE"/net/netmode.sh "$APP_DIR/net/"

echo ">> configuration"
install -d -m0755 "$CONF_DIR"
{ grep -vE '^(SMS_AP_PASS|STACK_(HOME|MOBILE)_PSK)=' "$STACK_ENV"; printf "SMS_AP_PASS='%s'\n" "$SMS_AP_PASS"; } >"$CONF_DIR/stack.env"
chmod 0640 "$CONF_DIR/stack.env"
[ -f "$HERE/channels.txt" ] && install -m0644 "$HERE/channels.txt" "$CONF_DIR/channels.txt"

add_wifi() {   # name ssid psk
  nmcli connection delete "$1" >/dev/null 2>&1 || true
  nmcli connection add type wifi con-name "$1" ssid "$2" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$3" connection.autoconnect yes >/dev/null
}
if command -v nmcli >/dev/null; then
  if [ -n "$STACK_WIFI_COUNTRY" ] && command -v raspi-config >/dev/null; then
    raspi-config nonint do_wifi_country "$STACK_WIFI_COUNTRY" || echo "!! could not set the Wi-Fi country"
  fi
  if [ -n "$STACK_HOME_SSID" ]; then
    add_wifi "${SMS_HOME_CON:-sms-home}" "$STACK_HOME_SSID" "$STACK_HOME_PSK"
  fi
  if [ -n "$STACK_MOBILE_SSID" ]; then
    add_wifi "${SMS_MOBILE_CON:-sms-mobile}" "$STACK_MOBILE_SSID" "$STACK_MOBILE_PSK"
  fi
fi

echo ">> services"
UNITS="sms-meshbot sms-web sms-netmode"
[ "$STACK_LCD" = 1 ] && UNITS="$UNITS sms-lcd"
[ "$STACK_FOX" = 1 ] && UNITS="$UNITS sms-fox"
for u in sms-lcd sms-meshbot sms-web sms-power sms-bridge sms-fox sms-netmode; do
  sed "s/^User=.*/User=$APP_USER/" "$HERE/systemd/$u.service" >/etc/systemd/system/$u.service
done
systemctl daemon-reload
# shellcheck disable=SC2086
systemctl enable --now $UNITS
for u in sms-lcd sms-fox; do case " $UNITS " in *" $u "*) ;; *) systemctl disable --now $u 2>/dev/null || true ;; esac; done

# bridge and power are mutually exclusive with meshbot / need hardware: opt-in only
systemctl disable sms-bridge.service 2>/dev/null || true
if [ "$STACK_POWER" = 1 ]; then systemctl enable --now sms-power.service; else systemctl disable --now sms-power.service 2>/dev/null || true; fi

echo
echo ">> done. 'sudo reboot' to apply config.txt (I2C, SPI, UART) and overlays."
echo "   add channels:  sudo $APP_DIR/add-channels.sh"
echo "   check:         systemctl status sms-meshbot sms-web sms-lcd sms-netmode"
[ -z "${GENERATED_AP_PASS:-}" ] || echo "   generated AP password for '$SMS_AP_SSID': $SMS_AP_PASS  (kept in $CONF_DIR/stack.env)"
