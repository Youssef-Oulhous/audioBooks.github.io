// Mini player bar (always at the bottom) + full "Now playing" sheet.

import { h, clock, long, throttle } from './util.js';
import { icon } from './icons.js';
import { player, RATES, SLEEP_OPTIONS, rateLabel, BACK_SECONDS, FORWARD_SECONDS } from './player.js';
import { actionSheet, toast, enableDragToClose, equalizer } from './ui.js';
import { waterCover } from './water.js';
import * as stats from './stats.js';
import { openGoToPage } from './gotopage.js';
import { state, bus } from './state.js';
import { mediaUrl } from './config.js';

export function mountPlayerUI(root) {
  /* ---------- mini bar ---------- */
  const miniArt = h('div', { class: 'mini-art' });
  const miniTitle = h('div', { class: 'mini-title' });
  const miniSub = h('div', { class: 'mini-sub' });
  const miniFill = h('i');
  const miniPlay = h('button', { class: 'icon-btn mini-play', type: 'button', 'aria-label': 'Play' }, icon('play', 26));
  const miniMain = h('button', { class: 'mini-main', type: 'button', 'aria-label': 'Open Now Playing' }, miniArt, h('div', { class: 'mini-text' }, miniTitle, miniSub));
  const mini = h('div', { class: 'mini', hidden: true }, h('div', { class: 'mini-progress', 'aria-hidden': 'true' }, miniFill), miniMain, miniPlay);

  miniMain.addEventListener('click', openSheet);
  miniPlay.addEventListener('click', () => player.toggle());

  /* ---------- now playing sheet ---------- */
  const npArt = h('div', { class: 'np-art' });
  const npPart = h('div', { class: 'np-part', hidden: true });
  const npChapter = h('h2', { class: 'np-chapter' });
  const npBook = h('div', { class: 'np-book' });
  const npPageText = h('span', { text: 'Go to page' });
  const npPage = h('button', { class: 'np-page', type: 'button', 'aria-label': 'Go to page' }, icon('book', 18), npPageText, icon('chevronRight', 14));
  npPage.addEventListener('click', () => player.book && openGoToPage(player.book));
  const scrub = h('input', { type: 'range', class: 'scrub', min: '0', max: '1000', step: '1', value: '0', 'aria-label': 'Position in chapter' });
  const tElapsed = h('span', { class: 'np-t' });
  const tRemain = h('span', { class: 'np-t' });
  const npPos = h('div', { class: 'np-pos' });
  const bigPlay = h('button', { class: 'np-play', type: 'button', 'aria-label': 'Play' }, icon('play', 36));
  const btnPrev = h('button', { class: 'icon-btn np-btn', type: 'button', 'aria-label': 'Previous chapter' }, icon('prevChapter', 28));
  const btnBack = h('button', { class: 'icon-btn np-btn', type: 'button', 'aria-label': `Back ${BACK_SECONDS} seconds` }, icon('back15', 32));
  const btnFwd = h('button', { class: 'icon-btn np-btn', type: 'button', 'aria-label': `Forward ${FORWARD_SECONDS} seconds` }, icon('fwd30', 32));
  const btnNext = h('button', { class: 'icon-btn np-btn', type: 'button', 'aria-label': 'Next chapter' }, icon('nextChapter', 28));
  const speedLabel = h('span', { class: 'x-label' });
  const sleepLabel = h('span', { class: 'x-label' });
  const btnSpeed = h('button', { class: 'np-x', type: 'button', 'aria-label': 'Playback speed' }, h('span', { class: 'x-big' }, speedLabel), h('span', { class: 'x-cap', text: 'Speed' }));
  const btnSleep = h('button', { class: 'np-x', type: 'button', 'aria-label': 'Sleep timer' }, h('span', { class: 'x-big' }, icon('moon', 20), sleepLabel), h('span', { class: 'x-cap', text: 'Sleep timer' }));
  const btnChapters = h('button', { class: 'np-x', type: 'button', 'aria-label': 'Show chapters' }, h('span', { class: 'x-big' }, icon('list', 20)), h('span', { class: 'x-cap', text: 'Chapters' }));
  const btnClose = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Close Now Playing' }, icon('chevronDown', 28));
  const npDl = h('a', { class: 'np-dl', download: '', rel: 'noopener', hidden: true }, icon('download', 18), h('span', { text: 'Download this chapter (MP3)' }));
  const npWaitText = h('span');
  const npWait = h('div', { class: 'np-wait', hidden: true, 'aria-live': 'polite' }, h('span', { class: 'spinner sm' }), npWaitText);
  const npTop = h('div', { class: 'np-top' }, btnClose, h('div', { class: 'np-kicker', text: 'Now playing' }), h('div', { class: 'np-top-eq' }));
  const eq = equalizer();
  npTop.lastElementChild.append(eq);

  const np = h(
    'div',
    { class: 'np', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Now playing', 'aria-hidden': 'true' },
    h(
      'div',
      { class: 'np-inner' },
      npTop,
      npArt,
      h('div', { class: 'np-meta' }, npPart, npChapter, npBook, npPage),
      h('div', { class: 'np-scrub' }, scrub, h('div', { class: 'np-times' }, tElapsed, tRemain)),
      npPos,
      npWait,
      h('div', { class: 'np-transport' }, btnPrev, btnBack, bigPlay, btnFwd, btnNext),
      h('div', { class: 'np-extras' }, btnSpeed, btnSleep, btnChapters),
      npDl,
    ),
  );
  root.append(mini, np);

  bigPlay.addEventListener('click', () => player.toggle());
  btnPrev.addEventListener('click', () => player.prev());
  btnNext.addEventListener('click', () => player.next());
  btnBack.addEventListener('click', () => player.seekBy(-BACK_SECONDS));
  btnFwd.addEventListener('click', () => player.seekBy(FORWARD_SECONDS));
  btnSpeed.addEventListener('click', () => player.cycleRate());
  btnSleep.addEventListener('click', openSleep);
  btnChapters.addEventListener('click', goToChapters);
  btnClose.addEventListener('click', closeSheet);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && np.classList.contains('open')) closeSheet();
  });
  enableDragToClose(np, npTop, closeSheet);
  enableDragToClose(np, npArt, closeSheet);

  let scrubbing = false;
  scrub.addEventListener('input', () => {
    scrubbing = true;
    const d = player.duration;
    const t = (scrub.value / 1000) * d;
    paintScrub(t, d);
  });
  scrub.addEventListener('change', () => {
    player.seekTo((scrub.value / 1000) * player.duration);
    scrubbing = false;
  });
  const endScrub = () => setTimeout(() => (scrubbing = false), 50);
  scrub.addEventListener('pointerup', endScrub);
  scrub.addEventListener('touchend', endScrub);

  function openSheet() {
    if (!player.book) return;
    np.classList.add('open');
    np.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('np-open');
    renderAll();
    setTimeout(() => bigPlay.focus({ preventScroll: true }), 50);
  }
  function closeSheet() {
    np.classList.remove('open');
    np.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('np-open');
  }

  function goToChapters() {
    const b = player.book;
    if (!b) return;
    closeSheet();
    state.scrollToCurrent = true;
    const target = '#/book/' + encodeURIComponent(b.id);
    if (location.hash !== target) location.hash = target;
    else bus.emit('scroll-to-current');
  }

  function openSleep() {
    const cur = player.sleep.mode;
    actionSheet({
      title: 'Sleep timer',
      message: 'Playback pauses when the timer runs out.',
      actions: SLEEP_OPTIONS.map((o) => ({
        label: o.label,
        icon: o.value === 'off' ? 'close' : o.value === 'chapter' ? 'book' : 'moon',
        checked: cur === o.value,
        onSelect: () => {
          player.setSleep(o.value);
          if (o.value !== 'off') toast(o.value === 'chapter' ? 'Will pause at the end of this chapter' : `Will pause in ${o.label}`, { kind: 'ok' });
        },
      })),
    });
  }

  /* ---------- rendering ---------- */

  function paintScrub(t, d) {
    const pct = d > 0 ? Math.min(100, (t / d) * 100) : 0;
    scrub.style.setProperty('--pct', pct + '%');
    tElapsed.textContent = clock(t);
    tRemain.textContent = '−' + clock(Math.max(0, d - t));
  }

  let coverFor = null;
  let miniCover = null;
  let npCover = null;
  function refreshWater() {
    const b = player.book;
    if (!b || !miniCover) return;
    const p = stats.progress(b);
    miniCover.setFill(p);
    npCover.setFill(p);
  }

  function renderTrack() {
    lastPage = undefined;
    const b = player.book;
    const has = !!b;
    mini.hidden = !has;
    document.body.classList.toggle('has-mini', has);
    if (!has) {
      closeSheet();
      return;
    }
    const ch = player.chapter;
    if (coverFor !== b.id) {
      coverFor = b.id;
      miniCover = waterCover(b, { pct: stats.progress(b), size: 'xs', rise: false });
      npCover = waterCover(b, { pct: stats.progress(b), size: 'np' });
      miniArt.replaceChildren(miniCover);
      npArt.replaceChildren(npCover);
    } else {
      miniCover.setBook(b);
      npCover.setBook(b);
      refreshWater();
    }
    const part = (ch && ch.part) || null;
    // the mini player has one line: "Part II: Instruments" becomes "Part II" there
    const shortPart = part && part.length > 12 && /[:—–-]/.test(part) ? part.split(/\s*[:—–]\s*|\s+-\s+/)[0] : part;
    miniTitle.textContent = [shortPart, (ch && ch.title) || b.title].filter(Boolean).join(' · ');
    npPart.hidden = !part;
    npPart.textContent = part || '';
    npChapter.textContent = (ch && ch.title) || b.title;
    npBook.textContent = [b.title, b.author].filter(Boolean).join(' · ');
    const mp3 = ch && ch.mp3_url && navigator.onLine !== false ? mediaUrl(ch.mp3_url) : null;
    npDl.hidden = !mp3;
    if (mp3) npDl.href = mp3;
    const info = player.bookInfo();
    btnPrev.disabled = false;
    btnNext.disabled = !info || info.pos >= info.total;
    renderTime();
    renderState();
  }

  function renderState() {
    const playing = player.playing;
    const lbl = playing ? 'Pause' : 'Play';
    miniPlay.replaceChildren(icon(playing ? 'pause' : 'play', 26));
    miniPlay.setAttribute('aria-label', lbl);
    bigPlay.replaceChildren(icon(playing ? 'pause' : 'play', 36));
    bigPlay.setAttribute('aria-label', lbl);
    bigPlay.classList.toggle('is-loading', playing && player.stalled);
    miniPlay.classList.toggle('is-loading', playing && player.stalled);
    eq.classList.toggle('paused', !playing);
    mini.classList.toggle('is-playing', playing);
    document.body.classList.toggle('is-playing', playing);
    npWait.hidden = !player.waiting;
    npWaitText.textContent = player.waiting === 'part' ? 'Preparing the next part…' : 'The next chapter is still being voiced. It will start on its own.';
    renderSub();
    renderPage();
  }

  let lastPage = undefined;
  function renderPage() {
    const b = player.book;
    const page = b ? player.currentPage() : null;
    if (page === lastPage) return;
    lastPage = page;
    npPageText.textContent = page ? `Page ${page}` : 'Go to page';
    npPage.setAttribute('aria-label', page ? `Page ${page}. Go to another page` : 'Go to page');
    npPage.hidden = !(b && b.pages);
    renderSub();
  }

  function renderSub() {
    const b = player.book;
    if (!b) return;
    if (player.waiting) {
      miniSub.textContent = player.waiting === 'part' ? 'Preparing the next part…' : 'Waiting for the next chapter…';
      return;
    }
    const d = player.duration;
    const left = player.loaded && player.audio.ended ? 'Chapter finished' : d ? clock(Math.max(0, d - player.time)) + ' left' : '';
    const page = player.currentPage();
    miniSub.textContent = [page ? `Page ${page}` : b.title, left].filter(Boolean).join(' · ');
  }

  let lastSub = 0;
  function renderTime() {
    if (!player.book) return;
    const d = player.duration;
    const t = player.time;
    mini.style.setProperty('--p', String(d ? Math.min(1, t / d) : 0));
    renderPage();
    const now = Date.now();
    if (now - lastSub > 900) {
      lastSub = now;
      renderSub();
    }
    if (!np.classList.contains('open')) return;
    if (!scrubbing) {
      scrub.value = d ? String(Math.round((t / d) * 1000)) : '0';
      paintScrub(t, d);
    }
    const info = player.bookInfo();
    if (info && info.pos > 0) {
      npPos.textContent = `Chapter ${info.pos} of ${info.total} · ${long(info.left)} left in book`;
    } else {
      npPos.textContent = '';
    }
  }

  function renderRate() {
    speedLabel.textContent = rateLabel(player.rate);
    btnSpeed.setAttribute('aria-label', `Playback speed ${rateLabel(player.rate)}. Tap to change.`);
    btnSpeed.classList.toggle('on', player.rate !== 1);
  }

  function renderSleep() {
    const m = player.sleep.mode;
    let txt = 'Off';
    if (m === 'chapter') txt = 'Chapter end';
    else if (typeof m === 'number') txt = clock(player.sleepLeft() / 1000);
    sleepLabel.textContent = txt;
    btnSleep.classList.toggle('on', m !== 'off');
    btnSleep.setAttribute('aria-label', m === 'off' ? 'Sleep timer: off' : `Sleep timer: ${txt}`);
  }

  function renderAll() {
    renderTrack();
    renderRate();
    renderSleep();
  }

  player.on('track', renderTrack);
  player.on('time', throttle(refreshWater, 5000));
  stats.events.on('change', refreshWater);
  player.on('book', renderTrack);
  player.on('state', renderState);
  player.on('time', renderTime);
  player.on('rate', () => {
    renderRate();
    renderTime();
  });
  player.on('sleep', renderSleep);
  player.on('sleeptick', renderSleep);
  player.on('slept', () => toast('Sleep timer ended. Playback paused.', { kind: 'info' }));
  player.on('notice', (msg) => toast(msg));
  player.on('error', (msg) => toast(msg, { kind: 'error', duration: 5000 }));
  player.on('blocked', () => toast('Tap play to start listening.'));
  player.on('finished', () => toast('You’ve reached the end of the book.', { kind: 'ok' }));

  renderAll();
  return { openSheet, closeSheet };
}

export { RATES };
