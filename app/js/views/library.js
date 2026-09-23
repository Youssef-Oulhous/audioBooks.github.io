// Library ("your bibliothèque"): greeting, today's goal and streak, continue listening,
// and the bookshelf of water covers.

import { h, fill, clock, long, store, throttle } from '../util.js';
import { icon } from '../icons.js';
import { listBooks } from '../api.js';
import { state, bus, rememberBooks, snapshotBooks, refreshStatus } from '../state.js';
import { callout, goalRing } from '../ui.js';
import { player, includedChapters } from '../player.js';
import { waterCover, creationInfo } from '../water.js';
import * as offline from '../offline.js';
import * as stats from '../stats.js';

export function bookHref(b) {
  return b.status === 'draft' ? `#/new/${encodeURIComponent(b.id)}/chapters` : `#/book/${encodeURIComponent(b.id)}`;
}

/** One-line status for a book (shelf note, lists). */
export function statusLine(b) {
  const p = b.progress || {};
  switch (b.status) {
    case 'ready':
      return { cls: 'ok', text: `Ready, ${long(b.audio_seconds)}` };
    case 'rendering': {
      const eta = p.eta_seconds == null ? '' : p.eta_seconds < 60 ? ', almost done' : `, about ${long(p.eta_seconds)} left`;
      return { cls: 'accent', text: `Creating ${Math.floor(p.percent || 0)}%${eta}` };
    }
    case 'queued':
      return { cls: 'muted', text: 'Queued' };
    case 'draft':
      return { cls: 'warn', text: 'Finish setup' };
    case 'paused':
      return { cls: 'muted', text: `Paused at ${Math.floor(p.percent || 0)}%` };
    case 'error':
      return { cls: 'err', text: 'Needs attention' };
    default:
      return { cls: 'muted', text: b.status || '' };
  }
}

/** Engine status chip: only shown when something needs attention. */
export function engineChip(status, statusError) {
  let cls = '';
  let text = '';
  let ic = null;
  if (status && status.engine) {
    const st = status.engine.state;
    if (status.auth_required && !status.authorized) [cls, text, ic] = ['warn', 'Locked', 'alert'];
    else if (st === 'loading') [cls, text, ic] = ['warn', 'Loading voices', 'hourglass'];
    else if (st === 'error') [cls, text, ic] = ['err', 'Engine error', 'alert'];
  } else if (statusError === 'offline') [cls, text, ic] = ['', 'Offline', 'cloudOff'];
  else if (statusError) [cls, text, ic] = ['err', 'Server error', 'alert'];
  if (!text) return null;
  return h('span', { class: `status-chip ${cls}`, title: (status && status.engine && status.engine.label) || '' }, icon(ic, 16), text);
}

export function greeting(date = new Date()) {
  const hr = date.getHours();
  const part = hr < 5 ? 'Good night' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
  const name = String(store.get('name', '') || '').trim();
  return name ? `${part}, ${name}` : part;
}

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'listening', label: 'Listening' },
  { id: 'finished', label: 'Finished' },
  { id: 'creating', label: 'Creating' },
];

function inFilter(b, f) {
  if (f === 'all') return true;
  if (f === 'creating') return b.status !== 'ready';
  const pct = stats.progress(b);
  const done = stats.isFinished(b.id) || pct >= 100;
  if (f === 'finished') return done;
  return !done && pct > 0; // listening
}

let firstLoad = true;

export function mount(root) {
  let alive = true;
  let timer = null;
  let statusTimer = null;
  let filter = FILTERS.some((f) => f.id === store.get('shelf.filter')) ? store.get('shelf.filter') : 'all';
  const items = new Map(); // id -> shelf item api
  let contKey = '';
  let contCover = null;

  const title = h('h1', { class: 't-display', text: greeting() });
  const chipSlot = h('div', { class: 'chip-slot' });
  const addBtn = h('a', { class: 'icon-btn filled', href: '#/new', 'aria-label': 'Add a book', title: 'Add a book' }, icon('plus', 24));
  const head = h('header', { class: 'lib-head' }, title, h('div', { class: 'lib-head-actions' }, chipSlot, addBtn));
  const banners = h('div', { class: 'banners' });
  const ring = goalRing(60);
  const todayLine = h('div', { class: 'today-line' });
  const todaySub = h('div', { class: 'today-sub' });
  const today = h('a', { class: 'today-strip', href: '#/stats', 'aria-label': 'Today, open stats' }, ring, h('div', { class: 'today-text' }, todayLine, todaySub), icon('chevronRight', 20));
  const contSlot = h('div', { class: 'continue-slot' });
  const countEl = h('span', { class: 'section-meta' });
  const seg = h('div', { class: 'seg', role: 'tablist', 'aria-label': 'Filter books' });
  const segBtns = FILTERS.map((f) => {
    const b = h('button', { class: 'seg-btn', type: 'button', role: 'tab', text: f.label, 'aria-selected': String(f.id === filter) });
    b.addEventListener('click', () => {
      filter = f.id;
      store.set('shelf.filter', filter);
      segBtns.forEach((x) => x.setAttribute('aria-selected', String(x === b)));
      renderShelf();
    });
    seg.append(b);
    return b;
  });
  const shelfHead = h('div', { class: 'shelf-head' }, h('div', { class: 'shelf-head-row' }, h('h2', { class: 'section-title', text: 'Your books' }), countEl), seg);
  const shelf = h('div', { class: 'shelf' });
  const addTile = h('a', { class: 'add-tile', href: '#/new' }, h('span', { class: 'add-tile-ic' }, icon('plus', 26)), h('span', { text: 'Add a book' }));
  const empty = h('div', { class: 'empty-slot' });
  const page = h('div', { class: 'page page-tab page-library' }, head, banners, today, contSlot, shelfHead, shelf, empty);
  root.append(h('div', { class: 'sb-scrim', 'aria-hidden': 'true' }), page);

  let books = state.books.size ? [...state.books.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0)) : null;
  if (books) render();
  else skeleton();

  function skeleton() {
    fill(
      shelf,
      [0, 1, 2, 3].map(() => h('div', { class: 'shelf-item', 'aria-hidden': 'true' }, h('div', { class: 'sk sk-cover' }), h('div', { class: 'sk sk-line', style: 'width:80%' }), h('div', { class: 'sk sk-line', style: 'width:50%' }))),
    );
    shelfHead.hidden = false;
  }

  /* ---------- header, banners, today ---------- */

  function renderChip() {
    fill(chipSlot, engineChip(state.status, state.statusError));
    renderBanners();
  }

  function renderBanners() {
    banners.replaceChildren();
    const s = state.status;
    if (state.offline) banners.append(callout('offline', h('span', {}, h('strong', { text: 'Offline.' }), ' Showing downloaded books.')));
    if (s && s.auth_required && !s.authorized) banners.append(callout('warn', h('span', {}, 'This server needs an access code. ', h('a', { href: '#/settings', text: 'Open Settings' }))));
    if (s && s.engine && s.engine.is_demo) banners.append(callout('demo', h('span', {}, h('strong', { text: 'Demo engine:' }), ' placeholder tones, not real AuK speech.')));
    if (s && s.engine && s.engine.state === 'error') banners.append(callout('error', `Voice engine error: ${s.engine.detail || 'unknown problem'}.`));
  }

  function renderToday() {
    title.textContent = greeting();
    const k = stats.dateKey();
    const listen = stats.listenOn(k);
    const goal = stats.goalMinutes();
    const st = stats.streak(k);
    ring.set(listen, goal);
    const left = Math.max(0, Math.ceil((goal * 60 - listen) / 60));
    todayLine.textContent = left <= 0 ? 'Goal reached.' : `${left} ${left === 1 ? 'minute' : 'minutes'} to today's goal`;
    fill(
      todaySub,
      st.current ? h('span', { class: 'flame' }, icon('flame', 18)) : icon('flameLine', 18),
      h('span', { text: st.current ? `${st.current} ${st.current === 1 ? 'day' : 'days'} in a row` : '5 minutes a day starts a streak' }),
    );
  }

  /* ---------- continue listening ---------- */

  function visibleBooks() {
    if (!books) return [];
    if (!state.offline) return books;
    return books.filter((b) => offline.status(b).state !== 'none');
  }

  function renderContinue() {
    const last = store.get('last');
    const b = last && visibleBooks().find((x) => x.id === last.bookId);
    const rp = b && player.resumePoint(b);
    if (!rp || (state.offline && !offline.isChapterOffline(b, rp.chapter))) {
      contSlot.replaceChildren();
      contKey = '';
      contCover = null;
      return;
    }
    const ch = rp.chapter;
    const inc = includedChapters(b);
    const isCurrent = player.book && player.book.id === b.id;
    const playing = isCurrent && player.playing;
    const chLeft = ch.duration ? Math.max(0, ch.duration - rp.time) : null;
    const key = [b.id, ch.index, playing, b.cover_url].join('|');
    const meta = `Chapter ${inc.indexOf(ch) + 1} of ${inc.length}` + (chLeft != null ? ` · ${clock(chLeft)} left` : '');
    if (key === contKey && contSlot.firstChild) {
      contSlot.querySelector('.hc-meta').textContent = meta;
      if (contCover) contCover.setFill(stats.progress(b));
      return;
    }
    contKey = key;
    contCover = waterCover(b, { pct: stats.progress(b), size: 'lg' });
    const playBtn = h('button', { class: 'btn btn-primary hc-play', type: 'button', 'aria-label': playing ? 'Pause' : `Resume ${b.title}` }, icon(playing ? 'pause' : 'play', 22), h('span', { text: playing ? 'Pause' : 'Resume' }));
    playBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (isCurrent) player.toggle();
      else player.resumeBook(b);
    });
    fill(
      contSlot,
      h(
        'a',
        { class: 'hero-continue', href: bookHref(b) },
        contCover,
        h('div', { class: 'hc-text' }, h('div', { class: 'hc-kicker', text: playing ? 'Now playing' : 'Continue listening' }), h('div', { class: 'hc-title', text: b.title }), h('div', { class: 'hc-meta', text: meta }), playBtn),
      ),
    );
  }

  /* ---------- shelf ---------- */

  function shelfItem(b) {
    const cover = waterCover(b, { pct: stats.progress(b), size: 'md', creating: creationInfo(b) });
    const titleEl = h('div', { class: 'shelf-title' });
    const author = h('div', { class: 'shelf-author' });
    const note = h('div', { class: 'shelf-note' });
    const el = h('a', { class: 'shelf-item', href: bookHref(b) }, cover, titleEl, author, note);
    return {
      el,
      update(book) {
        el.href = bookHref(book);
        el.dataset.status = book.status;
        titleEl.textContent = book.title || 'Untitled';
        author.textContent = book.author || '';
        author.hidden = !book.author;
        cover.setBook(book);
        const info = creationInfo(book);
        cover.setCreating(info);
        if (!info) cover.setFill(stats.progress(book));
        note.replaceChildren();
        note.className = 'shelf-note';
        const off = offline.status(book).state;
        if (book.status === 'error') {
          note.classList.add('warn');
          note.append(icon('alert', 16), h('span', { text: 'Needs attention' }));
        } else if (book.status === 'draft') {
          note.append(icon('pencil', 16), h('span', { text: 'Finish setup' }));
        } else if (off === 'full') {
          note.append(icon('downloaded', 16), h('span', { text: 'Downloaded' }));
        }
        note.hidden = !note.childNodes.length;
        el.setAttribute('aria-label', `${book.title}${book.author ? ', by ' + book.author : ''}. ${info ? statusLine(book).text : stats.progress(book) + '% listened'}`);
      },
      refill(book) {
        if (!creationInfo(book)) cover.setFill(stats.progress(book));
      },
    };
  }

  /** Shelf order: books in progress first, then new ones, finished ones, and books still being made. */
  function rank(b) {
    if (b.status === 'draft' || b.status === 'error') return 5;
    if (b.status !== 'ready') return 4;
    const pct = stats.progress(b);
    if (stats.isFinished(b.id) || pct >= 100) return 3;
    return pct > 0 ? 1 : 2;
  }

  function renderShelf() {
    const lastPlayed = (b) => (stats.merged().books[b.id] || {}).last_played_at || 0;
    const all = visibleBooks()
      .map((b, i) => ({ b, i, r: rank(b), lp: lastPlayed(b) }))
      .sort((x, y) => x.r - y.r || (x.r === 1 ? y.lp - x.lp : 0) || x.i - y.i)
      .map((x) => x.b);
    const shown = all.filter((b) => inFilter(b, filter));
    countEl.textContent = all.length ? String(all.length) : '';
    shelfHead.hidden = !books || (!all.length && !state.offline);
    const seen = new Set();
    const order = [];
    for (const b of shown) {
      seen.add(b.id);
      let it = items.get(b.id);
      if (!it) {
        it = shelfItem(b);
        items.set(b.id, it);
      }
      it.update(b);
      order.push(it.el);
    }
    for (const [id, it] of items) {
      if (!seen.has(id)) {
        it.el.remove();
        if (!all.some((b) => b.id === id)) items.delete(id);
      }
    }
    if (!state.offline && all.length) order.push(addTile);
    if (!shown.length && all.length) order.unshift(h('div', { class: 'shelf-empty', text: filter === 'finished' ? 'Books you finish will appear here.' : filter === 'listening' ? 'Books you have started will appear here.' : 'Nothing is being created right now.' }));
    // keep existing nodes in place (no cover reloads while polling)
    shelf.querySelectorAll('.sk, .shelf-empty').forEach((n) => n.closest('.shelf-item, .shelf-empty')?.remove());
    order.forEach((node, i) => {
      if (shelf.children[i] !== node) shelf.insertBefore(node, shelf.children[i] || null);
    });
    while (shelf.children.length > order.length) shelf.lastElementChild.remove();
    if (firstLoad && order.length) {
      firstLoad = false;
      shelf.classList.add('stagger');
      order.forEach((n, i) => n.style.setProperty('--i', String(Math.min(i, 10))));
      setTimeout(() => shelf.classList.remove('stagger'), 1200);
    }
  }

  function renderEmpty() {
    empty.replaceChildren();
    if (!books || visibleBooks().length) return;
    if (state.offline) {
      empty.append(
        h('div', { class: 'empty' }, h('div', { class: 'empty-art' }, icon('cloudOff', 44)), h('h2', { class: 'empty-title', text: 'No downloaded books' }), h('p', { class: 'empty-text', text: 'Open a book while you are connected and choose Download for offline from its menu.' })),
      );
      return;
    }
    empty.append(
      h(
        'div',
        { class: 'empty' },
        h('div', { class: 'empty-art' }, icon('books', 48)),
        h('h2', { class: 'empty-title', text: 'Your library is empty' }),
        h('p', { class: 'empty-text', text: 'Add the PDF of a book you own, check its chapters and pick a narrator. Your server voices it chapter by chapter, and you can start listening as soon as the first one is ready.' }),
        h('a', { class: 'btn btn-primary btn-lg', href: '#/new' }, icon('plus', 22), h('span', { text: 'Add a book' })),
      ),
    );
  }

  function render() {
    renderShelf();
    renderEmpty();
    renderContinue();
    renderToday();
    today.hidden = !books || (!visibleBooks().length && !stats.hasAnyListening());
  }

  function refills() {
    renderToday();
    renderContinue();
    if (!books) return;
    for (const b of visibleBooks()) items.get(b.id)?.refill(b);
  }

  /* ---------- data ---------- */

  async function load() {
    try {
      const fresh = await listBooks();
      if (!alive) return;
      state.offline = false;
      rememberBooks(fresh);
      books = fresh;
      for (const b of fresh) player.updateBook(b);
    } catch (err) {
      if (!alive) return;
      if (err.network || err.timeout) {
        state.offline = true;
        if (!books) books = snapshotBooks();
      } else if (err.status !== 401) {
        banners.append(callout('error', `Couldn't load your books. ${err.message}`));
        if (!books) books = [];
      } else if (!books) books = [];
    }
    render();
    renderBanners();
    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    if (!alive) return;
    const busy = books && books.some((b) => b.status === 'queued' || b.status === 'rendering');
    if (state.offline) timer = setTimeout(load, 10000);
    else if (busy) timer = setTimeout(load, 4000);
  }

  async function pollStatus() {
    clearTimeout(statusTimer);
    await refreshStatus();
    if (!alive) return;
    const loading = state.status && state.status.engine && state.status.engine.state === 'loading';
    statusTimer = setTimeout(pollStatus, loading ? 4000 : 20000);
  }

  const liveRefill = throttle(refills, 5000);
  const offs = [
    bus.on('status', renderChip),
    bus.on('visible', () => {
      load();
      pollStatus();
    }),
    bus.on('online', () => {
      load();
      pollStatus();
    }),
    player.on('state', renderContinue),
    player.on('track', renderContinue),
    player.on('time', liveRefill),
    stats.events.on('change', refills),
    offline.events.on('change', () => render()),
  ];

  renderChip();
  renderToday();
  load();
  pollStatus();

  return () => {
    alive = false;
    clearTimeout(timer);
    clearTimeout(statusTimer);
    offs.forEach((off) => off());
  };
}
