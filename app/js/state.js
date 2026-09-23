// Shared in-memory state + the offline snapshot of the last /api/books response.

import { Emitter, store } from './util.js';
import { getStatus } from './api.js';

export const bus = new Emitter();

export const state = {
  books: new Map(), // id -> Book (latest seen)
  status: null, // last /api/status
  statusError: null, // 'offline' | message
  offline: false, // last books request failed with a network error
  authMessage: null, // shown on Settings after a 401
  scrollToCurrent: false, // book screen should scroll to the playing chapter
};

function slim(book) {
  // Chapter previews are only needed during setup; keep the snapshot small.
  return { ...book, chapters: (book.chapters || []).map(({ preview, ...c }) => c) };
}

function sortBooks(list) {
  return list.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
}

export function snapshotBooks() {
  const snap = store.get('snapshot', null);
  return snap && Array.isArray(snap.books) ? snap.books : [];
}

function saveSnapshot() {
  const list = sortBooks([...state.books.values()].map(slim));
  if (!store.set('snapshot', { at: Date.now(), books: list })) {
    // Storage full: keep only books that are downloaded or recently played.
    store.set('snapshot', { at: Date.now(), books: list.slice(0, 10) });
  }
}

export function rememberBooks(list) {
  state.books = new Map(list.map((b) => [b.id, b]));
  saveSnapshot();
  bus.emit('books', list);
}

export function rememberBook(book) {
  if (!book || !book.id) return;
  if (!state.books.size) for (const b of snapshotBooks()) state.books.set(b.id, b);
  state.books.set(book.id, book);
  saveSnapshot();
  bus.emit('book', book);
}

export function forgetBook(id) {
  state.books.delete(id);
  saveSnapshot();
  store.del('pos:' + id);
  const last = store.get('last');
  if (last && last.bookId === id) store.del('last');
}

/** Best known copy of a book: memory, then the offline snapshot. */
export function knownBook(id) {
  return state.books.get(id) || snapshotBooks().find((b) => b.id === id) || null;
}

export async function refreshStatus() {
  try {
    state.status = await getStatus({ quiet401: true });
    state.statusError = null;
  } catch (err) {
    state.status = null;
    state.statusError = err.network || err.timeout ? 'offline' : err.message;
  }
  bus.emit('status', state.status);
  return state.status;
}
