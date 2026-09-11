/* CineMediaVault setup wizard.
 *
 * Deliberately dependency-free: the machine being set up may have no internet
 * access, and an installer that needs a CDN is an installer that fails when you
 * most need it.
 *
 * The browser holds no authority. Every value is re-validated on the server by
 * the same schema the unattended install uses, so this file is about making the
 * questions clear - not about deciding what is acceptable.
 */
'use strict';

const S = {
  csrf: '',
  config: {},
  progress: null,
  stage: 'welcome',
  suggest: null,
  timezones: [],
  lineup: [],
  pollTimer: null,
  since: 0,
};

/* ---------------------------------------------------------------- helpers */

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function get(path, fallback) {
  let node = S.config;
  for (const part of path.split('.')) {
    if (node === null || typeof node !== 'object' || !(part in node)) return fallback;
    node = node[part];
  }
  return node === undefined || node === null ? fallback : node;
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (S.csrf) headers['X-CSRF-Token'] = S.csrf;
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers,
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
    method: options.body ? (options.method || 'POST') : (options.method || 'GET'),
  });
  let data = {};
  try { data = await response.json(); } catch { /* non-JSON error page */ }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.fields = data.fields || {};
    error.status = response.status;
    throw error;
  }
  return data;
}

function setStageContent(...nodes) {
  const stage = document.getElementById('stage');
  stage.replaceChildren(...nodes.flat().filter(Boolean));
  stage.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function notice(kind, title, body) {
  return el('div', { class: `notice ${kind}` },
    title ? el('strong', {}, title) : null, body);
}

/* ----------------------------------------------------------- form controls */

const collected = new Map();   // key -> () => value

function textField(key, label, opts = {}) {
  const value = opts.value !== undefined ? opts.value : get(key, opts.fallback ?? '');
  const input = el('input', {
    type: opts.password ? 'password' : (opts.number ? 'number' : 'text'),
    id: `f-${key}`, value: value === null ? '' : value,
    placeholder: opts.placeholder || '',
    min: opts.min, max: opts.max, step: opts.step,
    autocomplete: opts.autocomplete || 'off',
    spellcheck: 'false',
  });
  const msg = el('div', { class: 'msg', id: `m-${key}` });
  collected.set(key, () => opts.number ? input.value : input.value.trim());
  if (opts.onInput) input.addEventListener('input', () => opts.onInput(input, msg));
  if (opts.onBlur) input.addEventListener('blur', () => opts.onBlur(input, msg));
  return el('div', { class: 'field' },
    el('label', { for: `f-${key}` }, label),
    opts.hint ? el('p', { class: 'hint' }, opts.hint) : null,
    input, msg);
}

function selectField(key, label, choices, opts = {}) {
  const current = String(opts.value !== undefined ? opts.value : get(key, choices[0][0]));
  const select = el('select', { id: `f-${key}` },
    ...choices.map(([v, text]) =>
      el('option', { value: v, selected: String(v) === current }, text)));
  collected.set(key, () => select.value);
  if (opts.onChange) select.addEventListener('change', () => opts.onChange(select.value));
  return el('div', { class: 'field' },
    el('label', { for: `f-${key}` }, label),
    opts.hint ? el('p', { class: 'hint' }, opts.hint) : null,
    select, el('div', { class: 'msg', id: `m-${key}` }));
}

function checkField(key, label, opts = {}) {
  const checked = opts.value !== undefined ? opts.value : get(key, opts.fallback ?? false);
  const input = el('input', { type: 'checkbox', id: `f-${key}`, checked: !!checked });
  collected.set(key, () => input.checked);
  if (opts.onChange) input.addEventListener('change', () => opts.onChange(input.checked));
  return el('div', { class: 'check' }, input,
    el('div', {},
      el('label', { for: `f-${key}` }, label),
      opts.hint ? el('p', { class: 'hint' }, opts.hint) : null));
}

function pathField(key, label, opts = {}) {
  const field = textField(key, label, {
    ...opts,
    placeholder: opts.placeholder || '/srv/cinemediavault/Movies',
    onBlur: async (input, msg) => {
      const path = input.value.trim();
      if (!path) { msg.textContent = ''; msg.className = 'msg'; input.removeAttribute('aria-invalid'); return; }
      msg.textContent = 'Checking…'; msg.className = 'msg';
      try {
        const result = await api('/api/validate/path', {
          body: { path, purpose: opts.purpose || label, needs_write: !!opts.needsWrite },
        });
        msg.textContent = result.message || '';
        msg.className = 'msg ' + (result.ok ? (result.warning ? 'warn' : 'ok') : 'err');
        input.setAttribute('aria-invalid', result.ok ? 'false' : 'true');
      } catch (error) {
        msg.textContent = error.message;
        msg.className = 'msg err';
      }
    },
  });
  return field;
}

function portField(key, label, opts = {}) {
  return textField(key, label, {
    ...opts, number: true, min: 1024, max: 65535,
    onBlur: async (input, msg) => {
      const port = parseInt(input.value, 10);
      if (!port) { msg.textContent = ''; return; }
      try {
        const result = await api('/api/validate/port', { body: { port } });
        msg.textContent = result.message;
        msg.className = 'msg ' + (result.ok ? 'ok' : 'err');
        input.setAttribute('aria-invalid', result.ok ? 'false' : 'true');
      } catch (error) { msg.textContent = error.message; msg.className = 'msg err'; }
    },
  });
}

function chipGroup(key, label, options, opts = {}) {
  const selected = new Set(get(key, opts.fallback || []));
  const chips = options.map(([value, text]) => {
    const input = el('input', { type: 'checkbox', checked: selected.has(value) });
    const chip = el('label', { class: 'chip' + (selected.has(value) ? ' on' : '') },
      input, text);
    input.addEventListener('change', () => {
      chip.classList.toggle('on', input.checked);
      if (input.checked) selected.add(value); else selected.delete(value);
      if (opts.onChange) opts.onChange([...selected]);
    });
    return chip;
  });
  collected.set(key, () => [...selected]);
  return el('div', { class: 'field' },
    el('label', {}, label),
    opts.hint ? el('p', { class: 'hint' }, opts.hint) : null,
    el('div', { class: 'chips' }, ...chips));
}

function actions(nextLabel, onNext, { back = true, extra = null } = {}) {
  const next = el('button', { class: 'primary', onClick: onNext }, nextLabel);
  return el('div', { class: 'actions' },
    back ? el('button', { class: 'secondary', onClick: goBack }, 'Back') : null,
    next, extra);
}

async function submitStage(stage, extra = {}) {
  const fields = { ...extra };
  for (const [key, read] of collected.entries()) fields[key] = read();
  clearFieldErrors();
  try {
    const result = await api('/api/stage', { body: { stage, fields } });
    S.progress = result.progress;
    await refreshState();
    if (result.warnings && result.warnings.length) {
      // Warnings never block; they are shown on the next screen so the operator
      // sees them in the context of what they just chose.
      S.pendingWarnings = result.warnings;
    }
    render();
  } catch (error) {
    showFieldErrors(error);
  }
}

function clearFieldErrors() {
  document.querySelectorAll('.msg.err').forEach((n) => {
    if (n.dataset.server) { n.textContent = ''; n.className = 'msg'; }
  });
}

function showFieldErrors(error) {
  const fields = error.fields || {};
  let first = null;
  for (const [key, message] of Object.entries(fields)) {
    const msg = document.getElementById(`m-${key}`);
    const input = document.getElementById(`f-${key}`);
    if (msg) { msg.textContent = message; msg.className = 'msg err'; msg.dataset.server = '1'; }
    if (input) { input.setAttribute('aria-invalid', 'true'); if (!first) first = input; }
  }
  if (!Object.keys(fields).length) {
    const stage = document.getElementById('stage');
    stage.prepend(notice('err', 'That did not work', error.message));
  }
  if (first) first.focus();
}

async function goBack() {
  await api('/api/back', { body: { stage: S.stage } });
  await refreshState();
  render();
}

/* -------------------------------------------------------------- the stages */

const STAGE_RENDERERS = {

  welcome() {
    const s = S.suggest || {};
    const facts = s.facts || {};
    return [
      el('h2', {}, 'Welcome to CineMediaVault'),
      el('p', { class: 'lede' },
        'This will set up your personal media server: movies, TV, music, books, ' +
        'comics, games, live TV and recording. It takes about ten minutes, and ' +
        'you can stop and come back at any point.'),
      el('div', { class: 'card' },
        el('h3', {}, 'This machine'),
        el('table', { class: 'summary' },
          el('tbody', {},
            row('Operating system', facts.os || 'unknown'),
            row('Processor', `${facts.cpu || '?'} cores (${facts.arch || '?'})`),
            row('Memory', `${facts.memory_mb || '?'} MB`),
            row('Graphics', (s.gpu && s.gpu.length) ? s.gpu.join(', ')
                            : 'none detected (software transcoding)'),
            row('Docker', s.has_docker ? 'installed' : 'not installed'),
            row('Network address', s.lan_address || 'unknown')))),
      textField('meta.instance_name', 'What should this server be called?', {
        fallback: 'CineMediaVault',
        hint: 'Shown in the browser tab and on the Modules page.',
      }),
      selectField('deployment.mode', 'How much do you want to set up?', [
        ['standard', 'Standard - media libraries plus live TV and recording'],
        ['minimal', 'Minimal - just the web app with movies and TV'],
        ['everything', 'Everything - every optional module this hardware supports'],
      ], { hint: 'You can change any of this later; this only sets the starting points.' }),
      selectField('deployment.timezone', 'Time zone',
        S.timezones.map((t) => [t, t]),
        { value: get('deployment.timezone', s.detected_timezone || 'UTC'),
          hint: 'Used for the TV guide, recording times and scheduled tasks.' }),
      checkField('deployment.accept_licenses',
        'I accept the third-party licences for optional components', {
        hint: 'Some optional parts (the guide collector, emulator runtimes, ' +
              'speech recognition) are separate open-source projects downloaded ' +
              'during installation. Their licences are listed in ' +
              'docs/THIRD-PARTY-NOTICES.md.',
      }),
      actions('Get started', () => submitStage('welcome'), { back: false }),
    ];
  },

  admin() {
    const hasHash = !!get('admin.password_hash', '');
    return [
      el('h2', {}, 'Create your account'),
      el('p', { class: 'lede' },
        'This is the account you will sign in with. It has full control, so ' +
        'choose a password you do not use anywhere else.'),
      notice('ok', null,
        'Your password is turned into a one-way hash immediately. It is never ' +
        'written to a configuration file, never appears in a log, and cannot be ' +
        'recovered from this server - only reset.'),
      textField('admin.username', 'Username', {
        placeholder: 'jsmith', autocomplete: 'username',
        hint: 'Lower-case letters, digits, hyphen and underscore.',
      }),
      textField('admin.full_name', 'Display name', { placeholder: 'Jane Smith' }),
      textField('admin.email', 'Email address (optional)',
        { placeholder: 'jane@example.com' }),
      textField('admin.password', hasHash ? 'New password (leave blank to keep)' : 'Password', {
        password: true, value: '', autocomplete: 'new-password',
        hint: 'At least 12 characters, mixing at least three of: lower case, ' +
              'upper case, digits, symbols.',
        onInput: (input, msg) => {
          const strength = passwordStrength(input.value);
          msg.textContent = input.value ? strength.text : '';
          msg.className = 'msg ' + strength.kind;
        },
      }),
      actions('Continue', () => submitStage('admin')),
    ];
  },

  network() {
    const s = S.suggest || {};
    const tlsMode = get('network.tls_mode', 'self_signed');
    const tlsFields = el('div', { id: 'tls-extra' });
    const renderTls = (mode) => {
      tlsFields.replaceChildren();
      if (mode === 'self_signed') {
        tlsFields.append(notice('warn', 'About the security warning',
          'A certificate generated here is not signed by a public authority, so ' +
          'browsers show a warning the first time. Accept it once per device. ' +
          'The connection is still encrypted.'));
      } else if (mode === 'provided' || mode === 'acme_dns') {
        tlsFields.append(
          textField('network.tls_cert_path', 'Certificate file (PEM)',
            { placeholder: '/etc/letsencrypt/live/example/fullchain.pem' }),
          textField('network.tls_key_path', 'Private key file (PEM)',
            { placeholder: '/etc/letsencrypt/live/example/privkey.pem',
              hint: 'The key is referenced in place and never copied into ' +
                    'backups.' }));
      } else {
        tlsFields.append(notice('err', 'Not recommended',
          'Without HTTPS, passwords travel your network in the clear. Only ' +
          'choose this if something else terminates TLS in front of this server.'));
      }
    };
    renderTls(tlsMode);

    return [
      el('h2', {}, 'Network and HTTPS'),
      el('p', { class: 'lede' },
        'How people reach this server from their browsers, phones and TVs.'),
      textField('network.hostname', 'Address people will type', {
        fallback: s.hostname || 'cinemediavault.local',
        hint: 'A name from your router, or just this machine\'s IP address ' +
              `(${s.lan_address || 'unknown'}). It also goes on the certificate.`,
      }),
      selectField('network.listen_address', 'Listen on', [
        ['0.0.0.0', 'All network interfaces (recommended)'],
        [s.lan_address || '127.0.0.1', `Only ${s.lan_address || '127.0.0.1'}`],
        ['127.0.0.1', 'Only this machine (127.0.0.1)'],
      ]),
      selectField('network.tls_mode', 'HTTPS certificate', [
        ['self_signed', 'Generate one for me (recommended for home networks)'],
        ['provided', 'I already have a certificate and key'],
        ['acme_dns', 'Use my existing Let\'s Encrypt certificate'],
        ['none', 'No HTTPS (not recommended)'],
      ], { onChange: renderTls }),
      tlsFields,
      portField('network.https_port', 'HTTPS port', { fallback: 5000 }),
      checkField('network.enable_http', 'Also serve plain HTTP', {
        hint: 'Useful only for old devices that cannot do HTTPS.',
      }),
      portField('network.http_port', 'HTTP port', { fallback: 8080 }),
      textField('network.trusted_networks', 'Trusted networks', {
        value: (get('network.trusted_networks', [s.trusted_network || '192.168.0.0/16'])).join(', '),
        hint: 'Comma-separated. Only these ranges may reach the setup page and ' +
              'the guide collector. Public ranges are refused.',
      }),
      checkField('network.configure_firewall', 'Add firewall rules for me',
        { fallback: true,
          hint: 'Adds ufw rules for the ports above. The firewall is not switched ' +
                'on automatically - doing that over SSH can lock you out.' }),
      actions('Continue', () => submitStage('network')),
    ];
  },

  media() {
    return [
      el('h2', {}, 'Where is your media?'),
      el('p', { class: 'lede' },
        'Point CineMediaVault at the folders you already have. Nothing is moved, ' +
        'renamed or deleted - these folders are only read. Leave any of them ' +
        'blank to skip that library.'),
      notice('warn', 'One thing to check first',
        'If your media lives on a NAS, make sure it is mounted before you ' +
        'continue. An unmounted folder looks empty, and an empty folder would ' +
        'be recorded as an empty library.'),
      el('div', { id: 'mount-hint' }),
      pathField('media.movies_root', 'Movies', { purpose: 'movies' }),
      pathField('media.tv_root', 'TV shows', { purpose: 'TV shows' }),
      pathField('media.music_root', 'Music', { purpose: 'music' }),
      pathField('media.books_root', 'Books and audiobooks', { purpose: 'books' }),
      pathField('media.comics_root', 'Comics (CBZ/CBR files)', { purpose: 'comics' }),
      pathField('media.comic_library_root', 'Generated comic library', {
        purpose: 'comic library', needsWrite: true,
        hint: 'A separate, writable folder where readable comic pages are built. ' +
              'Must not be inside your comics folder.',
      }),
      pathField('media.games_root', 'Games and ROMs', { purpose: 'games', needsWrite: true }),
      pathField('media.recordings_root', 'TV recordings', {
        purpose: 'recordings', needsWrite: true,
        hint: 'Needs plenty of space: about 7 GB per hour of HD television.',
      }),
      checkField('media.create_missing', 'Create folders that do not exist yet', {
        hint: 'Only creates empty folders. Existing content is never touched.',
      }),
      actions('Continue', () => submitStage('media')),
    ];
  },

  mounts() {
    const rows = el('div', { id: 'mount-rows' });
    const existing = get('media.mounts', []);
    const addRow = (mount = {}) => {
      const card = el('div', { class: 'card' });
      const type = el('select', {},
        el('option', { value: 'cifs', selected: mount.type !== 'nfs' }, 'SMB / Windows share'),
        el('option', { value: 'nfs', selected: mount.type === 'nfs' }, 'NFS'));
      const source = el('input', { type: 'text', value: mount.source || '',
        placeholder: '//nas.local/Movies  or  nas.local:/export/movies' });
      const point = el('input', { type: 'text', value: mount.mountpoint || '',
        placeholder: '/srv/cinemediavault/movies' });
      const options = el('input', { type: 'text', value: mount.options || '',
        placeholder: 'vers=3.0,uid=cinevault' });
      const creds = el('input', { type: 'text', value: mount.credentials_file || '',
        placeholder: '/root/.smbcredentials' });
      const manage = el('input', { type: 'checkbox', checked: mount.manage_fstab !== false });
      card.append(
        field('Share type', type),
        field('Share address', source),
        field('Mount it at', point),
        field('Extra options (optional)', options),
        field('Credentials file (SMB only)', creds,
          'A file containing username= and password= lines, mode 0600. The ' +
          'installer never writes or reads share passwords itself.'),
        el('div', { class: 'check' }, manage,
          el('div', {}, el('label', {}, 'Add to /etc/fstab so it mounts at boot'),
            el('p', { class: 'hint' },
              'Added with nofail and automount, so a missing NAS cannot stop ' +
              'this machine from booting.'))),
        el('button', { class: 'secondary', onClick: () => card.remove() }, 'Remove'));
      card._read = () => ({
        type: type.value, source: source.value.trim(),
        mountpoint: point.value.trim(), options: options.value.trim(),
        credentials_file: creds.value.trim(), manage_fstab: manage.checked,
      });
      rows.append(card);
    };
    existing.forEach(addRow);

    collected.set('media.mounts', () =>
      [...rows.children].map((c) => c._read()).filter((m) => m.source && m.mountpoint));

    return [
      el('h2', {}, 'Network shares'),
      el('p', { class: 'lede' },
        'If your media is on a NAS that is not mounted yet, describe it here and ' +
        'the installer will mount it. If everything is already mounted, or your ' +
        'media is on local disks, skip this step.'),
      el('div', { id: 'existing-mounts' }),
      rows,
      el('div', { class: 'actions' },
        el('button', { class: 'secondary', onClick: () => addRow() }, 'Add a share')),
      actions('Continue', () => submitStage('mounts')),
    ];
  },

  metadata() {
    return [
      el('h2', {}, 'Posters and descriptions'),
      el('p', { class: 'lede' },
        'CineMediaVault looks up artwork, cast and summaries from The Movie ' +
        'Database. A free key takes a minute to get, and you can add it later.'),
      notice('ok', 'How your keys are stored',
        'API keys go into a separate file readable only by root and the ' +
        'CineMediaVault service. They are masked everywhere in this page and ' +
        'stripped from every log line.'),
      textField('metadata.tmdb_api_key', 'TMDb API key', {
        password: true, placeholder: 'leave blank to skip',
        hint: 'Free from themoviedb.org, under Settings then API.',
      }),
      textField('metadata.tmdb_read_access_token', 'TMDb read access token (alternative)',
        { password: true, hint: 'Either the key or the token is enough.' }),
      textField('metadata.google_books_api_key', 'Google Books key (optional)', {
        password: true, hint: 'Improves book covers and descriptions in Book Vault.',
      }),
      selectField('metadata.refresh_cron', 'How often to look for new files', [
        ['*/15 * * * *', 'Every 15 minutes (recommended)'],
        ['*/30 * * * *', 'Every 30 minutes'],
        ['0 * * * *', 'Every hour'],
        ['0 4 * * *', 'Once a day, at 4am'],
      ]),
      checkField('metadata.fetch_on_install', 'Fetch artwork during installation', {
        hint: 'Off by default. A large library can take hours and will hit the ' +
              'provider\'s rate limits; it runs on a schedule afterwards anyway.',
      }),
      actions('Continue', () => submitStage('metadata')),
    ];
  },

  livetv() {
    const results = el('div', { id: 'tuner-results' });
    const doDiscover = async (address) => {
      results.replaceChildren(el('p', {}, el('span', { class: 'spinner' }), 'Looking…'));
      try {
        const data = await api('/api/discover/tuners', { body: { address: address || '' } });
        if (!data.devices || !data.devices.length) {
          results.replaceChildren(notice('warn', 'No tuner found',
            data.message || 'Enter the tuner\'s IP address below instead.'));
          return;
        }
        results.replaceChildren(...data.devices.map((d) => el('div', { class: 'card' },
          el('h3', {}, d.friendly_name || d.model || 'HDHomeRun'),
          el('table', { class: 'summary' }, el('tbody', {},
            row('Device ID', d.device_id || 'unknown'),
            row('Address', d.address),
            row('Tuners', String(d.tuner_count)),
            row('Firmware', d.firmware || 'unknown'))),
          el('button', { class: 'primary', onClick: () => useTuner(d) }, 'Use this tuner'))));
      } catch (error) {
        results.replaceChildren(notice('err', 'Discovery failed', error.message));
      }
    };
    const useTuner = async (device) => {
      document.getElementById('f-livetv.device_address').value = device.address;
      document.getElementById('f-livetv.device_id').value = device.device_id || '';
      document.getElementById('f-livetv.tuner_count').value = device.tuner_count;
      await loadLineup(device.address);
    };
    const channelBox = el('div', { id: 'channel-box' });
    const loadLineup = async (address) => {
      channelBox.replaceChildren(el('p', {}, el('span', { class: 'spinner' }), 'Reading the channel list…'));
      try {
        const data = await api('/api/discover/lineup', { body: { address } });
        S.lineup = data.channels || [];
        if (!S.lineup.length) {
          channelBox.replaceChildren(notice('warn', 'No channels', data.message || ''));
          return;
        }
        const chosen = new Set(get('livetv.channels', []));
        const boxes = S.lineup.map((c) => {
          const input = el('input', { type: 'checkbox',
            checked: chosen.size === 0 || chosen.has(c.number) });
          return el('label', {}, input,
            el('span', { class: 'num' }, c.number), c.name,
            c.hd ? el('span', { class: 'hint' }, ' HD') : null);
        });
        collected.set('livetv.channels', () => {
          const picked = S.lineup.filter((_, i) => boxes[i].querySelector('input').checked);
          return picked.length === S.lineup.length ? [] : picked.map((c) => c.number);
        });
        channelBox.replaceChildren(
          el('p', { class: 'hint' },
            `${S.lineup.length} channels found. Untick anything you do not want ` +
            'in the guide. Leaving them all ticked keeps every channel.'),
          el('div', { class: 'channels' }, ...boxes));
      } catch (error) {
        channelBox.replaceChildren(notice('err', 'Could not read the lineup', error.message));
      }
    };

    return [
      el('h2', {}, 'Live TV'),
      el('p', { class: 'lede' },
        'If you have an HDHomeRun network tuner, CineMediaVault can show live ' +
        'television and a programme guide, and record shows.'),
      checkField('livetv.enabled', 'Set up live TV', {
        onChange: (on) => { document.getElementById('livetv-detail').hidden = !on; },
      }),
      el('div', { id: 'livetv-detail', hidden: !get('livetv.enabled', false) },
        el('div', { class: 'actions' },
          el('button', { class: 'secondary', onClick: () => doDiscover('') },
            'Search my network for a tuner')),
        results,
        textField('livetv.device_address', 'Tuner IP address', {
          placeholder: '192.168.1.50',
          hint: 'Automatic discovery needs internet access or a flat network. ' +
                'Typing the address always works.',
        }),
        textField('livetv.device_id', 'Device ID (filled in automatically)'),
        textField('livetv.tuner_count', 'Number of tuners', { number: true, min: 0, max: 16 }),
        textField('livetv.reserved_tuners', 'Tuners to keep free for live viewing', {
          number: true, min: 0, max: 16, fallback: 0,
          hint: 'Recording will never use these, so live TV always has one ' +
                'available. With two tuners, reserving one means one recording ' +
                'at a time.',
        }),
        el('div', { class: 'actions' },
          el('button', { class: 'secondary', onClick: () =>
            loadLineup(document.getElementById('f-livetv.device_address').value.trim()) },
            'Load the channel list')),
        channelBox),
      actions('Continue', () => submitStage('livetv', { 'livetv.discovery': 'manual' })),
    ];
  },

  epg() {
    const detail = el('div', { id: 'epg-detail',
      hidden: get('epg.mode', 'device_only') !== 'extended' });
    detail.append(
      notice('warn', 'What this does and does not do',
        'Your tuner\'s own guide always wins. The collector only fills in the ' +
        'days beyond it, and only where programme names and times still line up. ' +
        'If the collector is unavailable, stale or wrong, nothing changes - you ' +
        'simply keep the tuner\'s guide.'),
      textField('epg.horizon_days', 'Combined guide horizon (days)', {
        number: true, min: 1, max: 14, fallback: 14,
        hint: 'Fourteen combines about 2 authoritative tuner days with about 12 ' +
              'additional days. It is the verified upper limit.',
      }),
      textField('epg.local_cache_max_age_hours', 'Refresh local tuner guide after (hours)', {
        number: true, min: 1, max: 48, fallback: 12,
        hint: 'Twelve hours keeps the local two-day portion current without excessive requests.',
      }),
      textField('epg.refresh_time', 'Collect once a day at', {
        fallback: '03:20', placeholder: '03:20',
        hint: 'Overnight, away from the nightly database backup.',
      }),
      textField('epg.request_delay_ms', 'Pause between requests (ms)', {
        number: true, min: 500, max: 30000, fallback: 2500,
        hint: 'Being unhurried keeps the collector welcome. A full collection ' +
              'takes roughly an hour at this pace.',
      }),
      textField('epg.collector_bind_address', 'Collector address', {
        fallback: '127.0.0.1',
        hint: 'Must be private. The collector is never published to the internet.',
      }),
      portField('epg.collector_port', 'Collector port', { fallback: 3010 }),
      textField('epg.channel_map', 'Verified channel map (optional)', {
        placeholder: '/root/channels.xml',
        hint: 'An iptv-org channels.xml matching your channels to the upstream ' +
              'source. Without one the collector has nothing to fetch and your ' +
              'guide simply stays as the tuner provides it.',
      }),
      textField('epg.memory_limit_mb', 'Memory limit (MB)', {
        number: true, min: 1024, max: 16384, fallback: 3072,
        hint: 'The collector holds a whole guide in memory while it works.',
      }));

    return [
      el('h2', {}, 'Programme guide'),
      el('p', { class: 'lede' },
        'Tuners usually provide about two days of guide data. You can optionally ' +
        'run a private collector that extends it to about two weeks.'),
      selectField('epg.mode', 'Guide source', [
        ['device_only', 'Just the tuner\'s guide (about 2 days)'],
        ['extended', 'Unified guide: about 2 local + 12 extended days'],
      ], { onChange: (mode) => { detail.hidden = mode !== 'extended'; } }),
      detail,
      actions('Continue', () => submitStage('epg')),
    ];
  },

  dvr() {
    const detail = el('div', { id: 'dvr-detail', hidden: !get('dvr.enabled', false) });
    detail.append(
      el('div', { class: 'row' },
        textField('dvr.padding_start_seconds', 'Start recording early by (seconds)',
          { number: true, min: 0, max: 3600, fallback: 60 }),
        textField('dvr.padding_end_seconds', 'Keep recording after (seconds)',
          { number: true, min: 0, max: 7200, fallback: 120,
            hint: 'Broadcasts overrun; this catches the end of the show.' })),
      selectField('dvr.retention_days', 'Delete recordings automatically', [
        [0, 'Never (recommended)'],
        [30, 'After 30 days'], [60, 'After 60 days'],
        [90, 'After 90 days'], [365, 'After a year'],
      ], { hint: 'Off by default. Nothing you record is ever removed without ' +
                 'you choosing this.' }),
      selectField('dvr.conflict_policy', 'When more shows overlap than you have tuners', [
        ['priority', 'Keep the higher-priority series'],
        ['first_scheduled', 'Keep whichever was scheduled first'],
        ['manual', 'Record nothing and tell me'],
      ]),
      textField('dvr.min_free_gb', 'Stop recording when free space drops below (GB)',
        { number: true, min: 1, max: 10000, fallback: 20,
          hint: 'A full disk during a recording corrupts it and can affect the ' +
                'whole server.' }));

    return [
      el('h2', {}, 'Recording'),
      el('p', { class: 'lede' },
        'Record programmes from the guide, either one at a time or as a series ' +
        'rule. Recordings are copied straight from the broadcast, so recording ' +
        'costs almost no processing power.'),
      checkField('dvr.enabled', 'Set up recording', {
        onChange: (on) => { detail.hidden = !on; },
      }),
      detail,
      actions('Continue', () => submitStage('dvr')),
    ];
  },

  subtitles() {
    const subdl = el('div', { id: 'subdl-detail',
      hidden: !get('subtitles.subdl_enabled', false) });
    subdl.append(textField('subtitles.subdl_api_key', 'SubDL API key',
      { password: true }));

    const whisper = el('div', { id: 'whisper-detail',
      hidden: !get('subtitles.whisper_enabled', false) });
    const can = (S.suggest || {}).can_run_whisper;
    whisper.append(
      can ? null : notice('warn', 'This machine may struggle',
        'Speech recognition wants at least 8 GB of memory, and without a ' +
        'graphics card it runs several times slower than real time. A full ' +
        'library can take days.'),
      selectField('subtitles.whisper_model', 'Accuracy', [
        ['tiny', 'Fastest, least accurate'],
        ['base', 'Fast'],
        ['small', 'Balanced (recommended)'],
        ['medium', 'Accurate, slow'],
        ['large-v3', 'Most accurate, needs a graphics card'],
      ]),
      selectField('subtitles.whisper_device', 'Run on', [
        ['auto', 'Choose automatically'],
        ['cpu', 'Processor'],
        ['cuda', 'NVIDIA graphics card'],
      ]),
      textField('subtitles.whisper_cpu_quota_percent', 'Processor limit (%)', {
        number: true, min: 25, max: 1600, fallback: 200,
        hint: '100 is one core. The worker also runs at lowest priority so it ' +
              'never interrupts playback.',
      }),
      textField('subtitles.whisper_memory_limit_mb', 'Memory limit (MB)',
        { number: true, min: 1024, max: 65536, fallback: 6144 }),
      checkField('subtitles.whisper_start_enabled', 'Start working through the library now', {
        hint: 'Off by default. The first pass over a whole library runs for ' +
              'days; you can start it later from the Modules page.',
      }));

    return [
      el('h2', {}, 'Subtitles'),
      el('p', { class: 'lede' },
        'CineMediaVault always uses subtitle files and embedded subtitle tracks ' +
        'you already have. It can optionally find or create missing ones.'),
      notice('ok', null,
        'Existing subtitle files are never modified or replaced. Anything ' +
        'generated is written alongside them as a new file.'),
      checkField('subtitles.use_existing', 'Use subtitles I already have',
        { fallback: true }),
      textField('subtitles.languages', 'Languages', {
        value: get('subtitles.languages', ['en']).join(', '),
        hint: 'Comma-separated codes, for example: en, es',
      }),
      checkField('subtitles.subdl_enabled', 'Look up missing subtitles online (SubDL)',
        { onChange: (on) => { subdl.hidden = !on; } }),
      subdl,
      checkField('subtitles.whisper_enabled',
        'Create subtitles from the audio when none can be found', {
        hint: 'Runs speech recognition on this machine. Nothing is sent anywhere.',
        onChange: (on) => { whisper.hidden = !on; },
      }),
      whisper,
      actions('Continue', () => submitStage('subtitles')),
    ];
  },

  integrations() {
    return [
      el('h2', {}, 'Notifications and extras'),
      el('p', { class: 'lede' }, 'All optional. Skip if you are not sure.'),
      textField('integrations.webex_webhook_url', 'Webex webhook for notifications', {
        password: true, placeholder: 'https://webexapis.com/v1/webhooks/incoming/…',
        hint: 'Sends a message when a recording starts or fails, or when the ' +
              'server needs attention.',
      }),
      checkField('integrations.homepage_enabled',
        'Publish library counts for dashboard widgets', {
        hint: 'Exposes a small JSON summary that dashboards such as Homepage read.',
      }),
      textField('integrations.transcode_control_url',
        'Existing Transcode Control address (optional)',
        { placeholder: 'http://192.168.1.50:8126' }),
      textField('integrations.transcode_control_db',
        'Transcode Control database (optional)', {
        placeholder: '/home/user/transcode-control/data/transcode-control.db',
        hint: 'Only needed for the Whisper start/stop buttons on the Modules page.',
      }),
      actions('Continue', () => submitStage('integrations')),
    ];
  },

  modules() {
    const s = S.suggest || {};
    return [
      el('h2', {}, 'Modules and sizing'),
      el('p', { class: 'lede' },
        'Choose which parts to install and how much of this machine ' +
        'CineMediaVault may use.'),
      el('div', { class: 'card' },
        el('h3', {}, 'Libraries'),
        checkField('modules.movies', 'Movies', { fallback: true }),
        checkField('modules.tv', 'TV shows', { fallback: true }),
        checkField('modules.music', 'Music, with background playback'),
        checkField('modules.bookvault', 'Book Vault (ebooks and audiobooks)'),
        checkField('modules.comics', 'Comics'),
        checkField('modules.video_wall', 'Video Wall (four streams at once)',
          { fallback: true }),
        checkField('modules.downloads', 'Downloads and the Android companion app',
          { fallback: true })),
      el('div', { class: 'card' },
        el('h3', {}, 'Games'),
        el('p', { class: 'hint' },
          'Browser-playable retro game libraries. You supply your own game ' +
          'files; nothing is downloaded for you.'),
        chipGroup('modules.games', '', [
          ['nes', 'NES'], ['sega', 'SEGA'], ['dos', 'DOS'], ['mame', 'Arcade (MAME)'],
          ['gameboy', 'Game Boy'], ['gba', 'GBA'], ['n64', 'N64'], ['ps1', 'PS1'],
          ['c64', 'C64'], ['atari2600', 'Atari 2600'], ['atari5200', 'Atari 5200'],
          ['atari7800', 'Atari 7800'],
        ])),
      el('div', { class: 'card' },
        el('h3', {}, 'Creator tools'),
        el('p', { class: 'hint' },
          'Offline generators that turn raw files into browsable libraries. They ' +
          'only read your source files and write into a separate folder.'),
        chipGroup('modules.creators', '', [
          ['comics', 'Comic book creators'],
          ['books', 'Book and magazine creators'],
          ['games', 'Game library creators'],
        ])),
      el('div', { class: 'card' },
        el('h3', {}, 'Playback'),
        checkField('transcode.hls_enabled',
          'Convert on the fly for devices that cannot play a file directly',
          { fallback: true }),
        selectField('transcode.hls_encoder', 'Use hardware acceleration', [
          ['auto', `Choose automatically (detected: ${s.encoder || 'cpu'})`],
          ['cpu', 'Processor only'],
          ['nvenc', 'NVIDIA'], ['qsv', 'Intel Quick Sync'], ['vaapi', 'AMD / VA-API'],
        ]),
        selectField('transcode.default_playback_mode', 'Default playback', [
          ['direct', 'Play the original file when possible (recommended)'],
          ['hls', 'Always convert'],
        ]),
        checkField('transcode.library_queue_enabled',
          'Install the bulk library re-encoder', {
          hint: 'Installed stopped and empty. It rewrites your source files, so ' +
                'it must be started deliberately, from the command line, after ' +
                'you have reviewed its settings.',
        })),
      el('div', { class: 'card' },
        el('h3', {}, 'Resources'),
        textField('resources.app_memory_limit_mb', 'Memory limit (MB)', {
          number: true, min: 512, max: 131072,
          fallback: s.suggested_app_memory_mb || 2048,
          hint: `This machine has ${s.memory_mb || '?'} MB in total.`,
        }),
        textField('resources.app_cpu_quota_percent', 'Processor limit (%)', {
          number: true, min: 0, max: 6400, fallback: 0,
          hint: `0 means no limit. 100 is one core; this machine has ` +
                `${s.cpu_count || '?'}.`,
        })),
      actions('Review everything', () => submitStage('modules')),
    ];
  },

  async review() {
    const stage = document.getElementById('stage');
    stage.replaceChildren(el('div', { class: 'loading' },
      el('span', { class: 'spinner' }), 'Checking this machine…'));
    let data;
    try {
      data = await api('/api/review');
    } catch (error) {
      setStageContent(el('h2', {}, 'Review'),
        notice('err', 'Could not build the review', error.message),
        actions('Try again', () => render()));
      return;
    }

    const s = data.summary;
    const pf = data.preflight;
    const blocking = data.errors.length || !pf.can_install;

    setStageContent(
      el('h2', {}, 'Ready to install'),
      el('p', { class: 'lede' },
        'Here is exactly what will happen. Nothing has been changed yet.'),
      blocking ? notice('err', 'These must be fixed first',
        el('ul', {}, ...data.errors.map((e) => el('li', {}, `${e.key}: ${e.message}`)),
          ...pf.results.filter((r) => r.status === 'fail')
            .map((r) => el('li', {}, `${r.title}: ${r.detail}`)))) : null,
      data.warnings.length ? notice('warn', 'Worth knowing',
        el('ul', {}, ...data.warnings.map((w) => el('li', {}, w.message)))) : null,

      el('div', { class: 'card' },
        el('h3', {}, 'Your server'),
        el('table', { class: 'summary' }, el('tbody', {},
          row('Name', s.instance),
          row('Address', s.url),
          row('Sign in as', s.admin),
          row('Time zone', s.timezone),
          row('HTTPS', s.tls === 'self_signed' ? 'self-signed certificate' : s.tls),
          row('Memory limit', `${s.memory_limit_mb} MB`)))),

      s.libraries.length ? el('div', { class: 'card' },
        el('h3', {}, 'Libraries'),
        el('table', { class: 'summary' }, el('tbody', {},
          ...s.libraries.map((l) => row(l.label, l.path))))) : null,

      s.features.length ? el('div', { class: 'card' },
        el('h3', {}, 'Features'),
        el('ul', {}, ...s.features.map((f) => el('li', {}, f)))) : null,

      el('div', { class: 'card' },
        el('h3', {}, `Machine checks (${pf.passed} passed, ${pf.warnings} warnings, ` +
                     `${pf.failures} failures)`),
        el('ul', { class: 'checklist' },
          ...pf.results.map((r) => el('li', { class: r.status === 'pass' ? 'pass' :
              (r.status === 'warn' ? 'warn' : 'fail') },
            r.title,
            r.detail ? el('div', { class: 'detail' }, r.detail) : null,
            (r.remedy && r.status !== 'pass')
              ? el('div', { class: 'detail' }, '→ ' + r.remedy) : null)))),

      el('div', { class: 'card' },
        el('h3', {}, 'Steps that will run'),
        el('ul', { class: 'steplist' },
          ...data.plan.filter((p) => p.applies).map((p) => el('li', {},
            el('span', { class: 'state ' + (p.will_run ? 'pending' : 'skipped') },
              p.will_run ? 'WILL RUN' : 'DONE'),
            el('div', {}, el('div', {}, p.title),
              el('div', { class: 'why' }, p.preview)))))),

      el('div', { class: 'actions' },
        el('button', { class: 'secondary', onClick: goBack }, 'Back'),
        el('button', { class: 'secondary',
          onClick: () => startInstall(true) }, 'Dry run (change nothing)'),
        el('button', { class: 'primary', disabled: !!blocking,
          onClick: () => startInstall(false) }, 'Install now')),
    );
  },

  install() {
    return renderInstallScreen();
  },
};

/* --------------------------------------------------------- install screen */

let installState = { logEl: null, stepsEl: null, barEl: null, dry: false };

function renderInstallScreen() {
  const bar = el('div', { class: 'progress-bar' }, el('div', { style: 'width:0%' }));
  const steps = el('ul', { class: 'steplist' });
  const log = el('div', { class: 'log', id: 'install-log' });
  installState = { logEl: log, stepsEl: steps, barEl: bar.firstChild, dry: false };

  return [
    el('h2', {}, 'Installing'),
    el('p', { class: 'lede' },
      'You can close this page and come back - the installation keeps going, ' +
      'and reopening this page shows where it got to.'),
    bar,
    el('div', { id: 'install-result' }),
    steps,
    el('h3', { style: 'margin-top:20px;font-size:15px' }, 'Details'),
    el('p', { class: 'hint' },
      'Passwords and API keys are removed from these messages before they are ' +
      'written anywhere.'),
    log,
  ];
}

async function startInstall(dryRun) {
  S.stage = 'install';
  setStageContent(...renderInstallScreen());
  installState.dry = dryRun;
  S.since = 0;
  try {
    await api('/api/install', { body: { dry_run: dryRun } });
  } catch (error) {
    document.getElementById('install-result').replaceChildren(
      notice('err', 'Could not start', error.message));
    return;
  }
  pollProgress();
}

async function pollProgress() {
  if (S.pollTimer) clearTimeout(S.pollTimer);
  let data;
  try {
    data = await api(`/api/progress?since=${S.since}`);
  } catch (error) {
    S.pollTimer = setTimeout(pollProgress, 3000);
    return;
  }
  S.since = data.next_since;

  for (const record of data.logs || []) appendLog(record);
  for (const event of data.events || []) applyEvent(event);

  if (data.report) {
    showResult(data.report);
    return;
  }
  if (data.running || data.phase === 'installing') {
    S.pollTimer = setTimeout(pollProgress, 1000);
  } else {
    S.pollTimer = setTimeout(pollProgress, 2500);
  }
}

const seenLogs = new Set();
function appendLog(record) {
  const key = `${record.seq}`;
  if (seenLogs.has(key)) return;
  seenLogs.add(key);
  const log = installState.logEl || document.getElementById('install-log');
  if (!log) return;
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.append(el('div', { class: `lvl-${record.level}` },
    `${(record.time || '').slice(11, 19)}  ${record.message}`));
  if (atBottom) log.scrollTop = log.scrollHeight;
}

const stepRows = new Map();
function applyEvent(event) {
  const steps = installState.stepsEl;
  if (!steps) return;
  if (event.event === 'step_started' || event.event === 'step_finished' ||
      event.event === 'step_skipped') {
    let row = stepRows.get(event.step);
    if (!row) {
      row = el('li', {}, el('span', { class: 'state pending' }, 'PENDING'),
        el('div', {}, el('div', {}, event.title || event.step),
          el('div', { class: 'why' }, '')));
      stepRows.set(event.step, row);
      steps.append(row);
    }
    const state = row.querySelector('.state');
    const why = row.querySelector('.why');
    if (event.event === 'step_started') {
      state.className = 'state running'; state.textContent = 'RUNNING';
    } else if (event.event === 'step_skipped') {
      state.className = 'state skipped'; state.textContent = 'SKIPPED';
      why.textContent = event.summary || event.reason || '';
    } else {
      state.className = 'state completed'; state.textContent = 'DONE';
      why.textContent = event.summary || '';
      if (event.warnings && event.warnings.length) {
        why.textContent += (why.textContent ? ' — ' : '') + event.warnings.join(' ');
      }
    }
    if (event.total && installState.barEl) {
      installState.barEl.style.width =
        `${Math.round((event.index / event.total) * 100)}%`;
    }
  } else if (event.event === 'run_failed') {
    for (const row of stepRows.values()) {
      const state = row.querySelector('.state');
      if (state.textContent === 'RUNNING') {
        state.className = 'state failed'; state.textContent = 'FAILED';
      }
    }
  }
}

function showResult(report) {
  const target = document.getElementById('install-result');
  if (!target) return;
  if (installState.barEl) installState.barEl.style.width = '100%';

  if (report.dry_run) {
    target.replaceChildren(notice('ok', 'Dry run complete',
      'Nothing on this machine was changed. Go back and choose "Install now" ' +
      'when you are ready.'),
      el('div', { class: 'actions' },
        el('button', { class: 'secondary', onClick: goBack }, 'Back to review')));
    return;
  }

  if (!report.ok) {
    target.replaceChildren(
      notice('err', 'Installation did not finish',
        el('div', {},
          el('p', {}, report.error || 'See the details below.'),
          report.failed_step ? el('p', {}, `Stopped at: ${report.failed_step}`) : null,
          el('p', {},
            'Nothing is half-written: every file that was replaced was backed up ' +
            'first. Fix the problem and run the installer again - it carries on ' +
            'from where it stopped rather than starting over.'),
          report.failures ? el('ul', {},
            ...report.failures.map((f) => el('li', {},
              `${f.title}: ${f.detail}${f.remedy ? ' → ' + f.remedy : ''}`))) : null)),
      el('div', { class: 'actions' },
        el('button', { class: 'secondary', onClick: goBack }, 'Back'),
        el('button', { class: 'primary', onClick: () => startInstall(false) },
          'Try again')));
    return;
  }

  target.replaceChildren(
    notice('ok', 'CineMediaVault is ready',
      el('div', {},
        el('p', {}, 'Open it at ',
          el('a', { href: report.url, target: '_blank', rel: 'noreferrer' }, report.url),
          ` and sign in as ${report.admin}.`),
        report.warnings && report.warnings.length
          ? el('div', {}, el('p', {}, 'Worth knowing:'),
              el('ul', {}, ...report.warnings.map((w) => el('li', {}, w))))
          : null)),
    el('div', { class: 'card' },
      el('h3', {}, 'What to do next'),
      el('ul', {},
        el('li', {}, 'Open the address above and sign in.'),
        el('li', {}, 'Your libraries will fill in over the next few minutes as ' +
                     'they are scanned.'),
        el('li', {}, 'Run ', el('code', {}, 'sudo cinevaultctl smoke-test'),
                     ' to confirm everything is healthy.'),
        el('li', {}, 'Run ', el('code', {}, 'sudo cinevaultctl backup'),
                     ' to take your first backup.'))),
    el('div', { class: 'actions' },
      el('a', { class: 'primary', href: report.url, target: '_blank',
        rel: 'noreferrer', style: 'text-decoration:none;display:inline-block' },
        'Open CineMediaVault'),
      el('button', { class: 'secondary', onClick: finish },
        'Close setup')));
}

async function finish() {
  try { await api('/api/finish', { body: {} }); } catch { /* it is stopping */ }
  setStageContent(
    el('h2', {}, 'Setup finished'),
    el('p', { class: 'lede' },
      'The setup service has stopped. You can close this tab.'));
}

/* ------------------------------------------------------------------- shell */

function row(label, value) {
  return el('tr', {}, el('td', {}, label), el('td', {}, value));
}

function field(label, input, hint) {
  return el('div', { class: 'field' }, el('label', {}, label),
    hint ? el('p', { class: 'hint' }, hint) : null, input);
}

function passwordStrength(password) {
  if (!password) return { kind: '', text: '' };
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].filter((r) => r.test(password)).length;
  if (password.length < 12) {
    return { kind: 'err', text: `${password.length} of 12 characters minimum` };
  }
  if (classes < 3) {
    return { kind: 'warn', text: 'Add an upper-case letter, digit or symbol' };
  }
  if (password.length >= 16 && classes >= 3) {
    return { kind: 'ok', text: 'Strong' };
  }
  return { kind: 'ok', text: 'Good' };
}

function renderSteps() {
  const nav = document.getElementById('steps');
  if (!S.progress) { nav.replaceChildren(); return; }
  nav.replaceChildren(el('ol', {}, ...S.progress.stages.map((stage, index) =>
    el('li', {}, el('button', {
      class: (stage.current ? 'current ' : '') + (stage.complete ? 'done' : ''),
      disabled: !stage.reachable && !stage.current,
      onClick: async () => {
        if (!stage.reachable) return;
        await api('/api/goto', { body: { stage: stage.id } });
        await refreshState();
        render();
      },
    }, el('span', { class: 'dot' }, stage.complete ? '✓' : String(index + 1)),
       stage.title)))));
}

async function refreshState() {
  const data = await api('/api/state');
  S.csrf = data.csrf || S.csrf;
  S.config = data.config || {};
  S.progress = data.progress;
  S.stage = data.progress.current;
}

async function render() {
  collected.clear();
  renderSteps();
  const renderer = STAGE_RENDERERS[S.stage] || STAGE_RENDERERS.welcome;
  const warnings = S.pendingWarnings;
  S.pendingWarnings = null;
  const nodes = await renderer();
  if (Array.isArray(nodes)) {
    if (warnings && warnings.length) {
      nodes.splice(2, 0, notice('warn', 'From the previous step',
        el('ul', {}, ...warnings.map((w) => el('li', {}, w.message)))));
    }
    setStageContent(...nodes);
  }
  if (S.stage === 'install') pollProgress();
}

function renderSignIn(message) {
  document.getElementById('steps').replaceChildren();
  const input = el('input', { type: 'password', id: 'bootstrap-token',
    placeholder: 'setup code', autocomplete: 'one-time-code', autofocus: true });
  const msg = el('div', { class: 'msg' });
  const submit = async () => {
    msg.textContent = 'Checking…'; msg.className = 'msg';
    try {
      const data = await api('/api/session', { body: { token: input.value.trim() } });
      S.csrf = data.csrf;
      await boot();
    } catch (error) {
      msg.textContent = error.message; msg.className = 'msg err';
      input.select();
    }
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  setStageContent(
    el('h2', {}, 'Enter the setup code'),
    el('p', { class: 'lede' },
      'The code was printed on the console where you started the installer. ' +
      'It proves you have access to this machine.'),
    message ? notice('err', null, message) : null,
    el('div', { class: 'field' },
      el('label', { for: 'bootstrap-token' }, 'Setup code'), input, msg),
    el('div', { class: 'actions' },
      el('button', { class: 'primary', onClick: submit }, 'Continue')));
  input.focus();
}

async function boot() {
  try {
    await refreshState();
  } catch (error) {
    if (error.status === 401) { renderSignIn(); return; }
    setStageContent(notice('err', 'Cannot reach the setup service', error.message));
    return;
  }
  try {
    const [suggest, tz] = await Promise.all([
      api('/api/suggest'), api('/api/timezones'),
    ]);
    S.suggest = suggest;
    S.suggest.detected_timezone = tz.detected;
    S.timezones = tz.timezones;
  } catch { /* the wizard still works without these */ }
  await render();
}

document.addEventListener('DOMContentLoaded', boot);
