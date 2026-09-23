// Listening stats (docs/API.md "Listening stats", docs/DESIGN.md §6).
//
// This device keeps a live StatsDoc in localStorage; the server stores one document per
// device so history survives a reinstall and adds up across devices. Everything the Stats
// screen and the water covers show comes from merged(): this device's live document plus
// every other device's document from the server.

import { store, Emitter, clamp } from './util.js';
import { api } from './api.js';
import { apiBase, getToken } from './config.js';
import { includedChapters } from './player.js';

export const events = new Emitter(); // 'change', 'finished' (bookId)

const KEY = 'stats.v1';
const REMOTE_KEY = 'stats.remote';
export const ACTIVE_SECONDS = 60; // a day counts as active (and gets a colour) from 1 minute
export const STREAK_SECONDS = 300; // a streak day needs 5 minutes
export const GOALS = [15, 30, 45, 60, 90];

/* ---------- dates (device-local calendar) ---------- */

const pad = (n) => String(n).padStart(2, '0');
export function dateKey(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function parseKey(k) {
  const [y, m, d] = k.split('-').map(Number);
  return new Date(y, m - 1, d);
}
export function addDays(k, n) {
  const d = parseKey(k);
  d.setDate(d.getDate() + n);
  return dateKey(d);
}
export function monthKey(y, m) {
  return `${y}-${pad(m + 1)}`;
}

/* ---------- documents ---------- */

function newDoc() {
  return { version: 1, days: {}, books: {}, goal_minutes: 30, updated_at: 0 };
}

function validDoc(d) {
  return d && typeof d === 'object' && d.days && typeof d.days === 'object' && d.books && typeof d.books === 'object';
}

function loadDoc() {
  const d = store.get(KEY, null);
  return validDoc(d) ? { ...newDoc(), ...d } : newDoc();
}

function deviceId() {
  let id = store.get('device');
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(id)) {
    const abc = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    const bytes = new Uint8Array(20);
    (window.crypto || {}).getRandomValues ? crypto.getRandomValues(bytes) : bytes.forEach((_, i) => (bytes[i] = Math.random() * 256));
    id = 'ios_' + [...bytes].map((b) => abc[b % abc.length]).join('');
    store.set('device', id);
  }
  return id;
}

export const DEVICE = deviceId();
let doc = loadDoc();
let remote = store.get(REMOTE_KEY, {}) || {}; // other devices' documents, cached for offline
let dirty = false;
let unsynced = true; // changed since the last successful PUT
let lastPut = 0;
let cache = null;
export const state = { fetched: false, fetching: false, fetchError: null };

function touch() {
  doc.updated_at = Math.floor(Date.now() / 1000);
  dirty = true;
  unsynced = true;
  cache = null;
}

function day(k) {
  if (!doc.days[k]) doc.days[k] = { listen: 0, app: 0, books: {} };
  const d = doc.days[k];
  d.books = d.books || {};
  return d;
}

function bookEntry(book) {
  const e = doc.books[book.id] || (doc.books[book.id] = { title: book.title, author: book.author || '', furthest: 0, total: 0, finished_at: null, last_played_at: 0 });
  e.title = book.title || e.title;
  e.author = book.author || e.author || '';
  return e;
}

/** Book length in seconds: included chapter durations when all are ready, else the estimate. */
export function bookTotal(book) {
  const inc = includedChapters(book);
  if (inc.length && inc.every((c) => c.status === 'ready' && c.duration)) return inc.reduce((n, c) => n + c.duration, 0);
  return (book.est_seconds || 0) / (book.pace || 1);
}

/** Position in book-time (seconds of audio before this point): earlier included chapters + time in chapter. */
export function bookPosition(book, index, timeInChapter) {
  let t = 0;
  for (const c of includedChapters(book)) {
    if (c.index === index) break;
    t += c.duration || (c.est_seconds || 0) / (book.pace || 1);
  }
  return t + (timeInChapter || 0);
}

/* ---------- recording ---------- */

export function addListen(book, seconds, position) {
  if (!book || !(seconds > 0)) return;
  const k = dateKey();
  const d = day(k);
  d.listen = round1(d.listen + seconds);
  d.books[book.id] = round1((d.books[book.id] || 0) + seconds);
  const e = bookEntry(book);
  e.last_played_at = Math.floor(Date.now() / 1000);
  const total = bookTotal(book);
  if (total > 0) e.total = round1(Math.max(e.total || 0, total));
  if (position != null && position > (e.furthest || 0)) e.furthest = round1(position);
  if (!e.finished_at && book.status === 'ready' && e.total > 0 && e.furthest >= 0.98 * e.total) {
    e.finished_at = k;
    events.emit('finished', book.id);
  }
  touch();
}

export function addApp(seconds) {
  if (!(seconds > 0)) return;
  const d = day(dateKey());
  d.app = round1(d.app + seconds);
  touch();
}

export function goalMinutes() {
  return merged().goal_minutes || 30;
}

export function setGoal(min) {
  doc.goal_minutes = min;
  touch();
  save();
  sync({ force: true });
  events.emit('change');
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

/* ---------- player hook: listening seconds from media-time progress ---------- */

/**
 * On each timeupdate while playing, the media time that elapsed since the previous one
 * (0 < d < 30 s, no seek in between) is added as listening time, divided by the playback
 * rate. This stays right when iOS throttles events while the phone is locked.
 */
export function attachPlayer(player) {
  const a = player.audio;
  let last = null;
  const reset = () => (last = null);
  a.addEventListener('timeupdate', () => {
    if (a.paused || !player.loaded || !player.book || player._seeking) {
      last = null;
      return;
    }
    const t = a.currentTime;
    if (last != null) {
      const d = t - last;
      if (d > 0 && d < 30) addListen(player.book, d / (a.playbackRate || 1), bookPosition(player.book, player.index, player.time));
    }
    last = t;
  });
  for (const ev of ['seeking', 'pause', 'ended', 'waiting', 'emptied', 'loadstart', 'abort']) a.addEventListener(ev, reset);
  a.addEventListener('pause', () => {
    save();
    events.emit('change');
  });
}

/* ---------- app time ---------- */

let lastVisible = null;
function tickApp() {
  const now = Date.now();
  if (document.visibilityState !== 'visible') {
    lastVisible = null;
    return;
  }
  if (lastVisible != null) addApp(Math.min(10, (now - lastVisible) / 1000));
  lastVisible = now;
}

/* ---------- persistence + sync ---------- */

export function save() {
  if (!dirty) return;
  dirty = false;
  if (!store.set(KEY, doc)) {
    // storage full: drop per-book detail of days older than a year
    const cutoff = addDays(dateKey(), -366);
    for (const k of Object.keys(doc.days)) if (k < cutoff) doc.days[k].books = {};
    store.set(KEY, doc);
  }
}

export async function sync({ force = false, keepalive = false } = {}) {
  if (!unsynced || navigator.onLine === false) return;
  if (!force && Date.now() - lastPut < 60000) return;
  lastPut = Date.now();
  unsynced = false;
  const body = JSON.stringify(doc);
  const headers = { 'Content-Type': 'application/json' };
  const tk = getToken();
  if (tk) headers['X-Access-Token'] = tk;
  try {
    const res = await fetch(`${apiBase()}/api/stats/${encodeURIComponent(DEVICE)}`, {
      method: 'PUT',
      headers,
      body,
      keepalive: keepalive && body.length < 60000,
      cache: 'no-store',
    });
    if (!res.ok) unsynced = true;
  } catch {
    unsynced = true;
  }
}

/** Fetch every device's document; keeps the other devices' copies for offline use. */
export async function refresh() {
  if (state.fetching) return;
  state.fetching = true;
  try {
    const data = await api('/api/stats', { quiet401: true, timeout: 15000 });
    const devices = (data && data.devices) || {};
    remote = {};
    for (const [id, d] of Object.entries(devices)) if (id !== DEVICE && validDoc(d)) remote[id] = d;
    store.set(REMOTE_KEY, remote);
    // goal_minutes: the most recently updated document wins
    const newest = Object.values(remote).reduce((a, d) => ((d.updated_at || 0) > (a ? a.updated_at || 0 : -1) ? d : a), null);
    if (newest && (newest.updated_at || 0) > (doc.updated_at || 0) && newest.goal_minutes && newest.goal_minutes !== doc.goal_minutes) {
      doc.goal_minutes = newest.goal_minutes;
      dirty = true;
      save();
    }
    state.fetchError = null;
  } catch (err) {
    state.fetchError = err;
  } finally {
    state.fetched = true;
    state.fetching = false;
    cache = null;
    events.emit('change');
  }
}

/* ---------- merge ---------- */

export function merged() {
  if (cache) return cache;
  const docs = [doc, ...Object.values(remote)];
  const out = { days: {}, books: {}, goal_minutes: doc.goal_minutes || 30 };
  let newest = -1;
  for (const d of docs) {
    for (const [k, v] of Object.entries(d.days || {})) {
      const o = out.days[k] || (out.days[k] = { listen: 0, app: 0, books: {} });
      o.listen += Number(v.listen) || 0;
      o.app += Number(v.app) || 0;
      for (const [bid, s] of Object.entries(v.books || {})) o.books[bid] = (o.books[bid] || 0) + (Number(s) || 0);
    }
    for (const [bid, b] of Object.entries(d.books || {})) {
      const o = out.books[bid];
      if (!o) {
        out.books[bid] = { ...b };
        continue;
      }
      o.title = o.title || b.title;
      o.author = o.author || b.author;
      o.furthest = Math.max(o.furthest || 0, b.furthest || 0);
      o.total = Math.max(o.total || 0, b.total || 0);
      o.finished_at = [o.finished_at, b.finished_at].filter(Boolean).sort()[0] || null;
      o.last_played_at = Math.max(o.last_played_at || 0, b.last_played_at || 0);
    }
    if ((d.updated_at || 0) > newest && d.goal_minutes) {
      newest = d.updated_at || 0;
      out.goal_minutes = d.goal_minutes;
    }
  }
  cache = out;
  return out;
}

/* ---------- derived numbers ---------- */

/** Listened percentage of a book (0-100): furthest / total, 100 once finished. */
export function progress(book) {
  const m = merged().books[book.id];
  if (!m) return 0;
  if (m.finished_at) return 100;
  const total = book.status === 'ready' ? bookTotal(book) || m.total : m.total || bookTotal(book);
  if (!total) return 0;
  return clamp(Math.round(((m.furthest || 0) / total) * 100), 0, 99);
}

export function isFinished(bookId) {
  const m = merged().books[bookId];
  return !!(m && m.finished_at);
}

export function listenOn(k) {
  const d = merged().days[k];
  return d ? d.listen : 0;
}

/**
 * "Usual day": median listening of active days (>= 60 s) in the 60 days before `anchor`
 * (exclusive). Falls back to the daily goal when there are fewer than 3 such days.
 */
export function usualDay(anchor = dateKey()) {
  const days = merged().days;
  const vals = [];
  for (let i = 1; i <= 60; i++) {
    const d = days[addDays(anchor, -i)];
    if (d && d.listen >= ACTIVE_SECONDS) vals.push(d.listen);
  }
  if (vals.length < 3) return goalMinutes() * 60;
  vals.sort((a, b) => a - b);
  const mid = vals.length >> 1;
  return vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
}

/** Heatmap level 0-4 for a day's listening compared with the usual day. */
export function level(listen, usual) {
  if (!(listen >= ACTIVE_SECONDS)) return 0;
  const r = listen / (usual || 1);
  if (r < 0.5) return 1;
  if (r < 1) return 2;
  if (r < 1.5) return 3;
  return 4;
}

/** Consecutive days with >= 5 minutes, ending today (or yesterday if today isn't there yet). */
export function streak(today = dateKey()) {
  const days = merged().days;
  const ok = (k) => (days[k] ? days[k].listen : 0) >= STREAK_SECONDS;
  let start = ok(today) ? today : addDays(today, -1);
  let current = 0;
  while (ok(start)) {
    current++;
    start = addDays(start, -1);
  }
  const keys = Object.keys(days).filter(ok).sort();
  let longest = 0;
  let run = 0;
  let prev = null;
  for (const k of keys) {
    run = prev && addDays(prev, 1) === k ? run + 1 : 1;
    longest = Math.max(longest, run);
    prev = k;
  }
  return { current, longest: Math.max(longest, current), today: ok(today) };
}

export function daysInMonth(y, m) {
  return new Date(y, m + 1, 0).getDate();
}

/** Totals for a calendar month (y, m 0-11), up to `untilDay` (inclusive) when given. */
export function monthTotals(y, m, untilDay = null) {
  const days = merged().days;
  const n = untilDay || daysInMonth(y, m);
  let listen = 0;
  let app = 0;
  let active = 0;
  for (let d = 1; d <= n; d++) {
    const v = days[`${monthKey(y, m)}-${pad(d)}`];
    if (!v) continue;
    listen += v.listen || 0;
    app += v.app || 0;
    if (v.listen >= ACTIVE_SECONDS) active++;
  }
  return { listen, app, active };
}

/** Books whose finished_at falls in the month, oldest first. */
export function finishedIn(y, m) {
  const prefix = monthKey(y, m);
  return Object.entries(merged().books)
    .filter(([, b]) => b.finished_at && b.finished_at.startsWith(prefix))
    .sort((a, b) => a[1].finished_at.localeCompare(b[1].finished_at))
    .map(([id, b]) => ({ id, ...b }));
}

/** Books listened on a day, most listened first. */
export function booksOn(k) {
  const d = merged().days[k];
  if (!d) return [];
  const books = merged().books;
  return Object.entries(d.books || {})
    .filter(([, s]) => s >= 1)
    .sort((a, b) => b[1] - a[1])
    .map(([id, s]) => ({ id, seconds: s, title: (books[id] && books[id].title) || 'A deleted book', author: (books[id] && books[id].author) || '', finished: !!(books[id] && books[id].finished_at === k) }));
}

export function totalListen() {
  return Object.values(merged().days).reduce((n, d) => n + (d.listen || 0), 0);
}

export function hasAnyListening() {
  return Object.values(merged().days).some((d) => d.listen >= ACTIVE_SECONDS);
}

/* ---------- start ---------- */

export function start() {
  lastVisible = document.visibilityState === 'visible' ? Date.now() : null;
  setInterval(() => {
    tickApp();
    save();
    sync();
  }, 5000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      if (lastVisible != null) addApp(Math.min(10, (Date.now() - lastVisible) / 1000));
      lastVisible = null;
      save();
      sync({ force: true, keepalive: true });
    } else {
      lastVisible = Date.now();
      refresh();
    }
  });
  window.addEventListener('pagehide', () => {
    save();
    sync({ force: true, keepalive: true });
  });
  refresh();
}

/** Test/debug hook: the live local document. */
export function localDoc() {
  return doc;
}
