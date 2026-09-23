// Settings: server address, access code, connection test, storage, help.

import { h, fill, bytes, store } from '../util.js';
import { icon } from '../icons.js';
import { getStatus } from '../api.js';
import { APP_VERSION, getServer, getToken, setServer, setToken, normalizeServer } from '../config.js';
import { state, refreshStatus } from '../state.js';
import { callout, spinner, toast, confirmSheet } from '../ui.js';
import * as stats from '../stats.js';
import * as offline from '../offline.js';

export function mount(root) {
  let alive = true;
  const authMsg = state.authMessage;
  state.authMessage = null;

  const server = h('input', {
    class: 'input',
    type: 'url',
    inputmode: 'url',
    autocapitalize: 'off',
    autocorrect: 'off',
    spellcheck: 'false',
    autocomplete: 'off',
    placeholder: 'Same as this page',
    value: getServer(),
    id: 'set-server',
    enterkeyhint: 'next',
  });
  const token = h('input', {
    class: 'input',
    type: 'password',
    autocapitalize: 'off',
    autocorrect: 'off',
    spellcheck: 'false',
    autocomplete: 'off',
    placeholder: 'Optional',
    value: getToken(),
    id: 'set-token',
    enterkeyhint: 'done',
  });
  const reveal = h('button', { class: 'input-addon', type: 'button', text: 'Show', 'aria-label': 'Show access code' });
  reveal.addEventListener('click', () => {
    const show = token.type === 'password';
    token.type = show ? 'text' : 'password';
    reveal.textContent = show ? 'Hide' : 'Show';
    reveal.setAttribute('aria-label', show ? 'Hide access code' : 'Show access code');
  });
  const result = h('div', { class: 'test-result', 'aria-live': 'polite' });
  const saveBtn = h('button', { class: 'btn btn-primary btn-block', type: 'submit', text: 'Save' });
  const testBtn = h('button', { class: 'btn btn-secondary btn-block', type: 'button' }, icon('link', 20), h('span', { text: 'Test connection' }));

  const form = h(
    'form',
    { class: 'card form-card', novalidate: true },
    h('label', { class: 'field', for: 'set-server' }, h('span', { class: 'field-label', text: 'Server address' })),
    server,
    h('p', { class: 'field-help', text: 'Leave empty when this app is opened from your audiobook server. Otherwise enter its address, e.g. https://books.example.com' }),
    h('label', { class: 'field', for: 'set-token' }, h('span', { class: 'field-label', text: 'Access code' })),
    h('div', { class: 'input-group' }, token, reveal),
    h('p', { class: 'field-help', text: 'The code set on the server with AUDIOBOOK_TOKEN. A pairing link fills this in for you.' }),
    result,
    h('div', { class: 'btn-col' }, saveBtn, testBtn),
  );

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    save();
    toast('Saved', { kind: 'ok' });
    test();
  });
  testBtn.addEventListener('click', test);
  server.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      token.focus();
    }
  });

  function save() {
    setServer(server.value);
    setToken(token.value);
    server.value = getServer();
    refreshStatus();
  }

  async function test() {
    const base = normalizeServer(server.value);
    const tk = token.value.trim();
    testBtn.disabled = true;
    result.replaceChildren(h('div', { class: 'loading-row' }, spinner('sm'), h('span', { text: 'Contacting the server…' })));
    try {
      const s = await getStatus({ base, token: tk, quiet401: true });
      if (!alive) return;
      const eng = s.engine || {};
      const stateText = { ready: 'Ready', idle: 'Ready (idle)', loading: 'Loading voices…', error: 'Error' }[eng.state] || eng.state || 'unknown';
      const rows = [
        ['Server', `${s.app || 'Audiobook server'} ${s.version || ''}`.trim()],
        ['Engine', eng.label || eng.name || 'Unknown'],
        ['Engine state', stateText + (eng.detail ? `: ${eng.detail}` : '')],
      ];
      if (eng.realtime_factor) rows.push(['Speed', `${eng.realtime_factor}× faster than real time`]);
      let head;
      if (s.auth_required && !s.authorized) {
        head = callout('warn', tk ? 'Connected, but the access code is wrong.' : 'Connected. This server needs an access code.');
      } else if (eng.state === 'error') {
        head = callout('error', 'Connected, but the voice engine reports an error.');
      } else {
        head = h('div', { class: 'callout callout-ok' }, icon('check', 20), h('div', { class: 'callout-body', text: 'Connected' + (s.auth_required ? '. Access code accepted.' : '.') }));
      }
      fill(result, head, h('dl', { class: 'kv' }, rows.map(([k, v]) => h('div', { class: 'kv-row' }, h('dt', { text: k }), h('dd', { text: v })))), eng.is_demo ? callout('demo', 'Demo engine: placeholder tones, not real AuK speech.') : null);
    } catch (err) {
      if (!alive) return;
      result.replaceChildren(callout('error', err.network ? 'Couldn’t reach the server at this address. Check the address and that the server is running.' : err.message));
    } finally {
      testBtn.disabled = false;
    }
  }

  // name + daily goal
  const nameInput = h('input', { class: 'input', type: 'text', id: 'set-name', autocomplete: 'given-name', autocapitalize: 'words', maxlength: '40', placeholder: 'For example, Youssef', value: store.get('name', '') || '', enterkeyhint: 'done' });
  nameInput.addEventListener('change', () => {
    store.set('name', nameInput.value.trim());
    toast('Saved', { kind: 'ok' });
  });
  nameInput.addEventListener('keydown', (e) => e.key === 'Enter' && nameInput.blur());
  const goalSeg = h('div', { class: 'seg seg-sm', role: 'radiogroup', 'aria-labelledby': 'goal-label' });
  function renderGoal() {
    const cur = stats.goalMinutes();
    fill(
      goalSeg,
      stats.GOALS.map((g) => {
        const b = h('button', { class: 'seg-btn', type: 'button', role: 'radio', 'aria-checked': String(g === cur), 'aria-label': `${g} minutes` }, h('span', { class: 'seg-main', text: String(g) }), h('span', { class: 'seg-sub', text: 'min' }));
        b.addEventListener('click', () => {
          stats.setGoal(g);
          renderGoal();
        });
        return b;
      }),
    );
  }
  renderGoal();

  // storage
  const storageText = h('span', { class: 'muted' });
  const clearBtn = h('button', { class: 'link-btn danger', type: 'button' }, icon('trash', 18), h('span', { text: 'Remove all downloads' }));
  clearBtn.addEventListener('click', async () => {
    const ok = await confirmSheet({ title: 'Remove all downloads?', message: 'Books stay in your library and on the server; only the copies on this phone are removed.', confirmLabel: 'Remove downloads', danger: true });
    if (!ok) return;
    await offline.removeAll();
    renderStorage();
    toast('Downloads removed');
  });
  function renderStorage() {
    const n = offline.offlineBookIds().length;
    storageText.textContent = offline.supported() ? (n ? `${n} book${n === 1 ? '' : 's'} · ${bytes(offline.totalBytes())} on this phone` : 'No books downloaded yet.') : 'Offline downloads need the app to be opened over HTTPS.';
    clearBtn.hidden = !n;
  }
  renderStorage();

  const page = h(
    'div',
    { class: 'page page-tab page-settings' },
    h('h1', { class: 'page-title', text: 'Settings' }),
    authMsg ? callout('warn', h('span', {}, h('strong', { text: 'Access code needed. ' }), authMsg)) : null,
    h('h2', { class: 'section-title', text: 'You' }),
    h(
      'div',
      { class: 'card' },
      h('label', { class: 'field-label', for: 'set-name', text: 'Your name' }),
      nameInput,
      h('p', { class: 'field-help', text: 'Optional. Used to greet you in the library.' }),
      h('span', { class: 'field-label', id: 'goal-label', text: 'Daily listening goal' }),
      goalSeg,
      h('p', { class: 'field-help', text: 'Days you reach it keep your streak going and light up the calendar.' }),
    ),
    h('h2', { class: 'section-title spaced', text: 'Connection' }),
    form,
    h('h2', { class: 'section-title spaced', text: 'Downloads' }),
    h('div', { class: 'card' }, h('div', { class: 'storage-row' }, icon('downloaded', 22), storageText), clearBtn),
    h('h2', { class: 'section-title spaced', text: 'Add to Home Screen' }),
    h(
      'div',
      { class: 'card help' },
      h(
        'ol',
        { class: 'steps compact' },
        helpStep(1, 'Open this page in Safari on your iPhone.'),
        helpStep(2, h('span', {}, 'Tap the Share button ', h('span', { class: 'inline-ic' }, icon('share', 16)), ' at the bottom of the screen.')),
        helpStep(3, 'Scroll down and choose “Add to Home Screen”, then “Add”.'),
      ),
      h('p', { class: 'field-help', text: 'Open Audiobooks from the Home Screen icon: it runs full screen and shows the lock-screen controls.' }),
      h(
        'ul',
        { class: 'help-list' },
        h('li', {}, icon('moon', 16), h('span', { text: 'Listening with the screen locked works best from the Home Screen app.' })),
        h('li', {}, icon('download', 16), h('span', { text: 'For very long sessions you can also download the MP3 or M4B (book ••• menu) and play it in Apple Books or Files.' })),
      ),
    ),
    h('div', { class: 'about' }, h('img', { src: 'icons/icon.svg', alt: '', width: '40', height: '40' }), h('div', {}, h('div', { class: 'about-name', text: 'AuK Audiobooks' }), h('div', { class: 'muted', text: `App version ${APP_VERSION}` + (state.status && state.status.version ? ` · Server ${state.status.version}` : '') }))),
  );

  root.append(h('div', { class: 'sb-scrim', 'aria-hidden': 'true' }), page);

  if (authMsg) setTimeout(() => token.focus(), 300);

  return () => {
    alive = false;
  };
}

function helpStep(n, content) {
  return h('li', { class: 'step' }, h('span', { class: 'step-n', text: String(n) }), h('div', { class: 'step-text' }, content));
}
