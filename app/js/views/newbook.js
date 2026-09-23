// New book flow: 1 Upload -> 2 Chapters -> 3 Voice (+ confirm sheet).
// Routes: #/new, #/new/<id>/chapters, #/new/<id>/voice

import { h, fill, long, words, langName, plural, store } from '../util.js';
import { icon } from '../icons.js';
import * as api from '../api.js';
import { mediaUrl } from '../config.js';
import { state, rememberBook, forgetBook, refreshStatus } from '../state.js';
import { callout, coverEl, progressBar, spinner, toast, openSheet, confirmSheet, topbar, backButton, iconButton } from '../ui.js';
import { preview } from '../preview.js';
import { player } from '../player.js';
import { setPendingStart } from '../gotopage.js';
import { navigate, goBack } from '../router.js';

const STEPS = ['Upload', 'Chapters', 'Voice'];
const PACES = [
  { value: 0.9, label: 'Relaxed' },
  { value: 1.0, label: 'Normal' },
  { value: 1.1, label: 'Brisk' },
];
const EXAMPLES = [
  'A gentle old storyteller with a warm, raspy voice, speaking slowly',
  'A crisp, confident young woman with a bright British accent',
  'A deep, velvety late-night radio host, calm and intimate',
];

export function paceLabel(p) {
  const best = PACES.reduce((a, b) => (Math.abs(b.value - p) < Math.abs(a.value - p) ? b : a), PACES[1]);
  return best.label;
}

export function mount(root, params) {
  if (params.step === 'chapters') return mountChapters(root, params.id);
  if (params.step === 'voice') return mountVoice(root, params.id);
  return mountUpload(root);
}

function stepper(current) {
  return h(
    'ol',
    { class: 'stepper', 'aria-label': `Step ${current + 1} of 3: ${STEPS[current]}` },
    STEPS.map((label, i) =>
      h(
        'li',
        { class: i < current ? 'done' : i === current ? 'current' : '', 'aria-current': i === current ? 'step' : null },
        h('span', { class: 'step-dot' }, i < current ? icon('check', 14) : String(i + 1)),
        h('span', { class: 'step-label', text: label }),
      ),
    ),
  );
}

function flowHeader(current, onBack, backLabel) {
  return h(
    'div',
    { class: 'flow-head' },
    topbar({
      left: backButton(backLabel, onBack),
      title: h('span', { text: 'New audiobook' }),
      right: current < 2 ? iconButton('close', 'Close', () => navigate('#/library')) : null,
    }),
    stepper(current),
  );
}

async function loadBook(id) {
  const cached = state.books.get(id);
  try {
    const b = await api.getBook(id);
    rememberBook(b);
    return b;
  } catch (err) {
    if (cached && (err.network || err.timeout)) return cached;
    throw err;
  }
}

function loadingPage(text) {
  return h('div', { class: 'loading-row' }, spinner(), h('span', { text }));
}

function errorPage(err, retry) {
  return h(
    'div',
    { class: 'error-block' },
    callout('error', err.message || 'Something went wrong.'),
    h('div', { class: 'btn-row' }, h('button', { class: 'btn btn-secondary', type: 'button', text: 'Try again', onclick: retry }), h('a', { class: 'btn btn-ghost', href: '#/library', text: 'Back to library' })),
  );
}

/* ================= 1. Upload ================= */

function mountUpload(root) {
  let alive = true;
  let job = null;
  const input = h('input', { type: 'file', accept: 'application/pdf,.pdf', class: 'visually-hidden', tabindex: '-1', 'aria-hidden': 'true' });
  const body = h('div', { class: 'upload-body' });
  const page = h('div', { class: 'page page-flow' }, h('h1', { class: 'flow-title', text: 'Add a book' }), h('p', { class: 'flow-lead', text: 'Choose a PDF of the book. We’ll find its chapters so you can check them before anything is voiced.' }), body, input);
  root.append(flowHeader(0, () => goBack('#/library'), 'Library'), page);

  input.addEventListener('change', () => {
    const f = input.files && input.files[0];
    input.value = '';
    if (f) start(f);
  });
  const pick = () => input.click();

  function idle(errorMsg) {
    fill(body, 
      errorMsg ? callout('error', h('span', {}, h('strong', { text: 'That didn’t work. ' }), errorMsg)) : null,
      h(
        'button',
        { class: 'drop', type: 'button', onclick: pick, 'aria-label': 'Choose a PDF file' },
        h('span', { class: 'drop-art' }, icon('file', 34)),
        h('span', { class: 'drop-title', text: errorMsg ? 'Choose another PDF' : 'Choose a PDF' }),
        h('span', { class: 'drop-text', text: 'From Files, iCloud Drive or your downloads' }),
      ),
      h(
        'ul',
        { class: 'tips' },
        h('li', {}, icon('check', 18), h('span', { text: 'Books with selectable text work best (most e-book PDFs).' })),
        h('li', {}, icon('check', 18), h('span', { text: 'English and Chinese are supported.' })),
        h('li', {}, icon('alert', 18), h('span', { text: 'Scanned books (photos of pages) can’t be read. Run them through OCR first.' })),
      ),
    );
  }

  function start(file) {
    if (!/\.pdf$/i.test(file.name || '') && file.type !== 'application/pdf') {
      idle('Please choose a PDF file.');
      return;
    }
    const bar = progressBar(0);
    const pctEl = h('span', { class: 'up-pct', text: '0%' });
    const phase = h('div', { class: 'up-phase', text: 'Uploading…' });
    const cancel = h('button', { class: 'btn btn-ghost btn-sm', type: 'button', text: 'Cancel', onclick: () => job && job.abort() });
    fill(body, 
      h(
        'div',
        { class: 'card upload-card' },
        h('div', { class: 'up-file' }, h('span', { class: 'up-icon' }, icon('file', 26)), h('div', { class: 'up-name' }, h('div', { class: 'up-fname', text: file.name }), h('div', { class: 'up-size', text: sizeText(file.size) })), pctEl),
        bar,
        phase,
        h('div', { class: 'up-actions' }, cancel),
      ),
    );
    job = api.uploadBook(file, {
      onProgress: (f) => {
        bar.set(f * 100);
        pctEl.textContent = Math.round(f * 100) + '%';
      },
      onUploaded: () => {
        if (!alive) return;
        bar.set(100);
        pctEl.replaceChildren(spinner('sm'));
        phase.replaceChildren(h('strong', { text: 'Reading your book and finding chapters…' }), h('div', { class: 'muted', text: 'Big books can take a minute.' }));
        cancel.hidden = true;
      },
    });
    job.then(
      (book) => {
        if (!alive) return;
        rememberBook(book);
        navigate(`#/new/${encodeURIComponent(book.id)}/chapters`, { replace: true });
      },
      (err) => {
        if (!alive) return;
        if (err.message === 'Upload cancelled.') idle();
        else idle(err.message);
      },
    );
  }

  idle();
  return () => {
    alive = false;
  };
}

function sizeText(n) {
  if (!n) return '';
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`;
}

/* ================= 2. Chapters ================= */

function mountChapters(root, id) {
  let alive = true;
  const page = h('div', { class: 'page page-flow' });
  root.append(flowHeader(1, () => goBack('#/library'), 'Library'), page);
  page.append(loadingPage('Loading chapters…'));

  let book = null;
  const edits = new Map(); // index -> {title, include}
  let titleInput;
  let authorInput;
  let totalsText;
  let continueBtn;
  const rowEls = new Map(); // chapter index -> row (only the ones that have been built)
  const groups = []; // part groups, in order

  async function init() {
    page.replaceChildren(loadingPage('Loading chapters…'));
    try {
      book = await loadBook(id);
    } catch (err) {
      if (alive) page.replaceChildren(errorPage(err, init));
      return;
    }
    if (!alive) return;
    if (book.status === 'queued' || book.status === 'rendering') {
      toast('This book is being voiced. Pause it first to change chapters.');
      navigate(`#/book/${encodeURIComponent(id)}`, { replace: true });
      return;
    }
    for (const c of book.chapters) edits.set(c.index, { title: c.title, include: c.include !== false });
    build();
  }

  function build() {
    titleInput = h('input', { class: 'input input-title', type: 'text', value: book.title || '', 'aria-label': 'Book title', autocomplete: 'off', enterkeyhint: 'done', placeholder: 'Title' });
    authorInput = h('input', { class: 'input input-author', type: 'text', value: book.author || '', 'aria-label': 'Author', autocomplete: 'off', enterkeyhint: 'done', placeholder: 'Author' });
    for (const inp of [titleInput, authorInput]) inp.addEventListener('keydown', (e) => e.key === 'Enter' && inp.blur());

    const metaBits = [book.pages ? plural(book.pages, 'page') : null, book.total_words ? `${words(book.total_words)} words` : null, langName(book.language)].filter(Boolean);
    totalsText = h('div', { class: 'totals-text' });
    const allBtn = h('button', { class: 'chip-btn', type: 'button', text: 'Select all', onclick: () => setAll(true) });
    const noneBtn = h('button', { class: 'chip-btn', type: 'button', text: 'None', onclick: () => setAll(false) });
    continueBtn = h('button', { class: 'btn btn-primary btn-block btn-lg', type: 'button', onclick: save }, h('span', { text: 'Choose a voice' }), icon('chevronRight', 20));

    const list = h('div', { class: 'sch-list' });
    const sorted = [...book.chapters].sort((a, b) => a.index - b.index);
    const hasParts = Array.isArray(book.parts) && book.parts.length > 0;
    rowEls.clear();
    groups.length = 0;
    if (!hasParts) {
      for (const c of sorted) list.append(addRow(c));
    } else {
      // Chapter numbers restart inside each part of a long book, so the parts are what makes
      // the list readable. Collapsed parts build no rows at all, which keeps 355 chapters instant.
      const nParts = new Set(sorted.filter((c) => c.part).map((c) => c.part)).size;
      let current = null;
      for (const c of sorted) {
        const part = c.part || null;
        if (!part) {
          current = null;
          list.append(addRow(c));
          continue;
        }
        if (!current || current.title !== part) {
          current = partGroup(part, nParts <= 3);
          groups.push(current);
          list.append(current.el);
        }
        current.chapters.push(c);
      }
      for (const g of groups) g.refresh();
    }

    fill(page, 
      h(
        'div',
        { class: 'setup-head' },
        coverEl(book, 'cover-setup'),
        h('div', { class: 'setup-fields' }, h('label', { class: 'field-label', text: 'Title' }), titleInput, h('label', { class: 'field-label', text: 'Author' }), authorInput),
      ),
      h('div', { class: 'setup-meta', text: metaBits.join(' · ') }),
      book.language_warning ? callout('warn', book.language_warning) : null,
      book.detection && book.detection.note ? callout('info', book.detection.note) : null,
      h('div', { class: 'totals' }, totalsText, h('div', { class: 'totals-actions' }, allBtn, noneBtn)),
      list,
      h('div', { class: 'discard' }, h('button', { class: 'link-btn danger', type: 'button', onclick: discard }, icon('trash', 18), h('span', { text: 'Discard this book' }))),
      h('div', { class: 'flow-footer' }, continueBtn),
    );
    updateTotals();
    // park the part headers right under the flow header and the sticky totals bar
    const headEl = root.querySelector('.flow-head');
    const totalsEl = page.querySelector('.totals');
    if (headEl) {
      const top = headEl.getBoundingClientRect().height + (totalsEl ? totalsEl.getBoundingClientRect().height : 0);
      page.style.setProperty('--flow-head-h', `${Math.round(top)}px`);
    }
  }

  function addRow(c) {
    const r = chapterRow(c);
    rowEls.set(c.index, r);
    return r.el;
  }

  /** "pages 19–43", "page 19", or the printed label of a front-matter page. */
  function pagesText(c) {
    const first = c.first_page;
    const last = c.last_page;
    if (first == null && last == null) return c.first_page_label ? `page ${c.first_page_label}` : '';
    if (first != null && last != null && last > first) return `pages ${first}–${last}`;
    const one = first != null ? first : last;
    return one != null ? `page ${one}` : '';
  }

  function partsOpen() {
    return store.get('parts:' + id, {}) || {};
  }

  function partGroup(title, openByDefault) {
    const saved = partsOpen();
    let open = title in saved ? !!saved[title] : openByDefault;
    let built = false;
    const chapters = [];
    const meta = h('span', { class: 'sch-part-meta' });
    const chev = h('span', { class: 'part-chev' }, icon('chevronDown', 20));
    const head = h('button', { class: 'sch-part-head', type: 'button' }, chev, h('span', { class: 'part-main' }, h('span', { class: 'part-name', text: title }), meta));
    const sw = h('input', { type: 'checkbox', class: 'switch', role: 'switch', 'aria-label': `Include every chapter of ${title}` });
    const body = h('div', { class: 'sch-part-body' });
    const el = h('section', { class: 'sch-part' }, h('div', { class: 'sch-part-top' }, head, h('label', { class: 'switch-wrap' }, sw)), body);

    function ensureBuilt() {
      if (built || !open) return;
      built = true;
      body.append(...chapters.map(addRow));
    }
    function paint() {
      head.setAttribute('aria-expanded', String(open));
      el.classList.toggle('closed', !open);
      body.hidden = !open;
      ensureBuilt();
    }
    head.addEventListener('click', () => {
      open = !open;
      store.set('parts:' + id, { ...partsOpen(), [title]: open });
      paint();
    });
    sw.addEventListener('change', () => {
      for (const c of chapters) edits.get(c.index).include = sw.checked;
      for (const c of chapters) {
        const r = rowEls.get(c.index);
        if (r) r.sync();
      }
      updateTotals();
    });
    const g = {
      el,
      title,
      chapters,
      refresh() {
        const on = chapters.filter((c) => edits.get(c.index).include).length;
        sw.checked = on > 0;
        sw.indeterminate = on > 0 && on < chapters.length;
        el.classList.toggle('off', on === 0);
        const pages = pagesText({ first_page: chapters[0].first_page, last_page: [...chapters].reverse().find((c) => c.last_page != null)?.last_page, first_page_label: chapters[0].first_page_label });
        const secs = chapters.reduce((n, c) => n + (edits.get(c.index).include ? c.est_seconds || 0 : 0), 0);
        meta.textContent = [pages, `${on} of ${chapters.length} chapters`, secs ? `~${long(secs)}` : null].filter(Boolean).join(' · ');
        head.setAttribute('aria-label', `${title}, ${meta.textContent}. ${open ? 'Hide' : 'Show'} its chapters`);
        paint();
      },
    };
    return g;
  }

  function chapterRow(c) {
    const e = edits.get(c.index);
    const num = h('div', { class: 'sch-num', text: String(c.index + 1) });
    const titleBtn = h('button', { class: 'sch-title', type: 'button', 'aria-label': `Rename chapter: ${e.title}` }, h('span', { class: 'sch-title-text', text: e.title }), icon('pencil', 15));
    const meta = h('div', { class: 'sch-meta', text: [pagesText(c), `${words(c.words)} words`, `~${long(c.est_seconds)}`].filter(Boolean).join(' · ') });
    const prev = c.preview ? h('p', { class: 'sch-preview', text: c.preview }) : null;
    const sw = h('input', { type: 'checkbox', class: 'switch', role: 'switch', checked: e.include, 'aria-label': `Include ${e.title}` });
    const titleSlot = h('div', { class: 'sch-title-slot' }, titleBtn);
    const el = h('div', { class: `sch${e.include ? '' : ' off'}` }, num, h('div', { class: 'sch-body' }, titleSlot, meta, prev), h('label', { class: 'switch-wrap' }, sw));

    sw.addEventListener('change', () => {
      e.include = sw.checked;
      el.classList.toggle('off', !e.include);
      updateTotals();
    });
    titleBtn.addEventListener('click', () => {
      const inp = h('input', { class: 'input input-inline', type: 'text', value: e.title, 'aria-label': 'Chapter title', enterkeyhint: 'done' });
      let done = false;
      const finish = (commit) => {
        if (done) return;
        done = true;
        const v = inp.value.trim();
        if (commit && v) e.title = v;
        titleBtn.querySelector('.sch-title-text').textContent = e.title;
        titleBtn.setAttribute('aria-label', `Rename chapter: ${e.title}`);
        sw.setAttribute('aria-label', `Include ${e.title}`);
        titleSlot.replaceChildren(titleBtn);
      };
      inp.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          finish(true);
        } else if (ev.key === 'Escape') finish(false);
      });
      inp.addEventListener('blur', () => finish(true));
      titleSlot.replaceChildren(inp);
      inp.focus();
      inp.select();
    });
    return {
      el,
      sync() {
        sw.checked = e.include;
        el.classList.toggle('off', !e.include);
      },
    };
  }

  function setAll(on) {
    for (const e of edits.values()) e.include = on;
    for (const r of rowEls.values()) r.sync();
    updateTotals();
  }

  function updateTotals() {
    const total = book.chapters.length;
    let n = 0;
    let secs = 0;
    for (const c of book.chapters) {
      if (edits.get(c.index).include) {
        n++;
        secs += c.est_seconds || 0;
      }
    }
    totalsText.replaceChildren(h('strong', { text: `${n} of ${total} chapters` }), h('span', { text: `~${long(secs)} of audio` }));
    for (const g of groups) g.refresh();
    continueBtn.disabled = n === 0;
  }

  async function save() {
    const body = {};
    const t = titleInput.value.trim();
    const a = authorInput.value.trim();
    if (t && t !== book.title) body.title = t;
    if (a !== (book.author || '')) body.author = a;
    const changed = [];
    for (const c of book.chapters) {
      const e = edits.get(c.index);
      if (e.include !== (c.include !== false) || e.title !== c.title) changed.push({ index: c.index, include: e.include, title: e.title });
    }
    if (changed.length) body.chapters = changed;
    continueBtn.disabled = true;
    const label = continueBtn.firstElementChild;
    const oldText = label.textContent;
    label.textContent = 'Saving…';
    try {
      if (Object.keys(body).length) {
        book = await api.patchBook(id, body);
        rememberBook(book);
      }
      if (!alive) return;
      navigate(`#/new/${encodeURIComponent(id)}/voice`);
    } catch (err) {
      toast(err.message, { kind: 'error' });
      label.textContent = oldText;
      continueBtn.disabled = false;
    }
  }

  async function discard() {
    const ok = await confirmSheet({ title: 'Discard this book?', message: `“${book.title}” and its chapters will be removed from your server.`, confirmLabel: 'Discard', danger: true });
    if (!ok) return;
    try {
      await api.deleteBook(id);
      forgetBook(id);
      navigate('#/library', { replace: true });
    } catch (err) {
      toast(err.message, { kind: 'error' });
    }
  }

  init();
  return () => {
    alive = false;
  };
}

/* ================= 3. Voice ================= */

function mountVoice(root, id) {
  let alive = true;
  const page = h('div', { class: 'page page-flow' });
  root.append(flowHeader(2, () => goBack(`#/new/${encodeURIComponent(id)}/chapters`), 'Chapters'), page);

  let book = null;
  let voices = [];
  let selected = null;
  let pace = 1.0;
  let showAll = false;
  let wantPlay = null; // the voice whose sample the user asked to hear last
  const busy = new Set();
  const cards = new Map();
  let grid;
  let footerBtn;
  let showAllSwitch;
  let filterNote;

  async function init() {
    page.replaceChildren(loadingPage('Loading voices…'));
    try {
      const [b, v] = await Promise.all([loadBook(id), api.listVoices(), refreshStatus()]);
      book = b;
      voices = v;
    } catch (err) {
      if (alive) page.replaceChildren(errorPage(err, init));
      return;
    }
    if (!alive) return;
    selected = book.voice_id && voices.some((v) => v.id === book.voice_id) ? book.voice_id : null;
    pace = PACES.reduce((a, p) => (Math.abs(p.value - (book.pace || 1)) < Math.abs(a - (book.pace || 1)) ? p.value : a), 1.0);
    if (!voices.some((v) => v.language === book.language)) showAll = true;
    build();
  }

  function build() {
    grid = h('div', { class: 'voice-list', role: 'radiogroup', 'aria-label': 'Narrator voice' });
    showAllSwitch = h('input', { type: 'checkbox', class: 'switch', role: 'switch', checked: showAll, 'aria-label': 'Show all voices' });
    showAllSwitch.addEventListener('change', () => {
      showAll = showAllSwitch.checked;
      renderCards();
    });
    filterNote = h('span', { class: 'filter-note' });
    footerBtn = h('button', { class: 'btn btn-primary btn-block btn-lg', type: 'button', onclick: openConfirm });
    const s = state.status;

    const paceSeg = h(
      'div',
      { class: 'seg', role: 'radiogroup', 'aria-label': 'Reading pace' },
      PACES.map((p) => {
        const b = h('button', { type: 'button', role: 'radio', class: 'seg-btn', 'aria-checked': String(p.value === pace) }, h('span', { class: 'seg-main', text: p.label }), h('span', { class: 'seg-sub', text: p.value.toFixed(1) + '×' }));
        b.addEventListener('click', () => {
          pace = p.value;
          paceSeg.querySelectorAll('.seg-btn').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
          updateFooter();
        });
        return b;
      }),
    );

    fill(page, 
      h('h1', { class: 'flow-title', text: 'Choose a narrator' }),
      h('p', { class: 'flow-lead', text: s && s.engine && s.engine.voice_mode === 'preset' ? 'Tap play to hear a sample. Each sample takes a few seconds to create.' : 'Tap play to hear a sample. The first sample of a voice is created on your server and can take a minute.' }),
      s && s.engine && s.engine.is_demo ? callout('demo', h('span', {}, h('strong', { text: 'Demo engine:' }), ' samples are placeholder tones, not real AuK speech.')) : null,
      h('div', { class: 'filter-row' }, h('div', { class: 'filter-text' }, h('span', { class: 'filter-title', text: 'Show all voices' }), filterNote), h('label', { class: 'switch-wrap' }, showAllSwitch)),
      grid,
      h('h2', { class: 'section-title spaced', text: 'Reading pace' }),
      paceSeg,
      h('div', { class: 'flow-footer' }, footerBtn),
    );
    renderCards();
    updateFooter();
  }

  function visibleVoices() {
    if (showAll) return voices;
    return voices.filter((v) => v.language === book.language || v.id === selected);
  }

  function renderCards() {
    const vis = visibleVoices();
    filterNote.textContent = showAll ? `${voices.length} voices in all languages` : `Showing ${langName(book.language)} voices`;
    grid.replaceChildren();
    cards.clear();
    for (const v of vis) {
      const c = voiceCard(v);
      cards.set(v.id, c);
      grid.append(c.el);
    }
    // Kokoro (via Pipecat) has fixed speakers; describing a new voice needs AuK
    if (!(state.status && state.status.engine && state.status.engine.custom_voices === false)) grid.append(designCard());
  }

  function updateCard(vid) {
    const c = cards.get(vid);
    const v = voices.find((x) => x.id === vid);
    if (c && v) c.update(v);
  }

  function voiceCard(v) {
    const playBtn = h('button', { class: 'vc-play', type: 'button' });
    const retake = h('button', { class: 'link-btn vc-retake', type: 'button' }, icon('refresh', 16), h('span', { text: 'Another take' }));
    const del = v.custom ? h('button', { class: 'icon-btn vc-del', type: 'button', 'aria-label': `Delete voice ${v.name}` }, icon('trash', 20)) : null;
    const status = h('div', { class: 'vc-status' });
    const radio = h('span', { class: 'vc-radio', 'aria-hidden': 'true' }, icon('check', 16));
    const noSample = h('span', { class: 'chip chip-ghost', text: 'No sample yet' });
    const chips = h('div', { class: 'vc-chips' }, h('span', { class: 'chip', text: cap(v.gender) }), h('span', { class: 'chip', text: v.accent || langName(v.language) }), v.custom ? h('span', { class: 'chip chip-accent', text: 'Custom' }) : null, noSample);
    const main = h('button', { class: 'vc-main', type: 'button', role: 'radio' }, h('div', { class: 'vc-name', text: v.name }), h('div', { class: 'vc-tag', text: v.tagline || v.description || '' }), chips);
    const el = h('div', { class: 'vc' }, playBtn, main, radio, h('div', { class: 'vc-foot' }, status, retake, del));

    main.addEventListener('click', () => select(v.id));
    radio.addEventListener('click', () => select(v.id));
    playBtn.addEventListener('click', () => onPlay(v.id));
    retake.addEventListener('click', () => {
      preview.unlock();
      wantPlay = v.id;
      generate(v.id, true);
    });
    if (del) del.addEventListener('click', () => removeVoice(v.id));

    const c = {
      el,
      update(vv) {
        const isBusy = busy.has(vv.id);
        const playing = preview.isPlaying(vv.id);
        const isSel = selected === vv.id;
        el.classList.toggle('selected', isSel);
        main.setAttribute('aria-checked', String(isSel));
        playBtn.replaceChildren(isBusy ? spinner('sm') : icon(playing ? 'stop' : 'play', 22));
        playBtn.disabled = isBusy;
        playBtn.setAttribute('aria-label', isBusy ? `Creating a sample of ${vv.name}` : playing ? `Stop sample of ${vv.name}` : `Play sample of ${vv.name}`);
        playBtn.classList.toggle('playing', playing);
        retake.hidden = vv.retakable === false || !vv.preview_url || isBusy || !(isSel || playing);
        noSample.hidden = !!vv.preview_url || isBusy;
        status.textContent = isBusy ? 'Creating sample…' : playing ? 'Playing sample' : '';
        status.classList.toggle('busy', isBusy);
        el.classList.toggle('has-foot', isBusy || playing || !retake.hidden || !!del);
      },
    };
    c.update(v);
    return c;
  }

  function designCard() {
    return h(
      'button',
      { class: 'vc vc-design', type: 'button', onclick: openDesigner },
      h('span', { class: 'vc-play ghost' }, icon('sparkle', 22)),
      h('div', { class: 'vc-main as-div' }, h('div', { class: 'vc-name', text: 'Design your own voice' }), h('div', { class: 'vc-tag', text: 'Describe a narrator in your own words and AuK will create it.' })),
      h('span', { class: 'vc-chev' }, icon('plus', 20)),
    );
  }

  function select(vid) {
    selected = vid;
    for (const [id2] of cards) updateCard(id2);
    updateFooter();
  }

  async function onPlay(vid) {
    const v = voices.find((x) => x.id === vid);
    if (!v) return;
    if (preview.isPlaying(vid)) {
      preview.stop();
      wantPlay = null;
      return;
    }
    if (!selected) select(vid);
    wantPlay = vid;
    if (v.preview_url) {
      preview.play(v.id, mediaUrl(v.preview_url));
      return;
    }
    preview.unlock();
    await generate(vid, false);
  }

  async function generate(vid, retake) {
    if (busy.has(vid)) return;
    busy.add(vid);
    if (preview.isPlaying(vid)) preview.stop();
    updateCard(vid);
    try {
      const nv = await api.previewVoice(vid, retake);
      voices = voices.map((x) => (x.id === vid ? nv : x));
      busy.delete(vid);
      if (!alive) return;
      updateCard(vid);
      if (nv.preview_url && wantPlay === vid) {
        wantPlay = null;
        preview.play(vid, mediaUrl(nv.preview_url));
      }
    } catch (err) {
      busy.delete(vid);
      if (!alive) return;
      updateCard(vid);
      toast(err.timeout ? 'Creating the sample took too long. The server may still be loading the voice model. Try again in a minute.' : err.message, { kind: 'error', duration: 6000 });
    }
  }

  async function removeVoice(vid) {
    const v = voices.find((x) => x.id === vid);
    if (!v) return;
    const ok = await confirmSheet({ title: `Delete “${v.name}”?`, message: 'This custom voice will be removed. Books already narrated with it keep their audio.', confirmLabel: 'Delete voice', danger: true });
    if (!ok) return;
    try {
      await api.deleteVoice(vid);
      if (preview.isPlaying(vid)) preview.stop();
      voices = voices.filter((x) => x.id !== vid);
      if (selected === vid) selected = null;
      renderCards();
      updateFooter();
    } catch (err) {
      toast(err.message, { kind: 'error' });
    }
  }

  function openDesigner() {
    let gender = 'female';
    let language = ['en', 'zh'].includes(book.language) ? book.language : 'en';
    const name = h('input', { class: 'input', type: 'text', placeholder: 'e.g. Grandpa Joe', maxlength: '40', autocomplete: 'off', enterkeyhint: 'next' });
    const desc = h('textarea', { class: 'input textarea', rows: '4', placeholder: 'Describe the voice: age, warmth, accent, pace, mood…', maxlength: '400' });
    const seg = (opts, get, set) => {
      const wrap = h('div', { class: 'seg seg-sm', role: 'radiogroup' });
      for (const [val, label] of opts) {
        const b = h('button', { type: 'button', role: 'radio', class: 'seg-btn', 'aria-checked': String(get() === val) }, h('span', { class: 'seg-main', text: label }));
        b.addEventListener('click', () => {
          set(val);
          wrap.querySelectorAll('.seg-btn').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
        });
        wrap.append(b);
      }
      return wrap;
    };
    const err = h('div', { class: 'form-error', hidden: true });
    const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, icon('sparkle', 20), h('span', { text: 'Create voice' }));
    const examples = h(
      'div',
      { class: 'examples' },
      EXAMPLES.map((ex) =>
        h('button', {
          class: 'example-chip',
          type: 'button',
          text: ex,
          onclick: () => {
            desc.value = ex;
            desc.focus();
          },
        }),
      ),
    );
    const sheet = openSheet(
      (close) => {
        const form = h(
          'form',
          { class: 'designer', novalidate: true },
          h('div', { class: 'sheet-title', text: 'Design your own voice' }),
          h('p', { class: 'sheet-msg', text: 'AuK creates a narrator from your description. You’ll hear a sample next.' }),
          h('label', { class: 'field' }, h('span', { class: 'field-label', text: 'Name' }), name),
          h('div', { class: 'field-row' }, h('div', { class: 'field' }, h('span', { class: 'field-label', text: 'Voice' }), seg([['female', 'Female'], ['male', 'Male']], () => gender, (v) => (gender = v))), h('div', { class: 'field' }, h('span', { class: 'field-label', text: 'Language' }), seg([['en', 'English'], ['zh', '中文']], () => language, (v) => (language = v)))),
          h('label', { class: 'field' }, h('span', { class: 'field-label', text: 'Description' }), desc),
          h('div', { class: 'field-label', text: 'Try an example' }),
          examples,
          err,
          h('div', { class: 'btn-col' }, submit, h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: 'Cancel', onclick: () => close() })),
        );
        form.addEventListener('submit', async (e) => {
          e.preventDefault();
          const n = name.value.trim();
          const d = desc.value.trim();
          if (!n || d.length < 8) {
            err.hidden = false;
            err.textContent = !n ? 'Give the voice a name.' : 'Describe the voice in a few more words.';
            return;
          }
          err.hidden = true;
          submit.disabled = true;
          submit.lastElementChild.textContent = 'Creating…';
          preview.unlock();
          try {
            const v = await api.createVoice({ name: n, description: d, language, gender });
            voices = [v, ...voices.filter((x) => x.id !== v.id)];
            selected = v.id;
            if (v.language !== book.language && !showAll) {
              showAll = true;
              if (showAllSwitch) showAllSwitch.checked = true;
            }
            close();
            if (!alive) return;
            renderCards();
            updateFooter();
            wantPlay = v.id;
            generate(v.id, false);
          } catch (ex) {
            err.hidden = false;
            err.textContent = ex.message;
            submit.disabled = false;
            submit.lastElementChild.textContent = 'Create voice';
          }
        });
        return form;
      },
      { label: 'Design your own voice', className: 'sheet-form' },
    );
    return sheet;
  }

  function updateFooter() {
    const v = voices.find((x) => x.id === selected);
    fill(footerBtn, h('span', { text: v ? `Continue with ${v.name}` : 'Pick a voice to continue' }), v ? icon('chevronRight', 20) : null);
    footerBtn.disabled = !v;
  }

  function openConfirm() {
    const v = voices.find((x) => x.id === selected);
    if (!v) return;
    preview.stop();
    const inc = book.chapters.filter((c) => c.include !== false);
    const est = (book.est_seconds || inc.reduce((n, c) => n + (c.est_seconds || 0), 0)) / pace;
    const rf = state.status && state.status.engine && state.status.engine.realtime_factor;
    const create = h('button', { class: 'btn btn-primary btn-block btn-lg', type: 'button' }, icon('headphones', 22), h('span', { text: 'Create audiobook' }));
    const maxPage = book.pages || 0;
    const startInput = h('input', { class: 'input input-page-sm', type: 'text', inputmode: 'numeric', pattern: '[0-9]*', autocomplete: 'off', id: 'start-page', placeholder: 'For example, 12', maxlength: '5', enterkeyhint: 'done' });
    const startErr = h('p', { class: 'form-error', hidden: true, 'aria-live': 'polite' });
    startInput.addEventListener('input', () => (startErr.hidden = true));
    const startBox = h(
      'div',
      { class: 'start-box', hidden: true },
      h('label', { class: 'field-label', for: 'start-page', text: 'Start at page' }),
      startInput,
      h('p', { class: 'field-help', text: `Page numbers of your PDF${maxPage ? `, 1 to ${maxPage}` : ''}. That page is voiced first, then the rest of the book.` }),
      startErr,
    );
    const startToggle = h('button', { class: 'link-btn start-toggle', type: 'button', 'aria-expanded': 'false' }, icon('book', 18), h('span', { text: 'Start somewhere else?' }));
    startToggle.addEventListener('click', () => {
      const open = startBox.hidden;
      startBox.hidden = !open;
      startToggle.setAttribute('aria-expanded', String(open));
      if (open) setTimeout(() => startInput.focus(), 50);
    });
    const s = openSheet(
      (close) => {
        create.addEventListener('click', async () => {
          let startPage = null;
          const raw = String(startInput.value || '').trim();
          if (!startBox.hidden && raw) {
            const n = Number(raw);
            if (!Number.isInteger(n) || n < 1 || (maxPage && n > maxPage)) {
              startErr.hidden = false;
              startErr.textContent = `Enter a page between 1 and ${maxPage || 'the last page'}.`;
              return;
            }
            startPage = n;
          }
          player.unlock(); // inside the tap, so playback can start on its own when the page is voiced
          create.disabled = true;
          create.lastElementChild.textContent = 'Starting…';
          try {
            const b = await api.renderBook(id, v.id, pace, startPage);
            rememberBook(b);
            if (startPage) setPendingStart(id, startPage);
            close();
            navigate(`#/book/${encodeURIComponent(id)}`, { replace: true });
          } catch (err) {
            toast(err.message, { kind: 'error' });
            create.disabled = false;
            create.lastElementChild.textContent = 'Create audiobook';
          }
        });
        return h(
          'div',
          { class: 'confirm-sheet' },
          h('div', { class: 'confirm-head' }, coverEl(book, 'cover-sm2'), h('div', {}, h('div', { class: 'confirm-kicker', text: 'Ready to create' }), h('div', { class: 'confirm-title', text: book.title }), book.author ? h('div', { class: 'confirm-author', text: book.author }) : null)),
          h(
            'dl',
            { class: 'summary' },
            sumRow('book', 'Chapters', `${inc.length} of ${book.chapters.length}`),
            sumRow('headphones', 'Length', `~${long(est)} of audio`),
            sumRow('wave', 'Voice', `${v.name} · ${paceLabel(pace)} pace`),
            sumRow('play', 'Start listening', 'in about a minute'),
            rf ? sumRow('speed', 'Whole book ready', `in about ${long(est / rf)}`) : null,
          ),
          h('p', { class: 'confirm-note', text: 'You can start listening in about a minute, while the rest keeps being made on your server. Close the app whenever you like.' }),
          maxPage ? startToggle : null,
          maxPage ? startBox : null,
          h('div', { class: 'btn-col' }, create, h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: 'Not yet', onclick: () => close() })),
        );
      },
      { label: 'Confirm audiobook' },
    );
    return s;
  }

  const offs = [
    preview.on('change', () => {
      for (const [vid] of cards) updateCard(vid);
    }),
    preview.on('blocked', () => toast('Sample ready. Tap play to listen.')),
    preview.on('error', (m) => toast(m, { kind: 'error' })),
  ];

  init();
  return () => {
    alive = false;
    preview.stop();
    offs.forEach((o) => o());
  };
}

function sumRow(ic, label, value) {
  return h('div', { class: 'sum-row' }, h('dt', {}, icon(ic, 18), h('span', { text: label })), h('dd', { text: value }));
}

function cap(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}
