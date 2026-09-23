// Reusable UI pieces: toasts, bottom sheets, confirm dialogs, covers, callouts.

import { h, hue } from './util.js';
import { icon } from './icons.js';
import { mediaUrl } from './config.js';

/* ---------- toasts ---------- */

export function toast(message, { kind = 'info', duration = 3600, action } = {}) {
  const root = document.getElementById('toast-root');
  const el = h(
    'div',
    { class: `toast toast-${kind}` },
    icon(kind === 'error' ? 'alert' : kind === 'ok' ? 'downloaded' : 'info', 22),
    h('span', { class: 'toast-msg', text: message }),
    action
      ? h('button', {
          class: 'toast-action',
          text: action.label,
          onclick: () => {
            dismiss();
            action.onClick();
          },
        })
      : null,
  );
  let gone = false;
  function dismiss() {
    if (gone) return;
    gone = true;
    el.classList.remove('show');
    setTimeout(() => el.remove(), 250);
  }
  root.append(el);
  while (root.children.length > 3) root.firstElementChild.remove();
  requestAnimationFrame(() => el.classList.add('show'));
  el.addEventListener('click', (e) => {
    if (!e.target.closest('.toast-action')) dismiss();
  });
  setTimeout(dismiss, duration);
  return dismiss;
}

/* ---------- bottom sheets ---------- */

let openSheets = 0;

/**
 * Open a bottom sheet. content: Node or (close) => Node.
 * Returns {close, el, closed: Promise}.
 */
export function openSheet(content, { label = 'Dialog', className = '', onClose } = {}) {
  const root = document.getElementById('overlay-root');
  const scrim = h('div', { class: 'scrim' });
  const panel = h('div', { class: `sheet ${className}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': label });
  const grab = h('div', { class: 'grabber', 'aria-hidden': 'true' });
  let resolveClosed;
  const closed = new Promise((r) => (resolveClosed = r));
  let isClosed = false;
  const prevFocus = document.activeElement;

  function close(value) {
    if (isClosed) return;
    isClosed = true;
    panel.classList.remove('show');
    scrim.classList.remove('show');
    document.removeEventListener('keydown', onKey);
    openSheets = Math.max(0, openSheets - 1);
    if (!openSheets) document.documentElement.classList.remove('sheet-open');
    setTimeout(() => {
      scrim.remove();
      panel.remove();
    }, 280);
    try {
      prevFocus && prevFocus.focus && prevFocus.focus({ preventScroll: true });
    } catch {
      /* ignore */
    }
    onClose && onClose(value);
    resolveClosed(value);
  }
  function onKey(e) {
    if (e.key === 'Escape') close();
  }

  const body = typeof content === 'function' ? content(close) : content;
  panel.append(grab, body);
  scrim.addEventListener('click', () => close());
  document.addEventListener('keydown', onKey);
  enableDragToClose(panel, grab, () => close());
  root.append(scrim, panel);
  openSheets++;
  document.documentElement.classList.add('sheet-open');
  requestAnimationFrame(() => {
    scrim.classList.add('show');
    panel.classList.add('show');
    const first = panel.querySelector('[autofocus]') || panel.querySelector('button, a[href], input, textarea');
    if (first && !first.matches('input, textarea')) first.focus({ preventScroll: true });
  });
  return { close, el: panel, closed };
}

/** Let the user drag a sheet down by its handle area to dismiss it. */
export function enableDragToClose(panel, handle, onClose) {
  let startY = null;
  let dy = 0;
  const start = (e) => {
    if (panel.scrollTop > 0 && handle === panel) return;
    startY = e.touches[0].clientY;
    dy = 0;
    panel.style.transition = 'none';
  };
  const move = (e) => {
    if (startY == null) return;
    dy = Math.max(0, e.touches[0].clientY - startY);
    panel.style.transform = dy ? `translateY(${dy}px)` : '';
  };
  const end = () => {
    if (startY == null) return;
    startY = null;
    panel.style.transition = '';
    panel.style.transform = '';
    if (dy > 90) onClose();
  };
  handle.addEventListener('touchstart', start, { passive: true });
  handle.addEventListener('touchmove', move, { passive: true });
  handle.addEventListener('touchend', end);
  handle.addEventListener('touchcancel', end);
}

/**
 * Action sheet. actions: [{label, icon, danger, href, download, onSelect, hint, checked}]
 */
export function actionSheet({ title, message, actions }) {
  return openSheet(
    (close) =>
      h(
        'div',
        { class: 'actions' },
        title ? h('div', { class: 'sheet-title', text: title }) : null,
        message ? h('p', { class: 'sheet-msg', text: message }) : null,
        h(
          'div',
          { class: 'action-list' },
          actions.filter(Boolean).map((a) => {
            const inner = [
              a.icon ? icon(a.icon, 22) : h('span', { class: 'ic-spacer' }),
              h('span', { class: 'action-text' }, h('span', { class: 'action-label', text: a.label }), a.hint ? h('span', { class: 'action-hint', text: a.hint }) : null),
              a.checked ? icon('check', 20) : null,
            ];
            const cls = `action${a.danger ? ' danger' : ''}${a.checked ? ' checked' : ''}`;
            if (a.href) {
              return h('a', { class: cls, href: a.href, download: a.download || true, rel: 'noopener', onclick: () => setTimeout(close, 50) }, inner);
            }
            return h('button', { class: cls, type: 'button', disabled: a.disabled, onclick: () => { close(); a.onSelect && a.onSelect(); } }, inner);
          }),
        ),
        h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: 'Cancel', onclick: () => close() }),
      ),
    { label: title || 'Actions' },
  );
}

/** Confirm dialog as a sheet. Resolves true/false. */
export function confirmSheet({ title, message, confirmLabel = 'OK', danger = false, cancelLabel = 'Cancel' }) {
  const s = openSheet(
    (close) =>
      h(
        'div',
        { class: 'confirm' },
        h('div', { class: 'sheet-title', text: title }),
        message ? h('p', { class: 'sheet-msg', text: message }) : null,
        h(
          'div',
          { class: 'btn-col' },
          h('button', { class: `btn ${danger ? 'btn-danger' : 'btn-primary'} btn-block`, type: 'button', text: confirmLabel, onclick: () => close(true) }),
          h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: cancelLabel, onclick: () => close(false) }),
        ),
      ),
    { label: title },
  );
  return s.closed.then((v) => v === true);
}

/* ---------- small components ---------- */

export function spinner(cls = '') {
  return h('span', { class: `spinner ${cls}`, role: 'progressbar', 'aria-label': 'Working' });
}

export function callout(kind, text, extra) {
  return h(
    'div',
    { class: `callout callout-${kind}` },
    icon(kind === 'warn' || kind === 'error' ? 'alert' : kind === 'offline' ? 'cloudOff' : kind === 'demo' ? 'wave' : kind === 'ok' ? 'downloaded' : 'info', 22),
    h('div', { class: 'callout-body' }, typeof text === 'string' ? h('span', { text }) : text, extra || null),
  );
}

export function progressBar(pct = 0, cls = '') {
  const fill = h('i');
  const bar = h('div', { class: `bar ${cls}`, role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100' }, fill);
  bar.set = (p) => {
    const v = Math.max(0, Math.min(100, p || 0));
    bar.style.setProperty('--p', String(v / 100));
    bar.setAttribute('aria-valuenow', String(Math.round(v)));
  };
  bar.set(pct);
  return bar;
}

/** Book cover with an elegant generated fallback when there's no image. */
export function coverEl(book, cls = '') {
  const title = (book && book.title) || 'Book';
  const el = h('div', { class: `cover ${cls}`, style: `--h:${hue(title)}` });
  const fallback = h('span', { class: 'cover-fallback' }, h('span', { class: 'cover-letter', text: title.trim().charAt(0).toUpperCase() || 'A' }));
  el.append(fallback);
  const src = book && book.cover_url ? mediaUrl(book.cover_url) : null;
  if (src) {
    const img = h('img', { alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' });
    img.addEventListener('load', () => el.classList.add('has-img'));
    img.addEventListener('error', () => img.remove());
    img.src = src;
    el.append(img);
  }
  el.setSrc = (b) => {
    const next = b && b.cover_url ? mediaUrl(b.cover_url) : null;
    const img = el.querySelector('img');
    if (img && next && img.getAttribute('src') !== next) img.src = next;
  };
  return el;
}

/** Equalizer glyph for the playing chapter. */
export function equalizer() {
  return h('span', { class: 'eq', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'));
}

export function iconButton(name, label, onClick, cls = '') {
  return h('button', { class: `icon-btn ${cls}`, type: 'button', 'aria-label': label, title: label, onclick: onClick }, icon(name));
}

/** Top bar used by every screen. */
export function topbar({ left, title, right, cls = '' } = {}) {
  return h(
    'header',
    { class: `topbar ${cls}` },
    h('div', { class: 'topbar-side' }, left || null),
    h('div', { class: 'topbar-title' }, title || null),
    h('div', { class: 'topbar-side end' }, right || null),
  );
}

export function backButton(label, onClick) {
  return h('button', { class: 'back-btn', type: 'button', 'aria-label': label ? `Back to ${label}` : 'Back', onclick: onClick }, icon('chevronLeft'), label ? h('span', { text: label }) : null);
}

/** Daily goal ring: listened minutes in the centre, progress around. */
export function goalRing(size = 64) {
  const svgNS = 'http://www.w3.org/2000/svg';
  const R = 44;
  const C = 2 * Math.PI * R;
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.setAttribute('aria-hidden', 'true');
  const track = document.createElementNS(svgNS, 'circle');
  const bar = document.createElementNS(svgNS, 'circle');
  for (const c of [track, bar]) {
    c.setAttribute('cx', '50');
    c.setAttribute('cy', '50');
    c.setAttribute('r', String(R));
  }
  track.setAttribute('class', 'gr-track');
  bar.setAttribute('class', 'gr-bar');
  bar.setAttribute('stroke-dasharray', String(C));
  svg.append(track, bar);
  const num = h('span', { class: 'gr-num' });
  const unit = h('span', { class: 'gr-unit' });
  const el = h('div', { class: 'goal-ring', style: `--ring:${size}px`, role: 'img' }, svg, h('span', { class: 'gr-center' }, num, unit));
  el.set = (listenSeconds, goalMinutes) => {
    const min = Math.floor((listenSeconds || 0) / 60);
    const frac = Math.min(1, (listenSeconds || 0) / (Math.max(1, goalMinutes) * 60));
    bar.setAttribute('stroke-dashoffset', String(C * (1 - frac)));
    bar.style.opacity = frac > 0 ? '1' : '0';
    num.textContent = String(min);
    unit.textContent = size >= 80 ? `of ${goalMinutes} min` : 'min';
    el.classList.toggle('done', frac >= 1);
    el.setAttribute('aria-label', `${min} of ${goalMinutes} minutes listened today`);
  };
  return el;
}
