// "Download for offline": audio + cover go into Cache Storage ("audio-v1").
// - A finished book is saved as its single stream file (Book.stream_url), so offline
//   listening also plays as one continuous file (keeps going with the phone locked).
// - A book that is still rendering is saved chapter by chapter (Chapter.audio_url).
// sw.js serves these from the cache (with Range support); the player can also fall back
// to a blob: URL made from the cached file.

import { Emitter, store } from './util.js';
import { mediaUrl, cacheKey } from './config.js';

export const AUDIO_CACHE = 'audio-v1';
export const events = new Emitter(); // 'progress' (bookId, job|null), 'change' (bookId)

const active = new Map(); // bookId -> {done, total, fraction, abort, error, promise}

export function supported() {
  return typeof caches !== 'undefined' && window.isSecureContext;
}

function records() {
  return store.get('offline', {}) || {};
}
function saveRecord(bookId, rec) {
  const all = records();
  if (rec) all[bookId] = rec;
  else delete all[bookId];
  store.set('offline', all);
}

export function readyChapters(book) {
  return (book.chapters || []).filter((c) => c.include !== false && c.status === 'ready' && c.audio_url).sort((a, b) => a.index - b.index);
}

export function keyFor(url) {
  return cacheKey(mediaUrl(url, { token: '' }));
}

function includedCount(book) {
  return (book.chapters || []).filter((c) => c.include !== false).length;
}

export function isStreamOffline(book) {
  const rec = records()[book.id];
  return !!(rec && rec.stream && book.stream_url && rec.stream === keyFor(book.stream_url));
}

/** Quick status from the localStorage record: {state: 'none'|'partial'|'full', have, total, bytes}. */
export function status(book) {
  const rec = records()[book.id];
  const ready = readyChapters(book);
  const total = book.stream_url ? includedCount(book) : ready.length;
  if (!rec) return { state: 'none', have: 0, total };
  if (isStreamOffline(book)) return { state: 'full', have: total, total, bytes: rec.bytes || 0, stream: true };
  const have = new Set(rec.keys || []);
  const got = ready.filter((c) => have.has(keyFor(c.audio_url))).length;
  if (!got) return { state: 'none', have: 0, total: ready.length };
  const full = !book.stream_url && got === ready.length && ready.length === includedCount(book);
  return { state: full ? 'full' : 'partial', have: got, total: ready.length, bytes: rec.bytes || 0 };
}

export function isChapterOffline(book, chapter) {
  if (chapter.include === false) return false;
  if (isStreamOffline(book)) return true;
  const rec = records()[book.id];
  return !!(rec && chapter.audio_url && (rec.keys || []).includes(keyFor(chapter.audio_url)));
}

export function downloading(bookId) {
  return active.get(bookId) || null;
}

/** Double-check the record against Cache Storage (the OS may have evicted files). */
export async function verify(book) {
  if (!supported()) return status(book);
  const rec = records()[book.id];
  if (!rec) return status(book);
  try {
    const cache = await caches.open(AUDIO_CACHE);
    const keys = [];
    for (const k of rec.keys || []) if (await cache.match(k)) keys.push(k);
    const stream = rec.stream && (await cache.match(rec.stream)) ? rec.stream : null;
    if (keys.length !== (rec.keys || []).length || stream !== (rec.stream || null)) {
      saveRecord(book.id, keys.length || stream ? { ...rec, keys, stream } : null);
      events.emit('change', book.id);
    }
  } catch {
    /* ignore */
  }
  return status(book);
}

/**
 * Fetch `url` and store it under `key`, reporting bytes as they arrive. The body is
 * streamed into Cache Storage (no whole-book copy in memory); falls back to a Blob
 * where streaming bodies aren't supported.
 */
async function fetchInto(cache, url, key, onBytes, signal) {
  const res = await fetch(mediaUrl(url), { signal, cache: 'no-store' });
  if (!res.ok) throw new Error(`Download failed (error ${res.status}).`);
  const type = res.headers.get('content-type') || 'application/octet-stream';
  const total = Number(res.headers.get('content-length')) || 0;
  if (res.body && typeof TransformStream !== 'undefined' && res.body.pipeThrough) {
    let got = 0;
    const counter = new TransformStream({
      transform(chunk, ctl) {
        got += chunk.byteLength;
        if (onBytes) onBytes(got, total);
        ctl.enqueue(chunk);
      },
    });
    const headers = { 'Content-Type': type };
    if (total) headers['Content-Length'] = String(total);
    try {
      await cache.put(key, new Response(res.body.pipeThrough(counter), { headers }));
      return got;
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      // Some engines refuse streamed bodies in Cache.put: download again as a Blob.
      const again = await fetch(mediaUrl(url), { signal, cache: 'no-store' });
      if (!again.ok) throw new Error(`Download failed (error ${again.status}).`);
      const blob = await again.blob();
      await cache.put(key, new Response(blob, { headers: { 'Content-Type': type, 'Content-Length': String(blob.size) } }));
      if (onBytes) onBytes(blob.size, blob.size);
      return blob.size;
    }
  }
  const blob = await res.blob();
  await cache.put(key, new Response(blob, { headers: { 'Content-Type': type, 'Content-Length': String(blob.size) } }));
  if (onBytes) onBytes(blob.size, blob.size);
  return blob.size;
}

/** Download a book for offline listening. Resolves with the final status. */
export async function download(book) {
  if (!supported()) throw new Error('Offline downloads need the app to be opened over HTTPS.');
  if (active.has(book.id)) return active.get(book.id).promise;
  const ctl = new AbortController();
  const job = { done: 0, total: 0, fraction: 0, abort: () => ctl.abort(), error: null, stream: false };
  active.set(book.id, job);

  job.promise = (async () => {
    try {
      if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
      const cache = await caches.open(AUDIO_CACHE);
      const rec = records()[book.id] || { keys: [], bytes: 0 };
      const wanted = new Set();

      if (book.cover_url) {
        const ck = keyFor(book.cover_url);
        wanted.add(ck);
        if (!(await cache.match(ck))) {
          try {
            await fetchInto(cache, book.cover_url, ck, null, ctl.signal);
          } catch {
            /* cover is optional */
          }
        }
        rec.cover = ck;
      }

      if (book.stream_url) {
        // Finished book: one continuous file.
        const sk = keyFor(book.stream_url);
        wanted.add(sk);
        job.stream = true;
        job.total = 1;
        events.emit('progress', book.id, job);
        let bytes = rec.stream === sk ? rec.bytes || 0 : 0;
        if (!(rec.stream === sk && (await cache.match(sk)))) {
          bytes = await fetchInto(
            cache,
            book.stream_url,
            sk,
            (got, total) => {
              job.fraction = total ? got / total : 0;
              job.bytes = got;
              events.emit('progress', book.id, job);
            },
            ctl.signal,
          );
        }
        job.done = 1;
        job.fraction = 1;
        saveRecord(book.id, { cover: rec.cover, stream: sk, keys: [], bytes, at: Date.now() });
      } else {
        // Still rendering: the chapters that are ready now.
        const chapters = readyChapters(book);
        const keys = new Set(rec.keys || []);
        let bytes = rec.stream ? 0 : rec.bytes || 0;
        const todo = [];
        for (const c of chapters) {
          const key = keyFor(c.audio_url);
          wanted.add(key);
          if (keys.has(key) && (await cache.match(key))) continue;
          todo.push({ c, key });
        }
        job.total = todo.length;
        events.emit('progress', book.id, job);
        for (const { c, key } of todo) {
          const size = await fetchInto(
            cache,
            c.audio_url,
            key,
            (got, total) => {
              job.fraction = total ? (job.done + got / total) / job.total : job.done / job.total;
              events.emit('progress', book.id, job);
            },
            ctl.signal,
          );
          bytes += size;
          keys.add(key);
          job.done++;
          job.fraction = job.done / job.total;
          saveRecord(book.id, { cover: rec.cover, stream: null, keys: [...keys].filter((k) => wanted.has(k)), bytes, at: Date.now() });
          events.emit('progress', book.id, job);
        }
        saveRecord(book.id, { cover: rec.cover, stream: null, keys: [...keys].filter((k) => wanted.has(k)), bytes, at: Date.now() });
      }

      // Drop files of this book we no longer need (older versions, chapter files once the stream is saved).
      for (const req of await cache.keys()) {
        if (req.url.includes(`/api/books/${book.id}/`) && !wanted.has(req.url)) await cache.delete(req);
      }
      return status(book);
    } catch (err) {
      job.error = err.name === 'AbortError' ? null : err;
      throw err;
    } finally {
      active.delete(book.id);
      events.emit('progress', book.id, null);
      events.emit('change', book.id);
    }
  })();
  return job.promise;
}

export async function remove(bookId) {
  const job = active.get(bookId);
  if (job) job.abort();
  saveRecord(bookId, null);
  if (supported()) {
    try {
      const cache = await caches.open(AUDIO_CACHE);
      for (const req of await cache.keys()) if (req.url.includes(`/api/books/${bookId}/`)) await cache.delete(req);
    } catch {
      /* ignore */
    }
  }
  events.emit('change', bookId);
}

export async function removeAll() {
  for (const id of Object.keys(records())) await remove(id);
  if (supported()) {
    try {
      await caches.delete(AUDIO_CACHE);
    } catch {
      /* ignore */
    }
  }
}

export function totalBytes() {
  return Object.values(records()).reduce((n, r) => n + (r.bytes || 0), 0);
}

export function offlineBookIds() {
  return Object.keys(records());
}

/** blob: URL for a cached file (player fallback when the service worker can't serve media). */
export async function blobUrl(url) {
  if (!supported() || !url) return null;
  try {
    const cache = await caches.open(AUDIO_CACHE);
    const hit = await cache.match(keyFor(url));
    if (!hit) return null;
    return URL.createObjectURL(await hit.blob());
  } catch {
    return null;
  }
}
