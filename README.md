# Super Mesh Stack

A complete [MeshCore](https://meshcore.co.uk/) node for the Raspberry Pi and Heltec T114 radio, with a configurator that generates your install bundle.

- **Weather on command.** Answers `wx` on a bot channel or by direct message from a local station, with no cloud API.
- **Logging.** Telemetry and coverage logs in local SQLite, with a map of the repeaters that hear you.
- **Field UI.** A 0.96&Prime; screen and five-way joystick for status, messages, adverts and radio control.
- **Web UI.** Messages, weather, contacts, config and firmware flashing from any phone, including on the node's own access point.
- **MeshCore app over the network.** A multiplexer lets the phone app and the bot share one radio.
- **Battery operation.** Voltage monitoring, low-battery shutdown and wake-from-halt.

## Quick start

1. **Configure.** Open [`configurator/index.html`](configurator/index.html) in a browser. It runs entirely locally and uploads nothing. Choose your hardware and options, then download the bundle.
2. **Prepare the SD card.** Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager, then append the bundle's `config.txt.append` to `config.txt` on the boot partition.
3. **Install on the Pi.**

   ```bash
   git clone https://github.com/2theMAXtheStairFax/Super-Mesh-Stack.git
   cd Super-Mesh-Stack/provision
   # copy stack.env and channels.txt from the bundle into this directory
   ./setup.sh && sudo reboot
   ```

4. **Add channels.** After the reboot, run `sudo /opt/mesh-stack/add-channels.sh`.

The bundle's `SETUP.md` repeats these steps with your hostname, wiring and ports filled in. The full guide is [`provision/README.md`](provision/README.md).

## Configuration

| Choice | Effect |
|---|---|
| Radio link (UART or USB) | Serial device, boot overlays and the firmware you need |
| Screen and joystick, Argon HAT, battery monitor | I&sup2;C and SPI overlays, enabled services, packages and the wiring table |
| Access point and Wi-Fi networks | NetworkManager profiles and the rescue access point |
| Channels, bot channel, command word | Channel list and the indexes the bot answers on |
| App access and allowed networks | Multiplexer port and CIDR allow-list for the MeshCore app |
| Coverage log, aerial telemetry, flood scope, weather node | Bot features and defaults |
| Time zone, hostname, SD hardening, mDNS | System settings |
| Map area | `tiles.sh` for offline maps, with a tile-count estimate |

Settings are written to `/etc/mesh-stack/stack.env`, which every service reads. Runtime options can be changed later from the web UI's Config tab.

## Hardware

Developed and tested on a Raspberry Pi 3B. The Pi 4B and Zero 2 W are selectable but untested.

| Part | Notes |
|---|---|
| Radio | Heltec T114 (nRF52840, SX1262). The UART link requires the patched firmware in [`provision/firmware/t114/`](provision/firmware/t114/). USB works with stock companion firmware, with some features unavailable |
| Display | 0.96&Prime; 80&times;160 ST7735S on SPI0, with a SparkFun Qwiic navigation switch |
| Power | INA219 or Waveshare UPS HAT (D); Argon Fan HAT optional |
| GPS | u-blox M10 on the T114 P1 header (patched firmware) |
| Case | Parametric printable case in [`hardware/t114-case/`](hardware/t114-case/) |

See [`docs/hardware-notes.md`](docs/hardware-notes.md) for build notes and [`docs/features.md`](docs/features.md) for the full feature reference.

## Security

- The web UI (port 80) and the app port have no authentication. The web UI can send messages, log in to repeaters and flash firmware. Run the node only on networks you trust and keep the allowed-networks list narrow.
- The access point passphrase is required (8&ndash;63 characters). If you leave it blank, setup generates one.
- `stack.env` contains passphrases. It is gitignored and should never be committed or shared.
- The bundled `.uf2` files are built from the patches in this repository against upstream MeshCore. To avoid trusting a binary, rebuild them with [`build.sh`](provision/firmware/t114/build.sh).

## Mesh etiquette

The bot follows the [Austin Mesh bot guidelines](https://www.austinmesh.org/join/bot-guidelines/). It speaks only on its bot channel and in direct messages, only when addressed, keeps replies to a single packet, rate-limits each sender and works without internet access. Check your local mesh's rules before enabling additional features.

## Development

```bash
node --test "configurator/test/*.test.js"
python -m unittest provision/tests/test_mux.py
python tools/scan_private.py
```

`tools/scan_private.py` checks the tree for secrets and personal details. Set `SCAN_EXTRA` to a regular expression to add local patterns.

## License

[MIT](LICENSE)
