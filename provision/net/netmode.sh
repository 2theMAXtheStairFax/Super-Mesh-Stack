#!/usr/bin/env bash
# netmode - Wi-Fi as a client (home or mobile router) or as a self-hosted AP.
#
# Two settings, both kept across reboots in $STATE:
#   AP   on | off              on = host the AP; off = join a client network
#   NET  auto | home | mobile  which client network to prefer while AP is off
#
#   netmode.sh ap on|off|toggle   AP mode on / off (off rejoins NET)
#   netmode.sh home|mobile|auto   pick the client network (turns AP off)
#   netmode.sh status             print AP, NET and what wlan0 is doing now
#   netmode.sh boot               apply the saved settings (sms-netmode.service)
#   netmode.sh prep               create the AP profile only
#   (legacy: 'netmode.sh ap' = ap on, 'client' = ap off)
#
# home / mobile are a preference: if that network isn't in range the other one is tried.
# With AP off and no known network in range, the AP comes up anyway as a rescue so the
# node is never unreachable; AP stays 'off' in the settings, and the next 'boot' or
# 'ap off' tries the client networks again.
#
# Config via env (see sms-netmode.service):
#   SMS_AP_SSID     default "mesh-stack"
#   SMS_AP_PASS     required, 8-63 chars (set in /etc/mesh-stack/stack.env)
#   SMS_HOME_CON    NetworkManager home connection, default "preconfigured"
#                   (SMS_CLIENT_CON is still honoured as the old name)
#   SMS_MOBILE_CON  NetworkManager mobile-router connection, default "sms-mobile"
set -euo pipefail

AP_CON="sms-ap"
AP_SSID="${SMS_AP_SSID:-mesh-stack}"
AP_PASS="${SMS_AP_PASS:-}"
HOME_CON="${SMS_HOME_CON:-${SMS_CLIENT_CON:-preconfigured}}"
MOBILE_CON="${SMS_MOBILE_CON:-sms-mobile}"
STATE=/var/lib/mesh-stack/netmode.conf
PRIO_HI=20 PRIO_LO=10           # NetworkManager autoconnect priorities: preferred / other

AP=off NET=auto
# shellcheck source=/dev/null
[ -r "$STATE" ] && . "$STATE"

save() {
  mkdir -p "$(dirname "$STATE")"
  printf 'AP=%s\nNET=%s\n' "$AP" "$NET" > "$STATE.tmp" && chmod 644 "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

wifi_dev() { nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}'; }
has_con()  { nmcli -t -f NAME connection show | grep -qxF "$1"; }
con_ssid() { nmcli -t -f 802-11-wireless.ssid connection show "$1" 2>/dev/null | cut -d: -f2-; }

ensure_ap_profile() {
  [ "${#AP_PASS}" -ge 8 ] && [ "${#AP_PASS}" -le 63 ] || { echo "netmode: SMS_AP_PASS must be 8-63 characters" >&2; exit 1; }
  has_con "$AP_CON" && return
  nmcli connection add type wifi ifname "$(wifi_dev)" con-name "$AP_CON" \
    autoconnect no ssid "$AP_SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$AP_PASS" >/dev/null
}

go_ap() {
  ensure_ap_profile
  nmcli connection modify "$AP_CON" wifi-sec.psk "$AP_PASS" 802-11-wireless.ssid "$AP_SSID"
  nmcli connection up "$AP_CON" >/dev/null
  echo "netmode: AP '$AP_SSID' up on $(wifi_dev) (10.42.0.1)"
}

# Client connections to try, preferred first. Autoconnect priorities are set to match,
# so NetworkManager's own reconnects after a dropout follow the same preference.
client_order() {
  case "$NET" in
    mobile) echo "$MOBILE_CON $HOME_CON" ;;
    *)      echo "$HOME_CON $MOBILE_CON" ;;
  esac
}

set_priorities() {
  local first=1 c
  for c in $(client_order); do
    has_con "$c" || continue
    nmcli connection modify "$c" connection.autoconnect yes \
      connection.autoconnect-priority "$([ $first = 1 ] && echo $PRIO_HI || echo $PRIO_LO)"
    first=0
  done
}

go_client() {
  set_priorities
  nmcli connection down "$AP_CON" >/dev/null 2>&1 || true
  nmcli device wifi rescan 2>/dev/null || true
  sleep 4
  local seen c ssid
  seen="$(nmcli -t -f SSID device wifi list 2>/dev/null || true)"
  for c in $(client_order); do
    has_con "$c" || continue
    ssid="$(con_ssid "$c")"
    if [ -n "$ssid" ] && grep -qxF "$ssid" <<<"$seen"; then
      if nmcli connection up "$c" >/dev/null 2>&1; then
        echo "netmode: client '$c' ($ssid) up"; return 0
      fi
      echo "netmode: '$c' ($ssid) in range but failed to connect" >&2
    fi
  done
  echo "netmode: no known Wi-Fi joined - rescue AP (AP setting stays off)"
  go_ap
}

apply() { if [ "$AP" = on ]; then go_ap; else go_client; fi; }

status() {
  local dev
  dev="$(wifi_dev)"
  echo "AP=$AP NET=$NET"
  echo "wlan=$(nmcli -t -f GENERAL.CONNECTION device show "$dev" 2>/dev/null | cut -d: -f2-)"
  nmcli -t -f IP4.ADDRESS device show "$dev" 2>/dev/null | cut -d: -f2- | sed 's/^/ip=/'
}

case "${1:-boot}" in
  prep)   ensure_ap_profile; nmcli -f connection.id,802-11-wireless.mode,802-11-wireless.ssid connection show "$AP_CON" ;;
  ap)
    case "${2:-on}" in
      on)     AP=on ;;
      off)    AP=off ;;
      toggle) AP=$([ "$AP" = on ] && echo off || echo on) ;;
      *) echo "usage: netmode.sh ap {on|off|toggle}" >&2; exit 2 ;;
    esac
    save; apply ;;
  client) AP=off; save; apply ;;
  home|mobile|auto) NET="$1"; AP=off; save; apply ;;
  boot)   apply ;;
  status) status ;;
  *) echo "usage: netmode.sh {ap on|off|toggle | home|mobile|auto | status | boot}" >&2; exit 2 ;;
esac
