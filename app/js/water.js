// The water cover (DESIGN.md §3): a book cover filled with water up to the listened
// percentage. Books that are still being created show a progress ring instead; listening
// progress and creation progress never share a visual.

import { h, store } from './util.js';
import { icon } from './icons.js';
import { coverEl } from './ui.js';

// Tileable crests (200 units wide, repeating every 100) so translateX(-50%) loops seamlessly.
const CREST_A = 'M0 7 Q12.5 1 25 7 T50 7 T75 7 T100 7 T125 7 T150 7 T175 7 T200 7 V16 H0 Z';
const CREST_B = 'M0 8 Q25 2 50 8 T100 8 T150 8 T200 8 V16 H0 Z';
const RING_R = 19;
const RING_C = 2 * Math.PI * RING_R;

const svgNS = 'http://www.w3.org/2000/svg';
function crest(cls, d) {
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('class', `wc-wave ${cls}`);
  svg.setAttribute('viewBox', '0 0 200 16');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(svgNS, 'path');
  p.setAttribute('d', d);
  svg.append(p);
  return svg;
}

function ring() {
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('class', 'wc-ring');
  svg.setAttribute('viewBox', '0 0 48 48');
  svg.setAttribute('aria-hidden', 'true');
  const track = document.createElementNS(svgNS, 'circle');
  const bar = document.createElementNS(svgNS, 'circle');
  for (const c of [track, bar]) {
    c.setAttribute('cx', '24');
    c.setAttribute('cy', '24');
    c.setAttribute('r', String(RING_R));
  }
  track.setAttribute('class', 'wc-ring-track');
  bar.setAttribute('class', 'wc-ring-bar');
  bar.setAttribute('stroke-dasharray', String(RING_C));
  svg.append(track, bar);
  svg.bar = bar;
  return svg;
}

// Waves only animate while the cover is on screen (battery).
const io =
  'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => {
        for (const e of entries) e.target.classList.toggle('offscreen', !e.isIntersecting);
      })
    : null;

function shimmered(id) {
  const seen = store.get('shimmered', []) || [];
  if (seen.includes(id)) return true;
  seen.push(id);
  store.set('shimmered', seen.slice(-200));
  return false;
}

/**
 * waterCover(book, {pct, size, creating})
 *  size: 'xs' (mini player, flat water) | 'sm' | 'md' (shelf) | 'lg' | 'np'
 *  creating: null, or {kind: 'creating'|'queued'|'paused'|'error'|'draft', pct}
 * Returns the element with setFill(pct), setCreating(info) and setBook(book).
 */
export function waterCover(book, { pct = 0, size = 'md', creating = null, rise = true } = {}) {
  const el = h('div', { class: `wc wc-${size}`, role: 'img' });
  let cover = coverEl(book, 'wc-img');
  const water = h('div', { class: 'wc-water', 'aria-hidden': 'true' }, h('div', { class: 'wc-crest' }, crest('w1', CREST_A), crest('w2', CREST_B)));
  const pctEl = h('span', { class: 'wc-pct', 'aria-hidden': 'true' });
  const badge = h('span', { class: 'wc-badge', 'aria-hidden': 'true' }, icon('checkFill', size === 'sm' ? 16 : 22));
  const r = ring();
  const createLabel = h('span', { class: 'wc-create-label' });
  const create = h('div', { class: 'wc-create', hidden: true, 'aria-hidden': 'true' }, r, createLabel);
  const warn = h('span', { class: 'wc-create-icon' });
  el.append(cover, water, pctEl, badge, create);
  let fill = null;
  let title = book.title || 'Book';

  el.setFill = (p) => {
    p = Math.max(0, Math.min(100, Math.round(p || 0)));
    const first = fill == null;
    if (!first && p === fill) return;
    const wasFull = fill === 100;
    fill = p;
    el.style.setProperty('--fill', p + '%');
    el.dataset.pct = String(p);
    pctEl.textContent = p + '%';
    el.classList.toggle('is-empty', p <= 0);
    el.classList.toggle('is-full', p >= 100);
    if (!el.classList.contains('is-creating')) el.setAttribute('aria-label', `${title}, ${p === 100 ? 'finished' : p + '% listened'}`);
    if (p >= 100 && !wasFull && book.id && !shimmered(book.id)) {
      el.classList.add('shimmer');
      setTimeout(() => el.classList.remove('shimmer'), 2600);
    }
  };

  el.setCreating = (info) => {
    const on = !!info;
    el.classList.toggle('is-creating', on);
    create.hidden = !on;
    if (!on) {
      if (fill != null) el.setAttribute('aria-label', `${title}, ${fill === 100 ? 'finished' : fill + '% listened'}`);
      return;
    }
    const p = Math.max(0, Math.min(100, info.pct || 0));
    r.bar.setAttribute('stroke-dashoffset', String(RING_C * (1 - p / 100)));
    const text = {
      creating: `Creating ${Math.floor(p)}%`,
      queued: 'Queued',
      paused: `Paused at ${Math.floor(p)}%`,
      error: 'Needs attention',
      draft: 'Finish setup',
    }[info.kind] || `Creating ${Math.floor(p)}%`;
    createLabel.textContent = text;
    el.dataset.creating = info.kind;
    const iconName = info.kind === 'error' ? 'alert' : info.kind === 'draft' ? 'pencil' : null;
    r.style.display = iconName ? 'none' : '';
    warn.replaceChildren(iconName ? icon(iconName, 26) : '');
    if (iconName && !warn.parentNode) create.prepend(warn);
    if (!iconName) warn.remove();
    el.setAttribute('aria-label', `${title}, ${text}`);
  };

  el.setBook = (b) => {
    book = b;
    title = b.title || title;
    cover.setSrc && cover.setSrc(b);
  };

  el.setCreating(creating);
  if (rise) {
    // Rise from empty to the value on first appearance.
    el.style.setProperty('--fill', '0%');
    el.classList.add('rise', 'is-empty');
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        el.setFill(pct);
        setTimeout(() => el.classList.remove('rise'), 1300);
      }),
    );
  } else {
    el.setFill(pct);
  }
  if (io && size !== 'xs') io.observe(el);
  return el;
}

/** What a cover should show for a book: water, or a creation state. */
export function creationInfo(book) {
  if (!book || book.status === 'ready') return null;
  const pct = (book.progress && book.progress.percent) || 0;
  if (book.status === 'rendering') return { kind: 'creating', pct };
  if (book.status === 'queued') return { kind: 'queued', pct };
  if (book.status === 'paused') return { kind: 'paused', pct };
  if (book.status === 'error') return { kind: 'error', pct };
  return { kind: 'draft', pct: 0 };
}
