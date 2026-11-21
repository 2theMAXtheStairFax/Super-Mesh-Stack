#!/usr/bin/env bash
# Add the name-only (#hashtag) channels listed in /etc/mesh-stack/channels.txt to the radio.
# Stops the bot while it holds the radio, skips channels already present.
set -euo pipefail
LIST=${1:-/etc/mesh-stack/channels.txt}
CLI=/opt/mesh-stack/venv/bin/meshcore-cli
PORT=${SMS_SERIAL:-/dev/serial0}
[ -f /etc/mesh-stack/stack.env ] && PORT=$(sed -n "s/^SMS_SERIAL='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" /etc/mesh-stack/stack.env)
[ -r "$LIST" ] || { echo "no channel list at $LIST" >&2; exit 1; }

systemctl stop sms-meshbot
trap 'systemctl start sms-meshbot' EXIT
have=$("$CLI" -s "$PORT" get_channels)
while IFS= read -r ch; do
  ch=${ch%%#*}; ch=$(echo "$ch" | xargs)
  [ -n "$ch" ] || continue
  if grep -qiF -- "$ch" <<<"$have"; then echo "have $ch"; else "$CLI" -s "$PORT" add_channel "$ch" && echo "added $ch"; fi
done <"$LIST"
