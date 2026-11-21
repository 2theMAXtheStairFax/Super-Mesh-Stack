# Super Mesh Stack

A [MeshCore](https://meshcore.co.uk/) node on a Raspberry Pi with a Heltec T114 radio: weather-on-command bot, telemetry and coverage logging, a screen and joystick for the field, a walk-up web UI, and the MeshCore phone app over the network. Battery powered if you want it.

**[Configurator](configurator/index.html)** &mdash; pick your hardware and options, download a bundle (`stack.env`, boot config, channel list, wiring table, install steps). It runs entirely in the page; nothing is uploaded. Open `configurator/index.html` from a clone, or use the hosted copy if GitHub Pages is enabled for the repo.

## Install

1. Build your bundle in the configurator.
2. Flash Raspberry Pi OS Lite 64-bit with Raspberry Pi Imager and append `config.txt.append` to the boot partition.
3. On the Pi:
   ```bash
   git clone <this repo> ~/super-mesh-stack
   # put the bundle's provision/stack.env and provision/channels.txt in ~/super-mesh-stack/provision/
   cd ~/super-mesh-stack/provision && ./setup.sh && sudo reboot
   sudo /opt/mesh-stack/add-channels.sh
   ```

`SETUP.md` in the bundle repeats this with your hostname, wiring and ports filled in. The long version is [`provision/README.md`](provision/README.md).

## What the configurator decides

| Choice | Effect |
|---|---|
| Radio link: UART or USB | serial device, `disable-bt`/`enable_uart` in the boot config, which firmware you need |
| Screen + joystick, Argon HAT, battery monitor | I&sup2;C/SPI overlays, which services are enabled, packages installed, wiring table |
| AP name and passphrase, home and mobile Wi-Fi | NetworkManager profiles and the rescue access point |
| Channels, bot channel, command word | `add-channels.sh` list, channel indexes the bot answers on |
| App access, allowed networks | multiplexer port and CIDR allow-list for the MeshCore app |
| Coverage log, aerial telemetry, flood scope, weather node | bot features and defaults |
| Time zone, hostname, SD hardening, mDNS | system settings |
| Map area | `tiles.sh` for offline maps, with a tile-count estimate |

Settings land in `/etc/mesh-stack/stack.env`, which every service reads. Fine-tuning afterwards is in the web UI's Config tab.

## Hardware

Tested on a Raspberry Pi 3B. The Pi 4B and Zero 2 W are offered but untested.

| Part | Notes |
|---|---|
| Radio | Heltec T114 (nRF52840 / SX1262). UART link needs the patched firmware in [`provision/firmware/t114/`](provision/firmware/t114/); USB works with stock companion firmware at reduced features |
| Screen | 0.96&Prime; 80&times;160 ST7735S on SPI0, with a SparkFun Qwiic navigation switch |
| Power | INA219 or Waveshare UPS HAT (D), Argon Fan HAT optional |
| GPS | u-blox M10 on the T114's P1 header (patched firmware) |
| Case | printable, parametric: [`hardware/t114-case/`](hardware/t114-case/) |

More in [`docs/hardware-notes.md`](docs/hardware-notes.md) and [`docs/features.md`](docs/features.md).

## Security

- The web UI (port 80) and the app port have no authentication. The web UI can send messages, log in to repeaters and flash firmware. Run the node on a network you trust, and keep `Extra allowed networks` narrow.
- The access point passphrase is required (8&ndash;63 characters). Leave it blank in the configurator and setup generates one.
- `stack.env` holds passphrases. It is gitignored; do not paste it anywhere.
- The bundled `.uf2` files are built from the patches in this repo against upstream MeshCore. Rebuild with [`build.sh`](provision/firmware/t114/build.sh) if you would rather not trust a binary.

## Mesh conduct

The bot is modelled on the [Austin Mesh bot guidelines](https://www.austinmesh.org/join/bot-guidelines/): it speaks only on its bot channel and in direct messages, only when addressed, keeps replies to one packet, rate-limits each sender, and works without the internet. Check your local mesh's rules before enabling more.

## Development

```bash
node --test "configurator/test/*.test.js"
python -m unittest provision/tests/test_mux.py
python tools/scan_private.py        # secrets and personal details; SCAN_EXTRA="regex" adds local patterns
```

## License

[MIT](LICENSE)
