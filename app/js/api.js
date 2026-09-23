// Thin client for the audiobook server API (see docs/API.md).

import { apiBase, getToken } from './config.js';
import { Emitter } from './util.js';

export const apiEvents = new Emitter();

export class ApiError extends Error {
  constructor(message, status = 0, { network = false, timeout = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.network = network;
    this.timeout = timeout;
  }
}

function detailOf(data, status) {
  const d = data && data.detail;
  if (typeof d === 'string' && d) return d;
  if (Array.isArray(d) && d.length) return d.map((x) => x.msg || String(x)).join('; ');
  if (status === 413) return 'That file is too large for the server.';
  if (status >= 500) return `The server had a problem (error ${status}).`;
  return `Request failed (error ${status}).`;
}

/**
 * JSON request. Options: method, body (object), timeout (ms), base/token overrides,
 * quiet401 (don't route to Settings on 401).
 */
export async function api(path, { method = 'GET', body, timeout = 30000, base, token, quiet401 = false } = {}) {
  const url = (base ?? apiBase()) + path;
  if (navigator.onLine === false) {
    // The phone knows it has no connection: fail fast instead of waiting for a timeout.
    apiEvents.emit('network-error');
    throw new ApiError('You’re offline.', 0, { network: true });
  }
  const headers = { Accept: 'application/json' };
  const tk = token ?? getToken();
  if (tk) headers['X-Access-Token'] = tk;
  let payload;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let res;
  try {
    // A tunnel (Tailscale Funnel, Cloudflare) sometimes drops the very first connection of a
    // burst. Reading is safe to repeat, so a dropped GET is tried once more before giving up.
    for (let attempt = 0; ; attempt++) {
      try {
        res = await fetch(url, { method, headers, body: payload, signal: ctl.signal, cache: 'no-store' });
        break;
      } catch (err) {
        const retriable = attempt === 0 && method === 'GET' && !(err && err.name === 'AbortError');
        if (!retriable) throw err;
        await new Promise((done) => setTimeout(done, 400));
      }
    }
  } catch (err) {
    const timedOut = err && err.name === 'AbortError';
    apiEvents.emit('network-error');
    throw new ApiError(
      timedOut ? 'The server took too long to answer.' : "Can't reach the server. Check your connection or the server address in Settings.",
      0,
      { network: !timedOut, timeout: timedOut },
    );
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try {
    const text = await res.text();
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  apiEvents.emit('reachable');
  if (!res.ok) {
    const msg = detailOf(data, res.status);
    if (res.status === 401 && !quiet401) apiEvents.emit('unauthorized', msg);
    throw new ApiError(msg, res.status);
  }
  return data;
}

const TEN_MIN = 10 * 60 * 1000;
const enc = encodeURIComponent;

export const getStatus = (opts = {}) => api('/api/status', { timeout: 8000, ...opts });

export const listVoices = () => api('/api/voices').then((d) => d.voices || []);
export const previewVoice = (id, retake = false) =>
  api(`/api/voices/${enc(id)}/preview`, { method: 'POST', body: { retake }, timeout: TEN_MIN });
export const createVoice = (voice) => api('/api/voices', { method: 'POST', body: voice, timeout: 60000 });
export const deleteVoice = (id) => api(`/api/voices/${enc(id)}`, { method: 'DELETE' });

export const listBooks = () => api('/api/books', { timeout: 20000 }).then((d) => d.books || []);
export const getBook = (id) => api(`/api/books/${enc(id)}`);
export const patchBook = (id, body) => api(`/api/books/${enc(id)}`, { method: 'PATCH', body });
export const renderBook = (id, voiceId, pace, startPage = null) =>
  api(`/api/books/${enc(id)}/render`, { method: 'POST', body: { voice_id: voiceId, pace, ...(startPage ? { start_page: startPage } : {}) }, timeout: 60000 });
export const locate = (id, page) => api(`/api/books/${enc(id)}/locate?page=${encodeURIComponent(page)}`, { timeout: 15000 });
export const prioritize = (id, page) => api(`/api/books/${enc(id)}/prioritize`, { method: 'POST', body: { page }, timeout: 15000 });
export const pauseBook = (id) => api(`/api/books/${enc(id)}/pause`, { method: 'POST', body: {} });
export const deleteBook = (id) => api(`/api/books/${enc(id)}`, { method: 'DELETE' });

/**
 * Upload a PDF with XMLHttpRequest so we can show upload progress.
 * onProgress(fraction 0..1), onUploaded() once the bytes are sent (server is now parsing).
 */
export function uploadBook(file, { onProgress, onUploaded } = {}) {
  let xhr;
  const promise = new Promise((resolve, reject) => {
    xhr = new XMLHttpRequest();
    xhr.open('POST', apiBase() + '/api/books');
    const tk = getToken();
    if (tk) xhr.setRequestHeader('X-Access-Token', tk);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.timeout = 20 * 60 * 1000;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.upload.onload = () => onUploaded && onUploaded();
    xhr.onload = () => {
      let data = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && data) {
        resolve(data);
        return;
      }
      const msg = detailOf(data, xhr.status);
      if (xhr.status === 401) apiEvents.emit('unauthorized', msg);
      reject(new ApiError(msg, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Can't reach the server. The upload didn't go through.", 0, { network: true }));
    xhr.ontimeout = () => reject(new ApiError('The upload took too long and was stopped.', 0, { timeout: true }));
    xhr.onabort = () => reject(new ApiError('Upload cancelled.', 0));
    const fd = new FormData();
    fd.append('file', file, file.name || 'book.pdf');
    xhr.send(fd);
  });
  promise.abort = () => xhr && xhr.abort();
  return promise;
}
