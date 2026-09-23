// Server address + access code, persisted in localStorage.

import { store } from './util.js';

export const APP_VERSION = '3.2.0';

export function normalizeServer(input) {
  let v = String(input || '').trim();
  if (!v) return '';
  if (!/^https?:\/\//i.test(v)) v = (location.protocol === 'https:' ? 'https://' : 'http://') + v;
  v = v.replace(/\/+$/, '').replace(/\/api$/i, '');
  try {
    const u = new URL(v);
    return (u.origin + u.pathname).replace(/\/+$/, '');
  } catch {
    return v;
  }
}

export function getServer() {
  return String(store.get('server', '') || '').trim();
}
export function setServer(v) {
  store.set('server', normalizeServer(v));
}
export function getToken() {
  return String(store.get('token', '') || '').trim();
}
export function setToken(v) {
  store.set('token', String(v || '').trim());
}

/** '' means same origin (API paths are server-absolute: "/api/..."). */
export function apiBase() {
  return getServer();
}

/** Turn a server-relative media URL into a usable one (base + ?token=). */
export function mediaUrl(url, { server = getServer(), token = getToken() } = {}) {
  if (!url) return null;
  let abs = /^https?:\/\//i.test(url) ? url : server + url;
  if (token) abs += (abs.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
  return abs;
}

/** Absolute URL for things like Media Session artwork. */
export function absolute(url) {
  try {
    return new URL(url, location.href).href;
  } catch {
    return url;
  }
}

/** Cache Storage key for a media URL: absolute, without the token (must match sw.js). */
export function cacheKey(url) {
  const u = new URL(url, location.href);
  u.searchParams.delete('token');
  u.hash = '';
  return u.href;
}

/**
 * Handle ?pair=<token>&server=<url> links: save them and strip them from the address bar.
 * Returns true when something was saved.
 */
export function consumePairing() {
  const q = new URLSearchParams(location.search);
  if (!q.has('pair') && !q.has('server')) return false;
  if (q.has('pair')) setToken(q.get('pair'));
  if (q.has('server')) setServer(q.get('server'));
  q.delete('pair');
  q.delete('server');
  const rest = q.toString();
  try {
    history.replaceState(history.state, '', location.pathname + (rest ? '?' + rest : '') + location.hash);
  } catch {
    /* ignore */
  }
  return true;
}
