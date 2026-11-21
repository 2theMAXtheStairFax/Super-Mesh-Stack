const test = require('node:test');
const assert = require('node:assert/strict');
const Stack = require('../stack.js');
const Zip = require('../zip.js');

const base = (o = {}) => Object.assign({}, Stack.DEFAULTS, o);
const env = (files) => Object.fromEntries(
  files.find((f) => f.path === 'provision/stack.env').content
    .split('\n').filter((l) => /^[A-Z_0-9]+='/.test(l))
    .map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 2, -1)]));

test('defaults validate', () => {
  assert.equal(Stack.validate(base()).ok, true);
});

test('rejects characters that would break the env file', () => {
  for (const apSsid of ["it's", 'a\b', 'a\nb']) assert.ok(Stack.validate(base({ apSsid })).errors.apSsid);
  assert.ok(Stack.validate(base({ apPass: "pass'word1" })).errors.apPass);
  assert.ok(Stack.validate(base({ apPass: 'short' })).errors.apPass);
  assert.equal(Stack.validate(base({ apPass: 'x'.repeat(63) })).ok, true);
  assert.ok(Stack.validate(base({ apSsid: 'é'.repeat(17) })).errors.apSsid);
});

test('channel rules', () => {
  assert.ok(Stack.validate(base({ channels: 'bot' })).errors.channels);
  assert.ok(Stack.validate(base({ channels: '#a\n#a' })).errors.channels);
  assert.ok(Stack.validate(base({ channels: Array.from({ length: 8 }, (_, i) => '#c' + i).join('\n') })).errors.channels);
  assert.ok(Stack.validate(base({ botChannel: '#nope' })).errors.botChannel);
  assert.equal(Stack.validate(base({ botChannel: '' })).ok, true);
});

test('hardware conflicts', () => {
  assert.ok(Stack.validate(base({ argon: true, gpioShutdown: true })).errors.gpioShutdown);
  assert.ok(Stack.validate(base({ power: true, inaAddr: '0x50' })).errors.inaAddr);
  assert.ok(Stack.validate(base({ power: true, cells: 5 })).errors.cells);
});

test('network and misc fields', () => {
  assert.ok(Stack.validate(base({ appAllow: '10.0.0.0/33' })).errors.appAllow);
  assert.ok(Stack.validate(base({ appAllow: '300.0.0.0/8' })).errors.appAllow);
  assert.ok(Stack.validate(base({ appPort: 5001 })).errors.appPort);
  assert.ok(Stack.validate(base({ tz: 'Mars/Olympus' }), ['UTC', 'Europe/Berlin']).errors.tz);
  assert.ok(Stack.validate(base({ regionScope: 'Bad Scope' })).errors.regionScope);
  assert.ok(Stack.validate(base({ bbox: '10,10,5,20' })).errors.bbox);
  assert.ok(Stack.validate(base({ bbox: '-98,29,-96,33' })).ok);
});

test('env file reflects the answers', () => {
  const e = env(Stack.generate(base({
    tz: 'Europe/Berlin', link: 'usb', channels: '#Bot\n#testing', botChannel: '#testing',
    power: true, cells: 2, appAllow: '192.168.1.0/24, 10.0.0.0/8', apPass: 'hunter22hunter',
  })));
  assert.equal(e.SMS_TZ, 'Europe/Berlin');
  assert.equal(e.SMS_SERIAL, '/dev/ttyACM0');
  assert.equal(e.SMS_BOT_CHANNEL_IDX, '2');
  assert.equal(e.SMS_MUX_ALLOW, '192.168.1.0/24,10.0.0.0/8');
  assert.equal(e.SMS_BATT_CRIT_V, '6.80');
  assert.equal(e.STACK_UART, '0');
  assert.equal(e.SMS_AP_PASS, 'hunter22hunter');
});

test('header title', () => {
  assert.ok(Stack.validate(base({ title: '' })).errors.title);
  assert.ok(Stack.validate(base({ title: 'x'.repeat(25) })).errors.title);
  assert.ok(Stack.validate(base({ title: "it's" })).errors.title);
  assert.equal(env(Stack.generate(base({ title: '  Field Node 7 ' }))).SMS_TITLE, 'Field Node 7');
});

test('app access admits the local network by default, and can be restricted', () => {
  assert.equal(env(Stack.generate(base())).SMS_MUX_LAN, '1');
  assert.equal(env(Stack.generate(base({ appLan: false }))).SMS_MUX_LAN, '0');
});

test('closing app access sets port 0; no bot channel means DM-only', () => {
  const e = env(Stack.generate(base({ appAccess: false, botChannel: '' })));
  assert.equal(e.SMS_MUX_PORT, '0');
  assert.equal(e.SMS_BOT_CHANNEL_IDX, '-1');
});

test('boot config follows the hardware', () => {
  const cfg = (o) => Stack.generate(base(o)).find((f) => f.path === 'config.txt.append').content;
  assert.match(cfg({}), /disable-bt/);
  assert.doesNotMatch(cfg({ link: 'usb' }), /disable-bt|enable_uart/);
  assert.doesNotMatch(cfg({ lcd: false }), /spi0-1cs|i2c_arm/);
  assert.match(cfg({ lcd: false, power: true }), /i2c_arm=on/);
});

test('secrets stay out of everything but stack.env', () => {
  const files = Stack.generate(base({ apPass: 'sekrit-pass-1', mobileSsid: 'Phone', mobilePass: 'sekrit-pass-2' }));
  for (const f of files) {
    const has = /sekrit-pass/.test(f.content);
    assert.equal(has, f.path === 'provision/stack.env', f.path);
  }
});

test('bbox adds tiles.sh and tile count grows with area', () => {
  assert.ok(Stack.generate(base({ bbox: '-98,29,-96,33' })).some((f) => f.path === 'tiles.sh'));
  assert.ok(!Stack.generate(base()).some((f) => f.path === 'tiles.sh'));
  const small = Stack.tileCount([-98, 29, -97, 30], 0, 15);
  assert.ok(Stack.tileCount([-98, 29, -96, 31], 0, 15) > small * 3);
});

test('zip round-trips with correct CRCs, names and modes', () => {
  const files = [{ path: 'a/b.txt', content: 'héllo' }, { path: 'x.sh', content: '#!/bin/sh\n', mode: 0o755 }];
  const z = Zip.build(files, new Date(2026, 0, 2, 3, 4, 6));
  const dv = new DataView(z.buffer, z.byteOffset, z.byteLength);
  const eocd = z.length - 22;
  assert.equal(dv.getUint32(eocd, true), 0x06054b50);
  assert.equal(dv.getUint16(eocd + 10, true), 2);
  let p = dv.getUint32(eocd + 16, true);
  for (const f of files) {
    assert.equal(dv.getUint32(p, true), 0x02014b50);
    const size = dv.getUint32(p + 20, true), nlen = dv.getUint16(p + 28, true);
    const mode = dv.getUint32(p + 38, true) >>> 16;
    const lho = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(z.subarray(p + 46, p + 46 + nlen));
    const data = z.subarray(lho + 30 + nlen, lho + 30 + nlen + size);
    assert.equal(name, f.path);
    assert.equal(new TextDecoder().decode(data), f.content);
    assert.equal(Zip.crc32(data), dv.getUint32(p + 16, true));
    assert.equal(mode & 0o777, f.mode || 0o644);
    p += 46 + nlen;
  }
});
