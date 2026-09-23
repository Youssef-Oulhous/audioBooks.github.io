// Small shared helpers: DOM building, formatting, storage, events.

const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'selected', 'indeterminate']);

/** h('div', {class: 'x', onclick: fn}, child, 'text', [more]) -> HTMLElement */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const k in props) {
      const v = props[k];
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style') {
        if (typeof v === 'string') el.setAttribute('style', v);
        else Object.assign(el.style, v);
      } else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (PROPS.has(k)) el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(el, children);
  return el;
}

/** Replace an element's children; null/false children are skipped (native replaceChildren would print "null"). */
export function fill(el, ...kids) {
  el.replaceChildren();
  return append(el, kids);
}

export function append(el, kids) {
  for (const c of kids) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

const pad = (n) => String(n).padStart(2, '0');

/** 754 -> "12:34", 3723 -> "1:02:03" */
export function clock(sec) {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  sec = Math.floor(sec);
  const hh = Math.floor(sec / 3600);
  const mm = Math.floor((sec % 3600) / 60);
  const ss = sec % 60;
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${mm}:${pad(ss)}`;
}

/** 3900 -> "1 h 05 m", 2520 -> "42 m", 20 -> "< 1 m" */
export function long(sec) {
  if (sec == null || !Number.isFinite(sec)) return '';
  const m = Math.round(sec / 60);
  if (m < 1) return sec > 0 ? '< 1 m' : '0 m';
  const hh = Math.floor(m / 60);
  const mm = m % 60;
  return hh ? `${hh} h ${pad(mm)} m` : `${mm} m`;
}

export function words(n) {
  return Number(n || 0).toLocaleString('en-US');
}

export function plural(n, one, many = one + 's') {
  return `${words(n)} ${n === 1 ? one : many}`;
}

export function bytes(n) {
  if (!n) return '0 MB';
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

export function langName(code) {
  if (!code) return '';
  const known = { en: 'English', zh: 'Chinese' };
  if (known[code]) return known[code];
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) || code;
  } catch {
    return code;
  }
}

/** Stable hue (0-359) for a string, used for placeholder covers. */
export function hue(str) {
  let x = 0;
  for (const ch of String(str || '')) x = (x * 31 + ch.codePointAt(0)) >>> 0;
  return x % 360;
}

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

export function throttle(fn, ms) {
  let last = 0;
  return (...args) => {
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      fn(...args);
    }
  };
}

/** localStorage wrapper: JSON values, never throws. */
export const store = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem('auk.' + key);
      return raw == null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem('auk.' + key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  },
  del(key) {
    try {
      localStorage.removeItem('auk.' + key);
    } catch {
      /* ignore */
    }
  },
  keys(prefix = '') {
    const out = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('auk.' + prefix)) out.push(k.slice(4));
      }
    } catch {
      /* ignore */
    }
    return out;
  },
};

export class Emitter {
  constructor() {
    this._handlers = new Map();
  }
  on(ev, fn) {
    if (!this._handlers.has(ev)) this._handlers.set(ev, new Set());
    this._handlers.get(ev).add(fn);
    return () => this._handlers.get(ev)?.delete(fn);
  }
  emit(ev, ...args) {
    for (const fn of [...(this._handlers.get(ev) || [])]) {
      try {
        fn(...args);
      } catch (err) {
        console.error(err);
      }
    }
  }
}

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** 0.05 s of silence as a WAV data URI (used to unlock audio inside a tap on iOS). */
let silent = null;
export function silentWav() {
  if (silent) return silent;
  const n = 400;
  const buf = new Uint8Array(44 + n);
  const dv = new DataView(buf.buffer);
  const str = (o, x) => [...x].forEach((c, i) => (buf[o + i] = c.charCodeAt(0)));
  str(0, 'RIFF');
  dv.setUint32(4, 36 + n, true);
  str(8, 'WAVEfmt ');
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, 8000, true);
  dv.setUint32(28, 8000, true);
  dv.setUint16(32, 1, true);
  dv.setUint16(34, 8, true);
  str(36, 'data');
  dv.setUint32(40, n, true);
  buf.fill(128, 44);
  let bin = '';
  buf.forEach((b) => (bin += String.fromCharCode(b)));
  silent = 'data:audio/wav;base64,' + btoa(bin);
  return silent;
}
