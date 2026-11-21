(function () {
  'use strict';

  const form = document.getElementById('form');
  const $ = (id) => document.getElementById(id);
  const KEY = 'sms-answers';
  const tzList = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone').concat('UTC') : [];

  let current = 'SETUP.md';
  let touched = new Set();

  function el(name) { return form.elements[name]; }

  function read() {
    const cfg = {};
    for (const k of Object.keys(Stack.DEFAULTS)) {
      const e = el(k);
      if (!e) continue;
      cfg[k] = e.type === 'checkbox' ? e.checked : e.type === 'number' ? Number(e.value) : e.value;
    }
    return cfg;
  }

  function write(cfg) {
    for (const [k, v] of Object.entries(cfg)) {
      const e = el(k);
      if (!e) continue;
      if (e.type === 'checkbox') e.checked = !!v; else e.value = v;
    }
    if ('channels' in cfg) syncBotChannels(cfg.botChannel);
  }

  function syncBotChannels(prefer) {
    const sel = el('botChannel');
    const keep = prefer !== undefined ? prefer : sel.value;
    const names = el('channels').value.split(/\r?\n/).map((s) => s.trim().toLowerCase()).filter(Boolean);
    sel.replaceChildren(new Option('None (direct messages only)', ''), ...names.map((n) => new Option(n, n)));
    sel.value = names.includes(keep) ? keep : names.includes('#bot') ? '#bot' : '';
  }

  function showErrors(errors) {
    form.querySelectorAll('.err').forEach((n) => n.remove());
    form.querySelectorAll('[aria-invalid]').forEach((n) => n.removeAttribute('aria-invalid'));
    for (const [k, msg] of Object.entries(errors)) {
      const e = el(k);
      if (!e || !touched.has(k)) continue;
      e.setAttribute('aria-invalid', 'true');
      const s = document.createElement('small');
      s.className = 'err';
      s.textContent = msg;
      e.closest('label').appendChild(s);
    }
  }

  function toggles(cfg) {
    form.querySelectorAll('[data-show]').forEach((n) => { n.hidden = !cfg[n.dataset.show]; });
    form.querySelectorAll('[data-show-field]').forEach((n) => { n.hidden = !cfg[n.dataset.showField]; });
    el('gpioShutdown').disabled = cfg.argon;
    if (cfg.argon) el('gpioShutdown').checked = false;
  }

  function render() {
    syncBotChannels();
    const cfg = read();
    toggles(cfg);
    const v = Stack.validate(cfg, tzList);
    showErrors(v.errors);

    const b = Stack.parseBbox(cfg.bbox);
    $('tiles').textContent = b && !v.errors.bbox ? `About ${Stack.tileCount(b, 0, 15).toLocaleString()} street tiles.` : '';

    const notes = $('notes');
    notes.replaceChildren();
    const bad = Object.values(v.errors);
    if (bad.length) notes.append(note('error', `${bad.length} field${bad.length > 1 ? 's' : ''} need attention before you can download.`));
    v.warnings.forEach((w) => notes.append(note('warn', w)));

    $('download').disabled = !v.ok;
    const files = v.ok ? Stack.generate(cfg) : [];
    renderTabs(files);
    try { const { apPass, mobilePass, ...safe } = cfg; localStorage.setItem(KEY, JSON.stringify(safe)); } catch (_) { /* private mode */ }
    return files;
  }

  function note(kind, text) {
    const d = document.createElement('p');
    d.className = 'note ' + kind;
    d.textContent = text;
    return d;
  }

  function renderTabs(files) {
    const tabs = $('tabs');
    tabs.replaceChildren();
    if (!files.length) { $('out').textContent = 'Fix the highlighted fields to see your files.'; return; }
    if (!files.some((f) => f.path === current)) current = files[0].path;
    for (const f of files) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = f.path.replace('provision/', '');
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', String(f.path === current));
      b.onclick = () => { current = f.path; render(); };
      tabs.append(b);
    }
    const f = files.find((x) => x.path === current);
    $('out').textContent = f.path === 'provision/stack.env' ? f.content.replace(/(PASS|PSK)='[^']+'/g, "$1='••••••••'") : f.content;
  }

  function download(name, bytes, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([bytes], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function randomPass() {
    const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    const limit = 256 - (256 % alphabet.length);
    let out = '';
    while (out.length < 16) {
      for (const b of crypto.getRandomValues(new Uint8Array(32))) {
        if (b < limit && out.length < 16) out += alphabet[b % alphabet.length];
      }
    }
    return out;
  }

  for (const [k, p] of Object.entries(Stack.PIS)) el('pi').append(new Option(p.label + (p.tested ? '' : ' (untested)'), k));
  $('tzlist').replaceChildren(...tzList.map((z) => new Option(z)));

  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (_) { /* ignore */ }
  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  write(Object.assign({}, Stack.DEFAULTS, detected ? { tz: detected } : {}, saved));

  form.addEventListener('input', (e) => { if (e.target.name) touched.add(e.target.name); render(); });
  form.addEventListener('change', render);

  $('gen').onclick = () => { el('apPass').value = randomPass(); touched.add('apPass'); render(); };

  $('download').onclick = () => {
    const files = render();
    if (!files.length) return;
    download('super-mesh-stack-bundle.zip', Zip.build(files), 'application/zip');
  };

  $('import').onclick = () => $('importFile').click();
  $('importFile').onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      write(Object.fromEntries(Object.entries(data).filter(([k]) => k in Stack.DEFAULTS && !Stack.SECRET_FIELDS.includes(k))));
      Object.keys(Stack.DEFAULTS).forEach((k) => touched.add(k));
      render();
    } catch (_) {
      $('notes').prepend(note('error', 'That file is not a stack.json from this configurator.'));
    }
    e.target.value = '';
  };

  render();
})();
