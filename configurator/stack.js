/* Super Mesh Stack configurator core: validate answers, render the install bundle. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Stack = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const PIS = {
    pi3: { label: 'Raspberry Pi 3B / 3B+', tested: true },
    pi4: { label: 'Raspberry Pi 4B', tested: false },
    zero2w: { label: 'Raspberry Pi Zero 2 W', tested: false },
  };

  const DEFAULTS = Object.freeze({
    pi: 'pi3',
    hostname: 'mesh-stack',
    tz: 'UTC',
    link: 'uart',
    lcd: true,
    argon: false,
    gpioShutdown: false,
    wakeWire: false,
    power: false,
    inaAddr: '0x43',
    cells: 1,
    apSsid: 'mesh-stack',
    apPass: '',
    homeCon: 'preconfigured',
    mobileSsid: '',
    mobilePass: '',
    appAccess: true,
    appPort: 5000,
    appAllow: '',
    channels: '#bot\n#testing',
    botChannel: '#bot',
    cmdPrefix: 'wx',
    wxNode: '',
    idlePoweroff: 0,
    wardrive: true,
    fox: false,
    regionScope: '',
    bbox: '',
    harden: true,
    avahi: true,
    platformio: false,
  });

  const SECRET_FIELDS = ['apPass', 'mobilePass'];
  const MAX_SLOTS = 7;
  const MESHCORE_NAME = /^#[a-z0-9][a-z0-9_-]{0,29}$/;

  const lines = (s) => String(s || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const utf8Len = (s) => new TextEncoder().encode(s).length;
  const bad = (s) => /['\\\x00-\x1f\x7f]/.test(s);
  const q = (v) => `'${v}'`;

  function parseBbox(s) {
    const p = String(s || '').split(',').map((x) => Number(x.trim()));
    if (p.length !== 4 || p.some((x) => !Number.isFinite(x))) return null;
    return p;
  }

  function tileCount(bbox, z0, z1) {
    const [lon0, lat0, lon1, lat1] = bbox;
    const n = (z) => 1 << z;
    const tx = (lon, z) => Math.min(n(z) - 1, Math.max(0, Math.floor(((lon + 180) / 360) * n(z))));
    const ty = (lat, z) => {
      const r = (lat * Math.PI) / 180;
      return Math.min(n(z) - 1, Math.max(0, Math.floor(((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * n(z))));
    };
    let total = 0;
    for (let z = z0; z <= z1; z++) {
      total += (tx(lon1, z) - tx(lon0, z) + 1) * (ty(lat0, z) - ty(lat1, z) + 1);
    }
    return total;
  }

  function validate(cfg, tzList) {
    const errors = {};
    const warnings = [];
    const err = (k, m) => { errors[k] = errors[k] || m; };

    if (!PIS[cfg.pi]) err('pi', 'Pick a board.');
    else if (!PIS[cfg.pi].tested) warnings.push(`${PIS[cfg.pi].label} is untested; the 3B is what this stack was built on.`);

    if (!/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/i.test(cfg.hostname)) err('hostname', 'Letters, digits and hyphens; no leading or trailing hyphen.');

    const tzOk = tzList && tzList.length ? tzList.includes(cfg.tz) : /^(UTC|[A-Za-z_]+(\/[A-Za-z0-9_+-]+)+)$/.test(cfg.tz);
    if (!tzOk) err('tz', 'Use an IANA name such as America/Chicago or Europe/Berlin.');

    if (cfg.link === 'usb') {
      warnings.push('USB link: stock companion firmware works, but the joystick radio-screen control, GPS fix, GPS skew, Pi battery feed and brightness need the patched build.');
    } else if (cfg.link !== 'uart') err('link', 'Pick a radio link.');

    if (cfg.argon && cfg.gpioShutdown) err('gpioShutdown', 'gpio-shutdown collides with the Argon power-button handler on GPIO3/4.');
    if (cfg.argon && cfg.wakeWire) warnings.push('Wake pulses on GPIO3 share the I²C clock with the Argon MCU; a pulse while the Pi runs glitches one frame.');

    if (cfg.power) {
      if (!/^0x4[0-9a-f]$/i.test(cfg.inaAddr)) err('inaAddr', 'INA219 address is 0x40 to 0x4f.');
      if (!(cfg.cells >= 1 && cfg.cells <= 4 && Number.isInteger(+cfg.cells))) err('cells', '1 to 4 cells in series.');
    }

    if (!cfg.apSsid || utf8Len(cfg.apSsid) > 32 || bad(cfg.apSsid)) err('apSsid', '1 to 32 bytes; no quotes, backslashes or control characters.');
    if (cfg.apPass) {
      if (cfg.apPass.length < 8 || cfg.apPass.length > 63) err('apPass', 'WPA2 passphrase is 8 to 63 characters.');
      else if (bad(cfg.apPass) || /[^\x20-\x7e]/.test(cfg.apPass)) err('apPass', 'Printable ASCII only; no quotes or backslashes.');
    }
    if (!/^[A-Za-z0-9_. -]{1,40}$/.test(cfg.homeCon)) err('homeCon', 'NetworkManager connection name: letters, digits, space, . _ -');
    if (cfg.mobileSsid) {
      if (utf8Len(cfg.mobileSsid) > 32 || bad(cfg.mobileSsid)) err('mobileSsid', '1 to 32 bytes; no quotes or backslashes.');
      if (cfg.mobilePass.length < 8 || cfg.mobilePass.length > 63 || bad(cfg.mobilePass)) err('mobilePass', '8 to 63 printable characters; no quotes or backslashes.');
      warnings.push('Pi 3B/Zero 2 W cannot join WPA2/WPA3-mixed networks; set 2.4 GHz to WPA2-PSK only.');
    }

    if (cfg.appAccess) {
      const port = +cfg.appPort;
      if (!Number.isInteger(port) || port < 1024 || port > 65535 || port === 5001) err('appPort', '1024 to 65535, not 5001 (internal).');
      for (const c of lines((cfg.appAllow || '').replace(/,/g, '\n'))) {
        const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/.exec(c);
        if (!m || m.slice(1, 5).some((o) => +o > 255) || +m[5] > 32) { err('appAllow', `Not an IPv4 CIDR: ${c}`); break; }
        if (+m[5] < 16) warnings.push(`${c} exposes the radio to a very large network; the app port has no authentication.`);
      }
    }

    const chans = lines(cfg.channels).map((c) => c.toLowerCase());
    const badChan = chans.find((c) => !MESHCORE_NAME.test(c));
    if (badChan) err('channels', `Invalid channel name: ${badChan}. Use #name with a-z, 0-9, - or _.`);
    if (new Set(chans).size !== chans.length) err('channels', 'Duplicate channel.');
    if (chans.length > MAX_SLOTS) err('channels', `The radio has ${MAX_SLOTS} free slots after Public.`);
    if (cfg.botChannel && !chans.includes(cfg.botChannel.toLowerCase())) err('botChannel', 'Must be one of the channels above, or blank for DM-only.');

    if (!/^[a-z0-9$_-]{1,12}$/i.test(cfg.cmdPrefix)) err('cmdPrefix', '1 to 12 characters: letters, digits, $ _ -');
    if (bad(cfg.wxNode)) err('wxNode', 'No quotes, backslashes or control characters.');
    if (!(Number.isInteger(+cfg.idlePoweroff) && cfg.idlePoweroff >= 0 && cfg.idlePoweroff <= 86400)) err('idlePoweroff', 'Seconds, 0 to 86400.');
    if (+cfg.idlePoweroff > 0 && +cfg.idlePoweroff < 120) warnings.push('Powering off after under two minutes of silence makes the node hard to reach.');

    const scope = String(cfg.regionScope || '').trim().toLowerCase().replace(/^#/, '');
    if (scope && (scope.length > 29 || !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(scope))) err('regionScope', 'Lowercase words joined by hyphens, up to 29 characters.');
    if (scope) warnings.push('A flood scope only reaches repeaters that carry the same scope. Leave it blank unless your local mesh uses scopes.');

    if (cfg.bbox) {
      const b = parseBbox(cfg.bbox);
      if (!b) err('bbox', 'Four numbers: west,south,east,north.');
      else if (b[0] >= b[2] || b[1] >= b[3] || b[0] < -180 || b[2] > 180 || b[1] < -85 || b[3] > 85) err('bbox', 'West < east and south < north, within ±180 / ±85.');
      else {
        const dark = tileCount(b, 0, 15);
        if (dark > 400000) warnings.push(`That map area is about ${Math.round(dark / 1000)}k street tiles; the download and the SD card space will be large.`);
      }
    }

    return { errors, warnings, ok: Object.keys(errors).length === 0 };
  }

  function normalize(cfg) {
    const c = Object.assign({}, DEFAULTS, cfg);
    c.channels = lines(c.channels).map((x) => x.toLowerCase()).join('\n');
    c.botChannel = String(c.botChannel || '').trim().toLowerCase();
    c.regionScope = String(c.regionScope || '').trim().toLowerCase().replace(/^#/, '');
    c.cmdPrefix = String(c.cmdPrefix).trim().toLowerCase();
    return c;
  }

  function envFile(c) {
    const chans = lines(c.channels);
    const botIdx = c.botChannel ? chans.indexOf(c.botChannel) + 1 : -1;
    const allow = lines(c.appAllow.replace(/,/g, '\n')).join(',');
    const pw = Math.round;
    const v = (perCell) => (perCell * c.cells).toFixed(2);
    const kv = [
      ['SMS_TZ', c.tz],
      ['SMS_SERIAL', c.link === 'uart' ? '/dev/serial0' : '/dev/ttyACM0'],
      ['SMS_BOT_CHANNEL_IDX', botIdx],
      ['SMS_WX_CHANNEL_IDX', botIdx],
      ['SMS_CMD_PREFIX', c.cmdPrefix],
      ['SMS_WX_NODE', c.wxNode],
      ['IDLE_POWEROFF_SEC', pw(c.idlePoweroff)],
      ['SMS_MUX_PORT', c.appAccess ? c.appPort : 0],
      ['SMS_MUX_ALLOW', allow],
      ['SMS_AP_SSID', c.apSsid],
      ['SMS_AP_PASS', c.apPass],
      ['SMS_HOME_CON', c.homeCon],
      ['SMS_REGION_SCOPE_ON', c.regionScope ? 1 : 0],
      ['SMS_REGION_SCOPE', c.regionScope],
      ['SMS_WARDRIVE_LOG', c.wardrive ? 1 : 0],
    ];
    if (c.power) {
      kv.push(['SMS_INA219_ADDR', c.inaAddr], ['SMS_BATT_CELLS', c.cells],
        ['SMS_BATT_WARN_V', v(3.6)], ['SMS_BATT_CRIT_V', v(3.4)], ['SMS_BATT_FLOOR_V', v(3.3)]);
    }
    const flags = [
      ['STACK_UART', c.link === 'uart' ? 1 : 0], ['STACK_LCD', +c.lcd], ['STACK_POWER', +c.power],
      ['STACK_FOX', +c.fox], ['STACK_ARGON', +c.argon], ['STACK_HARDEN', +c.harden],
      ['STACK_AVAHI', +c.avahi], ['STACK_PLATFORMIO', +c.platformio],
      ['STACK_MOBILE_SSID', c.mobileSsid], ['STACK_MOBILE_PSK', c.mobilePass],
    ];
    const out = (rows) => rows.map(([k, val]) => `${k}=${q(val)}`).join('\n');
    return `# Super Mesh Stack - generated by the configurator. Contains secrets: do not commit.\n${out(kv)}\n\n# installer switches (read by setup.sh only)\n${out(flags)}\n`;
  }

  function bootConfig(c) {
    const out = ['# Super Mesh Stack - append to /boot/firmware/config.txt before first boot.', ''];
    if (c.lcd || c.power || c.argon) {
      out.push('# I2C: ' + [c.lcd && 'joystick 0x20', c.argon && 'Argon MCU 0x1a', c.power && `INA219 ${c.inaAddr}`].filter(Boolean).join(', '),
        'dtparam=i2c_arm=on', 'dtparam=i2c_arm_baudrate=400000', '');
    }
    if (c.lcd) out.push('# SPI0, one chip-select, so GPIO7 stays free', 'dtoverlay=spi0-1cs', '');
    out.push('# headless: no audio, camera or display stack', 'dtparam=audio=off', 'camera_auto_detect=0', 'display_auto_detect=0', 'disable_splash=1', 'gpu_mem=16', '');
    if (c.link === 'uart') {
      out.push('# radio on the PL011 UART (GPIO14/15); Bluetooth gives way', 'dtoverlay=disable-bt', 'enable_uart=1', '');
    }
    if (c.gpioShutdown) out.push('# clean shutdown from GPIO3 held low', 'dtoverlay=gpio-shutdown,gpio_pin=3,active_low=1,gpio_pull=up', '');
    return out.join('\n');
  }

  function wiring(c) {
    const rows = [];
    if (c.link === 'uart') {
      rows.push(['Pi TX (GPIO14)', 'pin 8', 'T114 P1-8 (RX)'], ['Pi RX (GPIO15)', 'pin 10', 'T114 P1-6 (TX)'], ['GND', 'pin 14', 'T114 P1-4']);
    } else rows.push(['USB', 'any Pi USB port', 'T114 USB (data cable)']);
    if (c.lcd) {
      rows.push(['TFT SCL / SPI0 SCLK', 'pin 23', 'display SCL'], ['TFT SDA / SPI0 MOSI', 'pin 19', 'display SDA'], ['TFT CS (CE0)', 'pin 24', 'display CS'],
        ['TFT DC (GPIO25)', 'pin 22', 'display DC'], ['TFT RES (GPIO24)', 'pin 18', 'display RES'], ['TFT BLK (GPIO18)', 'pin 12', 'display BLK'],
        ['I²C SDA / SCL', 'pins 3 / 5', 'Qwiic nav switch (0x20)']);
    }
    if (c.power) rows.push(['I²C SDA / SCL', 'pins 3 / 5', `INA219 / UPS HAT (${c.inaAddr})`]);
    if (c.wakeWire) rows.push(['Wake (GPIO3)', 'pin 5', 'T114 GPIO, open-drain, pulse low ~200 ms']);
    return rows;
  }

  const table = (head, rows) => `| ${head.join(' | ')} |\n|${head.map(() => '---').join('|')}|\n${rows.map((r) => `| ${r.join(' | ')} |`).join('\n')}\n`;

  function setupGuide(c) {
    const chans = lines(c.channels);
    const g = [];
    g.push(`# Your Super Mesh Stack setup\n\nBoard: ${PIS[c.pi].label}. Radio link: ${c.link === 'uart' ? 'UART (patched firmware)' : 'USB'}.\n`);
    g.push(`## 1. Flash the SD card\n\nRaspberry Pi Imager, Raspberry Pi OS Lite 64-bit (Bookworm). Under Edit Settings:\n\n- Hostname: \`${c.hostname}\`\n- Username and password: your choice\n- Wireless LAN: your network (2.4 GHz) and the country\n- Locale: \`${c.tz}\`\n- SSH: public-key only, paste your key\n`);
    g.push('## 2. Boot partition\n\nAppend `config.txt.append` from this bundle to `config.txt` on the `bootfs` partition, eject, boot the Pi.\n');
    g.push('## 3. Install\n\nOn the Pi:\n\n```bash\ngit clone https://github.com/2theMAXtheStairFax/Super-Mesh-Stack.git ~/super-mesh-stack\n```\n\nCopy `provision/stack.env` and `provision/channels.txt` from this bundle into `~/super-mesh-stack/provision/`, then:\n\n```bash\ncd ~/super-mesh-stack/provision && ./setup.sh && sudo reboot\n```\n\n`stack.env` holds your passphrases. It is gitignored; keep it out of repos and screenshots.\n');
    g.push(`## 4. Wiring\n\n${table(['Signal', 'Pi', 'Device'], wiring(c))}`);
    if (c.link === 'uart') g.push('## 5. Radio firmware\n\nFlash the patched T114 build from `provision/firmware/t114/` (see its README), or rebuild with `build.sh`. The UART link only exists in that build.\n');
    g.push(`## ${c.link === 'uart' ? 6 : 5}. Channels\n\nAfter the reboot:\n\n\`\`\`bash\nsudo /opt/mesh-stack/add-channels.sh\n\`\`\`\n\nAdds ${chans.length ? chans.join(', ') : 'nothing (none chosen)'} into the first free slots. ${c.botChannel ? `The bot answers \`${c.cmdPrefix} wx\` on ${c.botChannel}.` : 'The bot answers direct messages only.'} Add the same names in the phone app to see the same traffic.\n`);
    const reach = [`- Web UI: \`http://${c.hostname}.local/\`, or \`http://10.42.0.1/\` when joined to the AP \`${c.apSsid}\``];
    if (c.appAccess) reach.push(`- MeshCore app: Network/TCP, this node's address, port ${c.appPort}. Allowed: loopback, Tailscale (100.64.0.0/10)${c.appAllow ? ', ' + lines(c.appAllow.replace(/,/g, '\n')).join(', ') : ''}`);
    g.push(`## Reaching it\n\n${reach.join('\n')}\n`);
    if (c.bbox) g.push('## Offline maps\n\nRun `bash tiles.sh` on a machine with a good connection after setting `PM_BUILD`, then copy the `.mbtiles` files to `/opt/mesh-stack/maps/`.\n');
    return g.join('\n');
  }

  function tilesScript(c) {
    const b = parseBbox(c.bbox);
    return `#!/usr/bin/env bash
# Offline map tiles for bbox ${b.join(',')}. USGS topo covers the United States only.
# Set PM_BUILD to a current build from https://maps.protomaps.com/builds/
set -euo pipefail
PM_BUILD="\${PM_BUILD:?set PM_BUILD=https://build.protomaps.com/YYYYMMDD.pmtiles}"
BBOX=${b.join(',')}
OUT=\${OUT:-./maps}
mkdir -p "$OUT"
python3 provision/maps/fetch_tiles.py dark "$OUT/dark.mbtiles" "$PM_BUILD" "$BBOX"
python3 provision/maps/fetch_tiles.py topo "$OUT/topo.mbtiles" "$BBOX"
`;
  }

  function generate(input) {
    const c = normalize(input);
    const files = [
      { path: 'SETUP.md', content: setupGuide(c) },
      { path: 'config.txt.append', content: bootConfig(c) },
      { path: 'provision/stack.env', content: envFile(c), mode: 0o600 },
      { path: 'provision/channels.txt', content: lines(c.channels).join('\n') + '\n' },
      { path: 'stack.json', content: JSON.stringify(exportAnswers(c), null, 2) + '\n' },
    ];
    if (c.bbox) files.push({ path: 'tiles.sh', content: tilesScript(c), mode: 0o755 });
    return files;
  }

  function exportAnswers(c) {
    const o = Object.assign({}, c);
    for (const k of SECRET_FIELDS) delete o[k];
    return o;
  }

  return { DEFAULTS, PIS, SECRET_FIELDS, MAX_SLOTS, validate, generate, normalize, tileCount, parseBbox, wiring };
});
