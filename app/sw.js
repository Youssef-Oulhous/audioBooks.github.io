// AuK Audiobooks service worker.
// - App shell: cached at install (versioned cache), served cache-first and refreshed
//   in the background (stale-while-revalidate). Bump VERSION when shipping changes
//   to force a full re-download of the shell.
// - /api JSON: never touched (straight to the network, never cached, never POSTs).
// - Downloaded audio + covers ("audio-v1", filled by the page): served from cache,
//   with HTTP Range support (iOS Safari only plays media it can range-request).

const VERSION = '3.2.0';
const SHELL_CACHE = `auk-shell-${VERSION}`;
const AUDIO_CACHE = 'audio-v1';

const SHELL = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './manifest.webmanifest',
  './js/util.js',
  './js/icons.js',
  './js/config.js',
  './js/api.js',
  './js/state.js',
  './js/ui.js',
  './js/router.js',
  './js/offline.js',
  './js/player.js',
  './js/player-ui.js',
  './js/preview.js',
  './js/views/library.js',
  './js/views/newbook.js',
  './js/views/book.js',
  './js/views/settings.js',
  './js/views/stats.js',
  './js/stats.js',
  './js/water.js',
  './js/tabbar.js',
  './js/gotopage.js',
  './fonts/outfit-latin-wght-normal.woff2',
  './icons/icon.svg',
  './icons/icon-180.png',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
];

// chapter files, the whole-book stream and covers (MP3/M4B downloads are left alone)
const MEDIA_RE = /\/api\/books\/[^/]+\/(audio\/[^/]+\.m4a|stream\.m4a|cover\.jpg)$/;

self.addEventListener('install', (event) => {
  // Cache files one by one so a single missing file can't block installation.
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => Promise.all(SHELL.map((u) => cache.add(new Request(u, { cache: 'reload' })).catch(() => null))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) {
        if (key.startsWith('auk-shell-') && key !== SHELL_CACHE) await caches.delete(key);
      }
      await self.clients.claim();
    })(),
  );
});

/** Same normalization as cacheKey() in js/config.js: absolute, no token, no hash. */
function cacheKey(url) {
  const u = new URL(url);
  u.searchParams.delete('token');
  u.hash = '';
  return u.href;
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // never cache POST/PATCH/DELETE
  const url = new URL(req.url);

  if (MEDIA_RE.test(url.pathname)) {
    event.respondWith(fromMediaCache(req));
    return;
  }
  if (url.pathname.includes('/api/')) return; // API JSON: network only

  if (url.origin !== self.location.origin) return;
  const scope = new URL(self.registration.scope);
  if (!url.pathname.startsWith(scope.pathname)) return;

  if (req.mode === 'navigate') {
    // The app's own entry URL gets the shell (hash routing does the rest). Any other
    // page on the server (e.g. /docs) goes to the network, with the shell as offline fallback.
    const rel = url.pathname.slice(scope.pathname.length);
    if (rel === '' || rel === 'index.html') {
      event.respondWith(shell(new Request(scope.href), event, true));
    } else {
      event.respondWith(fetch(req).catch(() => shell(new Request(scope.href), event, true)));
    }
    return;
  }
  event.respondWith(shell(req, event, false));
});

async function shell(req, event, isNavigation) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(req, { ignoreSearch: true });
  const network = fetch(req.url, { cache: 'no-cache' })
    .then((res) => {
      if (res && res.ok && res.type === 'basic') cache.put(req, res.clone());
      return res;
    })
    .catch(() => null);
  if (hit) {
    event.waitUntil(network);
    return hit;
  }
  const res = await network;
  if (res) return res;
  if (isNavigation) {
    const fallback = await cache.match(new URL('./index.html', self.registration.scope).href);
    if (fallback) return fallback;
  }
  return new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } });
}

async function fromMediaCache(req) {
  let hit = null;
  try {
    const cache = await caches.open(AUDIO_CACHE);
    hit = await cache.match(cacheKey(req.url));
  } catch {
    hit = null;
  }
  if (!hit) {
    try {
      return await fetch(req);
    } catch {
      return Response.error();
    }
  }
  if (/cover\.jpg$/.test(new URL(req.url).pathname)) return hit;
  return rangeResponse(req, hit);
}

async function rangeResponse(req, cached) {
  const blob = await cached.blob();
  const size = blob.size;
  const type = cached.headers.get('Content-Type') || 'audio/mp4';
  const range = req.headers.get('Range');
  if (!range) {
    return new Response(blob, {
      status: 200,
      headers: { 'Content-Type': type, 'Content-Length': String(size), 'Accept-Ranges': 'bytes' },
    });
  }
  const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
  let start;
  let end;
  if (!m || (m[1] === '' && m[2] === '')) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
  }
  if (m[1] === '') {
    // suffix range: last N bytes
    start = Math.max(0, size - Number(m[2]));
    end = size - 1;
  } else {
    start = Number(m[1]);
    end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1);
  }
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
  }
  return new Response(blob.slice(start, end + 1, type), {
    status: 206,
    statusText: 'Partial Content',
    headers: {
      'Content-Type': type,
      'Content-Length': String(end - start + 1),
      'Content-Range': `bytes ${start}-${end}/${size}`,
      'Accept-Ranges': 'bytes',
    },
  });
}
