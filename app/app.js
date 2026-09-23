// AuK Audiobooks entry point: pairing, routing, player, service worker.

import { store } from './js/util.js';
import { consumePairing } from './js/config.js';
import { apiEvents } from './js/api.js';
import { state, bus, knownBook, refreshStatus } from './js/state.js';
import { startRouter, navigate, currentPath } from './js/router.js';
import { player } from './js/player.js';
import { mountPlayerUI } from './js/player-ui.js';
import { toast } from './js/ui.js';
import * as library from './js/views/library.js';
import * as newbook from './js/views/newbook.js';
import * as book from './js/views/book.js';
import * as settings from './js/views/settings.js';
import * as statsView from './js/views/stats.js';
import * as stats from './js/stats.js';
import { mountTabBar } from './js/tabbar.js';

const paired = consumePairing();

const routes = [
  { name: 'library', re: /^\/library\/?$/, view: library, keepScroll: true },
  { name: 'new', re: /^\/new\/?$/, view: newbook, params: () => ({ step: 'upload' }) },
  { name: 'new', re: /^\/new\/([^/]+)\/(chapters|voice)\/?$/, view: newbook, params: (m) => ({ id: decodeURIComponent(m[1]), step: m[2] }) },
  { name: 'book', re: /^\/book\/([^/]+)\/?$/, view: book, params: (m) => ({ id: decodeURIComponent(m[1]) }) },
  { name: 'stats', re: /^\/stats\/?$/, view: statsView },
  { name: 'settings', re: /^\/settings\/?$/, view: settings },
];

mountPlayerUI(document.getElementById('player-root'));
mountTabBar(document.getElementById('tabbar-root'));
stats.attachPlayer(player);
player.furthestHint = (b) => {
  const m = stats.merged().books[b.id];
  return m && !m.finished_at ? m.furthest || 0 : 0;
};
stats.start();
stats.events.on('finished', (id) => {
  const b = knownBook(id);
  if (b) toast(`Finished ${b.title}. Well done.`, { kind: 'ok', duration: 5000 });
});

// A 401 anywhere sends you to Settings with a friendly explanation.
apiEvents.on('unauthorized', (msg) => {
  state.authMessage = /token|code|auth/i.test(msg || '') ? 'Enter the access code for your server to continue.' : msg || 'Enter the access code for your server to continue.';
  if (!currentPath().startsWith('/settings')) navigate('#/settings');
});

// Restore the last book into the mini player (paused) so "continue listening" is one tap away.
function restoreLast() {
  if (player.book) return;
  const last = store.get('last');
  if (!last) return;
  const b = knownBook(last.bookId);
  if (!b) return;
  const rp = player.resumePoint(b);
  if (rp) player.prime(b, rp.chapter.index, rp.time);
}
restoreLast();
bus.on('books', restoreLast);

startRouter(routes, document.getElementById('view'));

if (paired) toast('Paired with your audiobook server.', { kind: 'ok' });

refreshStatus().then((s) => {
  if (s && s.auth_required && !s.authorized && !currentPath().startsWith('/settings')) {
    state.authMessage = 'This server needs an access code.';
    navigate('#/settings');
  }
});

// Top bars get a hairline once the page is scrolled.
let ticking = false;
window.addEventListener(
  'scroll',
  () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      document.documentElement.classList.toggle('scrolled', window.scrollY > 4);
      ticking = false;
    });
  },
  { passive: true },
);

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') bus.emit('visible');
});
window.addEventListener('online', () => bus.emit('online'));

if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js').catch(() => {
      /* offline support is optional */
    });
  });
}
