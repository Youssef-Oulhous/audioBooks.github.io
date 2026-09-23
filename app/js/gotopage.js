// Go to page: start listening at a page of the PDF, whatever state the book is in.
//   book ready     -> stream mode at book_time
//   chapter ready  -> that chapter's file at chapter_time
//   part ready     -> that part at part_time (then the following parts)
//   not voiced yet -> ask the server to voice it next, wait, then start

import { h, store } from './util.js';
import { icon } from './icons.js';
import * as api from './api.js';
import { openSheet, toast, spinner, confirmSheet } from './ui.js';
import { player } from './player.js';
import { rememberBook } from './state.js';

const POLL_MS = 2000;
const NOT_NARRATED = /^(table of )?contents$|^index$|^notes$|^bibliography$|^copyright|^acknowledg/i;

/** The range and wording of page numbers this book uses. */
export function pageRange(book) {
  const printed = book.page_numbering === 'printed';
  const lo = Number.isInteger(book.first_page) ? book.first_page : 1;
  const hi = Number.isInteger(book.last_page) ? book.last_page : book.pages || 9999;
  return {
    printed,
    lo,
    hi: Math.max(lo, hi),
    hint: printed ? `Page number printed in the book (${lo}–${Math.max(lo, hi)}).` : `Page of the PDF (1–${Math.max(lo, hi)}).`,
  };
}

/** The numeric "Go to page" sheet. */
export function openGoToPage(book) {
  const { lo, hi, hint } = pageRange(book);
  const cur = player.book && player.book.id === book.id ? player.currentPage() : null;
  const input = h('input', {
    class: 'input input-page',
    type: 'text',
    inputmode: 'numeric',
    pattern: '[0-9]*',
    autocomplete: 'off',
    enterkeyhint: 'go',
    'aria-label': 'Page number',
    placeholder: cur && /^\d+$/.test(String(cur)) ? String(cur) : String(lo),
    maxlength: '5',
  });
  const err = h('p', { class: 'form-error', hidden: true, 'aria-live': 'polite' });
  input.addEventListener('input', () => (err.hidden = true));
  const go = h('button', { class: 'btn btn-primary btn-block btn-lg', type: 'submit' }, icon('book', 22), h('span', { text: 'Go' }));
  openSheet(
    (close) => {
      const form = h(
        'form',
        { class: 'goto', novalidate: true },
        h('div', { class: 'sheet-title', text: 'Go to page' }),
        h('p', { class: 'sheet-msg', text: hint }),
        input,
        err,
        h('div', { class: 'btn-col' }, go, h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: 'Cancel', onclick: () => close() })),
      );
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const n = Number(String(input.value).trim());
        if (!Number.isInteger(n) || n < lo || n > hi) {
          err.hidden = false;
          err.textContent = `Enter a page between ${lo} and ${hi}.`;
          input.focus();
          return;
        }
        player.unlock(); // inside the tap: lets playback start later without another tap
        close();
        goToPage(book, n);
      });
      return form;
    },
    { label: 'Go to page' },
  );
  setTimeout(() => input.focus(), 350);
}

/** The title of the skipped chapter (contents, notes…) a page falls in, when it can be told. */
function skippedTitleAt(book, page, loc) {
  if (loc.skipped_chapter_title) return loc.skipped_chapter_title;
  const chs = [...(book.chapters || [])].sort((a, b) => a.index - b.index);
  const byPages = chs.find((c) => c.include === false && c.first_page != null && c.first_page <= page && page <= (c.last_page ?? c.first_page));
  if (byPages) return byPages.title;
  // Skipped chapters may have no page range (never planned): use the skipped run next to where we landed.
  const i = chs.findIndex((c) => c.index === loc.chapter);
  if (i < 0) return null;
  const run = [];
  const step = page < loc.resolved_page ? -1 : 1;
  for (let j = i + step; j >= 0 && j < chs.length && chs[j].include === false; j += step) run.push(chs[j]);
  return run.length === 1 ? run[0].title : null;
}

/** How to say where playback starts: the book's printed label when it isn't a plain number. */
function pageText(loc, fallback) {
  if (!loc) return String(fallback);
  const label = loc.page_label && String(loc.page_label).trim();
  const num = loc.resolved_page != null ? String(loc.resolved_page) : null;
  return label && label !== num ? label : num || String(fallback);
}

/** Tell the user when a page isn't narrated and where we start instead. */
function resolvedNote(book, page, loc) {
  if (!loc || loc.resolved_page == null) return;
  const asked = String(page).trim().toLowerCase();
  const got = pageText(loc, loc.resolved_page).trim().toLowerCase();
  if (!got || got === asked) return; // we start exactly where they asked
  const skipped = (skippedTitleAt(book, page, loc) || '').trim();
  let where = 'isn’t narrated';
  if (skipped) where = NOT_NARRATED.test(skipped) ? `is in the ${skipped.toLowerCase()}` : `is in “${skipped}”, which isn’t narrated`;
  else if (Number(page) > Number(loc.resolved_page)) where = 'is after the narration ends';
  toast(`Page ${page} ${where}. Starting at page ${got}.`, { duration: 5000 });
}

/**
 * Where a page label sits, worked out from the book we already have. Used when the server
 * can't take a label (it answers 422), so roman front-matter pages still work.
 */
function locateLocally(book, page) {
  const want = String(page).trim().toLowerCase();
  const same = (p) => String(p).trim().toLowerCase() === want;
  for (const c of (book.chapters || []).filter((x) => x.include !== false).sort((a, b) => a.index - b.index)) {
    const base = { page, resolved_page: page, page_label: String(page), chapter: c.index, chapter_title: c.title, part_title: c.part || null, skipped_chapter_title: null };
    const mark = (c.page_marks || []).find(([p]) => same(p));
    if (mark) {
      return { ...base, resolved_page: mark[0], page_label: String(mark[0]), chapter_ready: true, chapter_time: mark[1], book_time: typeof c.offset === 'number' ? c.offset + mark[1] : null, part: null, part_ready: false, part_time: null };
    }
    for (const pt of c.parts || []) {
      const hit = (pt.marks || []).find(([p]) => same(p));
      if (hit) {
        return { ...base, resolved_page: hit[0], page_label: String(hit[0]), chapter_ready: false, chapter_time: null, book_time: null, part: pt.index, part_ready: !!pt.ready, part_time: hit[1] };
      }
    }
    if (c.first_page_label && same(c.first_page_label)) {
      const p0 = (c.parts || [])[0];
      return { ...base, chapter_ready: c.status === 'ready', chapter_time: 0, book_time: typeof c.offset === 'number' ? c.offset : null, part: p0 ? p0.index : null, part_ready: !!(p0 && p0.ready), part_time: 0 };
    }
  }
  return null;
}

/** /locate, falling back to what the app knows when the server refuses a page label. */
async function locateOr(book, page) {
  try {
    return await api.locate(book.id, page);
  } catch (err) {
    if ((err.status === 422 || err.status === 400) && !/^\d+$/.test(String(page).trim())) {
      const local = locateLocally(book, page);
      if (local) return local;
    }
    throw err;
  }
}

async function freshBook(book) {
  try {
    const b = await api.getBook(book.id);
    rememberBook(b);
    player.updateBook(b);
    return b;
  } catch {
    return book;
  }
}

/**
 * Start playback at the located position if that audio exists. Returns true when started,
 * false when not voiced yet, null when the answer was stale (ask again).
 */
function startAt(book, loc) {
  const ch = book.chapters.find((c) => c.index === loc.chapter);
  if (!ch) return false;
  if (ch.status === 'ready') {
    if (loc.chapter_time == null) return null; // the chapter finished since /locate answered
    return player.load(book, ch.index, { time: loc.chapter_time, autoplay: true }) ? true : null; // stream or chapter file
  }
  if (loc.part_ready && loc.part != null) {
    const pt = ch.parts && ch.parts[loc.part];
    if (!pt || !pt.ready) return null;
    return player.load(book, ch.index, { part: loc.part, partTime: loc.part_time || 0, autoplay: true }) ? true : null;
  }
  return false;
}

/**
 * Has the render planned this book's audio yet? Page ranges come from detection, so the sign is
 * a chapter that is voiced or has parts. Before that, /locate answers 409, so we just wait.
 */
const planned = (b) => (b.chapters || []).some((c) => c.include !== false && (c.status === 'ready' || (c.parts || []).length > 0));

async function tryStart(book, page) {
  if (!planned(book) && book.voice_id) {
    const b = await freshBook(book);
    if (!planned(b)) return { started: false, loc: null, book: b }; // queued: parts not planned yet
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    const b0 = await freshBook(book);
    const loc = await locateOr(b0, page);
    const b = b0;
    const r = startAt(b, loc);
    if (r !== null) return { started: r, loc, book: b };
  }
  return { started: false, loc: null, book };
}

/** Go to `page` of `book`. `quiet` skips the "starting at" note (used for the start page after creating). */
export async function goToPage(book, page, { quiet = false } = {}) {
  if (!book.voice_id || book.status === 'draft') {
    clearPendingStart(book.id);
    toast('Choose a voice and start creating this audiobook first.', { kind: 'error' });
    return false;
  }
  let res;
  try {
    res = await tryStart(book, page);
  } catch (err) {
    if (err.status !== 409) {
      toast(err.message || 'Couldn’t find that page.', { kind: 'error' });
      return false;
    }
    // 409: no voice yet, or the render has only just been queued and hasn't planned its parts
    const b = await freshBook(book);
    if (!b.voice_id || b.status === 'draft') {
      clearPendingStart(book.id);
      toast('Choose a voice and start creating this audiobook first.', { kind: 'error' });
      return false;
    }
    res = { started: false, loc: null, book: b };
  }
  if (!quiet) resolvedNote(res.book, page, res.loc);
  if (res.started) {
    clearPendingStart(book.id);
    return true;
  }
  const b = res.book;
  // ask for it the way the book labels it, so roman front-matter pages resolve exactly
  const target = (res.loc && (res.loc.page_label || res.loc.resolved_page)) || page;
  const asNumber = /^\d+$/.test(String(target)) ? Number(target) : null;
  if (b.status === 'paused' || b.status === 'error') {
    const ok = await confirmSheet({ title: 'Creation is paused', message: `Page ${target} hasn’t been voiced yet. Resume creating the audiobook, starting with that page?`, confirmLabel: 'Resume' });
    if (!ok) return false;
    if (!b.voice_id) {
      location.hash = `#/new/${encodeURIComponent(b.id)}/voice`;
      return false;
    }
    try {
      rememberBook(await api.renderBook(b.id, b.voice_id, b.pace || 1, asNumber));
      if (asNumber == null) await api.prioritize(b.id, target); // a labelled page (e.g. "xii")
    } catch (err) {
      toast(err.message, { kind: 'error' });
      return false;
    }
  } else {
    try {
      await api.prioritize(b.id, target);
    } catch {
      /* the wait below still works; the page just may not be first in line */
    }
  }
  return waitForPage(b, target, { quiet: quiet || !!res.loc });
}

/** "Voicing page N…" with a cancel button; starts playback as soon as the page is voiced. */
export function waitForPage(book, page, { quiet = false } = {}) {
  return new Promise((resolve) => {
    let timer = null;
    let done = false;
    const sheet = openSheet(
      (close) =>
        h(
          'div',
          { class: 'voicing' },
          h('div', { class: 'voicing-art' }, spinner('lg'), icon('wave', 26)),
          h('div', { class: 'sheet-title', text: `Voicing page ${page}…` }),
          h('p', { class: 'sheet-msg', text: 'This usually takes under a minute. Playback starts on its own, and the rest of the book keeps being made after it.' }),
          h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: 'Cancel', onclick: () => close('cancel') }),
        ),
      {
        label: `Voicing page ${page}`,
        className: 'sheet-voicing',
        onClose: (why) => {
          done = true;
          clearTimeout(timer);
          if (why === 'cancel' || why === undefined) {
            clearPendingStart(book.id);
            resolve(false);
          }
        },
      },
    );
    const tick = async () => {
      if (done) return;
      try {
        const res = await tryStart(book, page);
        if (done) return;
        if (res.started) {
          done = true;
          clearPendingStart(book.id);
          sheet.close('started');
          const at = (res.loc && res.loc.resolved_page) || page;
          if (!quiet && at !== page) resolvedNote(res.book, page, res.loc);
          else toast(`Starting at page ${pageText(res.loc, page)}.`, { kind: 'ok' });
          resolve(true);
          return;
        }
      } catch {
        /* keep trying */
      }
      timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
  });
}

/* ---------- start page chosen when creating the book ---------- */

export function setPendingStart(bookId, page) {
  store.set('startpage:' + bookId, page);
}

export function pendingStart(bookId) {
  const p = store.get('startpage:' + bookId, null);
  return Number.isInteger(p) ? p : null;
}

export function clearPendingStart(bookId) {
  store.del('startpage:' + bookId);
}
