// Book screen: cover, render progress, the chapter list (main navigation), actions.

import { h, fill, clock, long, plural, throttle, store } from '../util.js';
import { icon } from '../icons.js';
import * as api from '../api.js';
import { mediaUrl } from '../config.js';
import { state, bus, rememberBook, forgetBook, knownBook, refreshStatus } from '../state.js';
import { callout, progressBar, spinner, toast, actionSheet, confirmSheet, topbar, backButton, iconButton, equalizer } from '../ui.js';
import { waterCover, creationInfo } from '../water.js';
import * as stats from '../stats.js';
import { player, includedChapters, playableChapters, chapterPlayable, readyParts } from '../player.js';
import { openGoToPage, goToPage, pendingStart } from '../gotopage.js';
import * as offline from '../offline.js';
import { navigate, goBack } from '../router.js';
import { paceLabel } from './newbook.js';

const ACTIVE = new Set(['queued', 'rendering']);

export function mount(root, { id }) {
  let alive = true;
  let timer = null;
  let book = knownBook(id);
  let fromSnapshot = !!book;
  const rows = new Map(); // chapter index -> row api
  const groups = new Map(); // part title -> part group api
  const openSections = new Set(); // chapter indexes showing their sections
  let rowsSig = '';
  let renderEls = null; // live elements of the "creating audio" card
  let currentIdx = null; // chapter index highlighted as playing
  let filesKey = ''; // what the downloads strip currently shows
  let heroCover = null; // persistent water cover (updated in place, never re-created on polls)
  const heroText = h('div', { class: 'hero-text' });

  const moreBtn = iconButton('more', 'Book actions', () => openActions());
  const barTitle = h('span', { class: 'bar-title' });
  const header = topbar({ left: backButton('Library', () => goBack('#/library')), title: barTitle, right: moreBtn });
  const hero = h('section', { class: 'hero' });
  const primary = h('div', { class: 'primary-slot' });
  const renderCard = h('div', { class: 'render-slot' });
  const filesSlot = h('div', { class: 'files-slot' });
  const offlineSlot = h('div', { class: 'offline-slot' });
  const chHead = h('div', { class: 'section-head ch-head' }, h('h2', { class: 'section-title', text: 'Chapters' }), h('span', { class: 'section-meta' }));
  const list = h('div', { class: 'ch-list', role: 'list' });
  const voiceSlot = h('div', { class: 'offline-slot' });
  const page = h('div', { class: 'page page-book' }, hero, voiceSlot, primary, filesSlot, renderCard, offlineSlot, chHead, list);
  root.append(header, page);

  // Part headers square off against the top bar while they are parked under it.
  let stickyIo = null;
  function watchSticky(sentinel) {
    if (!('IntersectionObserver' in window)) return;
    if (!stickyIo) {
      const top = Math.round(header.getBoundingClientRect().height) || 52;
      stickyIo = new IntersectionObserver(
        (entries) => {
          for (const e of entries) e.target.parentElement.classList.toggle('stuck', !e.isIntersecting && e.boundingClientRect.top < top);
        },
        { rootMargin: `-${top}px 0px 0px 0px`, threshold: 0 },
      );
    }
    stickyIo.observe(sentinel);
  }

  // Title fades into the top bar once the hero title scrolls away.
  const io = 'IntersectionObserver' in window ? new IntersectionObserver(([e]) => header.classList.toggle('show-title', !e.isIntersecting), { rootMargin: '-60px 0px 0px 0px' }) : null;

  if (book) renderAll();
  else page.prepend(h('div', { class: 'loading-row' }, spinner(), h('span', { text: 'Loading book…' })));

  /* ---------- rendering ---------- */

  function renderAll() {
    page.querySelector(':scope > .loading-row')?.remove();
    barTitle.textContent = book.title;
    renderHero();
    renderPrimary();
    renderRenderCard();
    renderFiles();
    renderOffline();
    renderChapters();
  }

  /** Finished book: MP3 / zip / m4b downloads, right under the play button. */
  function renderFiles() {
    const ready = book.status === 'ready' && !state.offline;
    const k = ready ? [book.mp3_url, book.mp3_zip_url, book.m4b_url].join('|') : '';
    if (k === filesKey) return;
    filesKey = k;
    if (!ready || !(book.mp3_url || book.mp3_zip_url || book.m4b_url)) {
      filesSlot.replaceChildren();
      return;
    }
    const tile = (url, ic, main, sub, name, cls = '') =>
      url ? h('a', { class: `file-tile ${cls}`, href: mediaUrl(url), download: name || '', rel: 'noopener' }, icon(ic, 24), h('span', { class: 'file-tile-text' }, h('span', { class: 'file-main', text: main }), h('span', { class: 'file-sub', text: sub }))) : null;
    fill(
      filesSlot,
      h(
        'section',
        { class: 'files' },
        h('div', { class: 'files-head' }, h('h2', { class: 'section-title', text: 'Download' }), h('span', { class: 'files-hint', text: 'Saved to Files, in Downloads' })),
        h(
          'div',
          { class: 'file-tiles' },
          tile(book.mp3_url, 'download', 'Whole book', 'One MP3 with chapter marks', '', 'primary'),
          tile(book.mp3_zip_url, 'zip', 'MP3 chapters', 'One per chapter, zip'),
          tile(book.m4b_url, 'book', 'Apple Books', 'm4b with chapters', `${safeName(book.title)}.m4b`),
        ),
      ),
    );
  }

  function heroProgress() {
    if (book.status !== 'ready') return '';
    const pct = stats.progress(book);
    return pct >= 100 ? 'Finished' : pct > 0 ? `${pct}% listened` : '';
  }

  function renderHero() {
    const inc = includedChapters(book);
    const total = book.status === 'ready' ? book.audio_seconds : book.est_seconds / (book.pace || 1);
    const info = creationInfo(book);
    if (!heroCover) {
      heroCover = waterCover(book, { pct: stats.progress(book), size: 'lg', creating: info });
      hero.replaceChildren(heroCover, heroText);
    } else {
      heroCover.setBook(book);
      heroCover.setCreating(info);
      if (!info) heroCover.setFill(stats.progress(book));
    }
    const title = h('h1', { class: 'hero-title', text: book.title });
    const prog = heroProgress();
    fill(
      heroText,
      title,
      book.author ? h('div', { class: 'hero-author', text: book.author }) : null,
      book.voice_name ? h('div', { class: 'hero-voice' }, icon('wave', 18), h('span', { text: `${book.voice_name}, ${paceLabel(book.pace || 1).toLowerCase()} pace` })) : null,
      h('div', { class: 'hero-meta', text: `${plural(inc.length, 'chapter')} · ${book.status === 'ready' ? '' : 'about '}${long(total)}` }),
      prog ? h('div', { class: 'hero-progress', text: prog }) : null,
    );
    if (io) {
      io.disconnect();
      io.observe(title);
    }
  }

  function refreshProgress() {
    if (!book || !heroCover || creationInfo(book)) return;
    heroCover.setFill(stats.progress(book));
    const el = heroText.querySelector('.hero-progress');
    const prog = heroProgress();
    if (el) el.textContent = prog;
    else if (prog) heroText.append(h('div', { class: 'hero-progress', text: prog }));
  }

  function renderPrimary() {
    const list = playableChapters(book);
    const isCurrent = player.book && player.book.id === book.id;
    const playing = isCurrent && player.playing;
    let label;
    let sub = '';
    if (!list.length) {
      label = book.status === 'draft' ? 'Finish setting up' : 'Nothing to play yet';
      sub = book.status === 'rendering' || book.status === 'queued' ? 'The first part is being voiced, usually under a minute' : '';
    } else if (playing) {
      label = 'Pause';
    } else {
      const rp = player.resumePoint(book);
      const inc = includedChapters(book);
      if (rp && (rp.started || isCurrent)) {
        label = 'Continue listening';
        sub = `Chapter ${inc.indexOf(rp.chapter) + 1} · ${rp.time > 1 ? clock(rp.time) : 'from the start'}`;
      } else {
        label = 'Play from the beginning';
        sub = list[0] && inc.indexOf(list[0]) > 0 ? `Starts at chapter ${inc.indexOf(list[0]) + 1}` : '';
      }
    }
    const btn = h(
      'button',
      { class: 'btn btn-primary btn-block btn-play', type: 'button', disabled: !list.length && book.status !== 'draft' },
      icon(playing ? 'pause' : 'play', 24),
      h('span', { class: 'btn-play-text' }, h('span', { text: label }), sub ? h('small', { text: sub }) : null),
    );
    btn.addEventListener('click', () => {
      if (book.status === 'draft' && !list.length) {
        navigate(`#/new/${encodeURIComponent(book.id)}/chapters`);
        return;
      }
      if (isCurrent) player.toggle();
      else player.resumeBook(book);
    });
    const canGo = book.pages && book.status !== 'draft';
    const goto = canGo ? h('button', { class: 'btn btn-secondary btn-sm goto-btn', type: 'button', onclick: () => openGoToPage(book) }, icon('book', 20), h('span', { text: 'Go to page' })) : null;
    fill(primary, btn, goto);
  }

  function renderRenderCard() {
    const st = book.status;
    if (st === 'ready') {
      renderCard.replaceChildren();
      renderEls = null;
      return;
    }
    const p = book.progress || {};
    const pct = p.percent || 0;
    if (ACTIVE.has(st)) {
      if (!renderEls || renderEls.kind !== 'active') {
        const bar = progressBar(pct, 'bar-lg');
        const pctEl = h('span', { class: 'rc-pct' });
        const label = h('span', { class: 'rc-label' });
        const eta = h('div', { class: 'rc-eta' });
        const now = h('div', { class: 'rc-now' });
        const pauseBtn = h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onclick: pauseRender }, icon('pause', 18), h('span', { text: 'Pause creation' }));
        const card = h('div', { class: 'card render-card' }, h('div', { class: 'rc-top' }, label, pctEl), bar, eta, now, h('div', { class: 'rc-actions' }, pauseBtn));
        renderCard.replaceChildren(card);
        renderEls = { kind: 'active', bar, pctEl, label, eta, now };
      }
      const r = renderEls;
      r.bar.set(pct);
      r.pctEl.textContent = st === 'queued' ? '' : `${Math.floor(pct)}%`;
      if (st === 'queued') {
        const q = (state.status && state.status.queue) || [];
        const ahead = q.indexOf(book.id);
        if (r.st !== st) r.label.replaceChildren(spinner('sm'), h('span', { text: 'Waiting to start' }));
        r.eta.textContent = ahead > 0 ? `${plural(ahead, 'book')} ahead of this one` : 'Starting soon…';
        r.now.textContent = '';
      } else {
        if (r.st !== st) r.label.replaceChildren(spinner('sm'), h('span', { text: 'Creating audio' }));
        r.eta.textContent = p.eta_seconds == null ? 'Estimating time left…' : p.eta_seconds < 60 ? 'Less than a minute left' : `About ${long(p.eta_seconds)} left`;
        const inc = includedChapters(book);
        const cur = inc.find((c) => c.index === p.current_chapter) || inc.find((c) => c.status === 'rendering');
        const listenable = playableChapters(book).length > 0;
        if (p.stage === 'packaging') r.now.textContent = 'All chapters are voiced. Building the whole-book files.';
        else r.now.textContent = (cur ? `“${cur.title || `Chapter ${inc.indexOf(cur) + 1}`}” is being voiced now.` : '') + (listenable ? ' You can listen to what’s ready.' : '');
      }
      r.st = st;
      return;
    }
    renderEls = null;
    let card;
    if (st === 'paused') {
      card = h(
        'div',
        { class: 'card render-card paused' },
        h('div', { class: 'rc-top' }, h('span', { class: 'rc-label' }, icon('pause', 18), h('span', { text: 'Creation paused' })), h('span', { class: 'rc-pct', text: `${Math.floor(pct)}%` })),
        progressBar(pct, 'bar-lg muted'),
        h('div', { class: 'rc-eta', text: book.voice_available === false ? 'This book’s voice isn’t available with the engine your server runs now. Resume to choose another voice.' : 'Chapters that are already done can be played.' }),
        h('div', { class: 'rc-actions' }, h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: resumeRender }, icon('play', 18), h('span', { text: 'Resume creation' })), editLink()),
      );
    } else if (st === 'error') {
      card = h(
        'div',
        { class: 'card render-card error' },
        h('div', { class: 'rc-top' }, h('span', { class: 'rc-label' }, icon('alert', 18), h('span', { text: 'Creation stopped' }))),
        h('div', { class: 'rc-error', text: book.error || 'Something went wrong on the server.' }),
        h('div', { class: 'rc-actions' }, h('button', { class: 'btn btn-primary btn-sm', type: 'button', onclick: resumeRender }, icon('refresh', 18), h('span', { text: 'Try again' })), editLink()),
      );
    } else if (st === 'draft') {
      card = h(
        'div',
        { class: 'card render-card' },
        h('div', { class: 'rc-top' }, h('span', { class: 'rc-label' }, icon('info', 18), h('span', { text: 'Not set up yet' }))),
        h('div', { class: 'rc-eta', text: 'Check the chapters and choose a voice to start.' }),
        h('div', { class: 'rc-actions' }, h('a', { class: 'btn btn-primary btn-sm', href: `#/new/${encodeURIComponent(book.id)}/chapters`, text: 'Finish setup' })),
      );
    }
    renderCard.replaceChildren(card || '');
  }

  function editLink() {
    return h('a', { class: 'btn btn-ghost btn-sm', href: `#/new/${encodeURIComponent(book.id)}/chapters` }, icon('pencil', 16), h('span', { text: 'Edit' }));
  }

  function renderOffline() {
    offlineSlot.replaceChildren();
    voiceSlot.replaceChildren();
    // audio made by the demo engine (placeholder tones) or with a voice the current engine doesn't have
    const eng = state.status && state.status.engine;
    const demoAudio = book.made_with === 'demo' && !(eng && eng.is_demo);
    if (book.status === 'ready' && (demoAudio || book.voice_available === false)) {
      voiceSlot.append(
        callout(
          'warn',
          h('span', {}, h('strong', { text: demoAudio ? 'Placeholder tones, not a real voice.' : 'This voice isn’t available anymore.' }), demoAudio ? ' This book was created in demo mode. Narrate it again with a real voice.' : ' Pick one of the current voices to narrate it again.'),
          h('div', { class: 'callout-actions' }, h('a', { class: 'btn btn-primary btn-sm', href: `#/new/${encodeURIComponent(book.id)}/voice`, text: 'Choose a voice' })),
        ),
      );
    }
    if (state.offline && offline.status(book).state !== 'full') offlineSlot.append(callout('offline', h('span', {}, h('strong', { text: 'Offline.' }), ' Only downloaded chapters can be played.')));
    const job = offline.downloading(book.id);
    if (job) {
      const bar = progressBar(job.fraction * 100, 'bar-thin');
      offlineSlot.append(
        h(
          'div',
          { class: 'dl-row' },
          h('span', { class: 'dl-ic' }, icon('download', 18)),
          h('div', { class: 'dl-text' }, h('div', { text: job.stream ? `Saving the book for offline · ${Math.floor(job.fraction * 100)}%` : job.total ? `Downloading ${Math.min(job.done + 1, job.total)} of ${job.total} · ${Math.floor(job.fraction * 100)}%` : 'Preparing download…' }), bar),
          h('button', { class: 'link-btn', type: 'button', text: 'Cancel', onclick: () => job.abort() }),
        ),
      );
      return;
    }
    const st = offline.status(book);
    if (st.state === 'full') {
      offlineSlot.append(h('div', { class: 'badge-row' }, h('span', { class: 'badge badge-ok' }, icon('downloaded', 16), h('span', { text: 'Available offline' }))));
    } else if (st.state === 'partial') {
      offlineSlot.append(
        h(
          'div',
          { class: 'badge-row' },
          h('span', { class: 'badge' }, icon('downloaded', 16), h('span', { text: `${st.have} of ${includedChapters(book).length} chapters offline` })),
          book.stream_url || st.have < st.total ? h('button', { class: 'link-btn', type: 'button', text: book.stream_url ? 'Download finished book' : 'Download new chapters', onclick: startDownload }) : null,
        ),
      );
    }
  }

  /** "pages 19–43", "page 19", or the printed label of a front-matter page. */
  function pagesText(first, last, label) {
    if (first == null && last == null) return label ? `page ${label}` : '';
    if (first != null && last != null && last > first) return `pages ${first}–${last}`;
    const one = first != null ? first : last;
    return one != null ? `page ${one}` : '';
  }

  /** Which part should be open when the list is built (the one being listened to). */
  function partInPlay() {
    const here = player.book && player.book.id === book.id ? book.chapters.find((c) => c.index === player.index) : null;
    if (here && here.part) return here.part;
    const rp = player.resumePoint(book);
    return (rp && rp.chapter && rp.chapter.part) || null;
  }

  function partsOpen() {
    return store.get('parts:' + book.id, {}) || {};
  }

  function setPartOpen(title, open) {
    store.set('parts:' + book.id, { ...partsOpen(), [title]: open });
  }

  function partGroup(title, openByDefault) {
    const saved = partsOpen();
    let open = title in saved ? !!saved[title] : openByDefault;
    const name = h('span', { class: 'part-name', text: title });
    const meta = h('span', { class: 'part-meta' });
    const right = h('span', { class: 'part-right' });
    const chev = h('span', { class: 'part-chev' }, icon('chevronDown', 20));
    const head = h('button', { class: 'part-head', type: 'button' }, chev, h('span', { class: 'part-main' }, name, meta), right);
    const body = h('div', { class: 'part-body', role: 'list' });
    const sentinel = h('div', { class: 'part-sentinel', 'aria-hidden': 'true' });
    const el = h('section', { class: 'part-group' }, sentinel, head, body);
    const g = {
      el,
      body,
      chapters: [],
      paint() {
        head.setAttribute('aria-expanded', String(open));
        head.setAttribute('aria-label', `${title}, ${meta.textContent}${right.getAttribute('aria-label') ? ', ' + right.getAttribute('aria-label') : ''}. ${open ? 'Hide' : 'Show'} its chapters`);
        el.classList.toggle('closed', !open);
        body.hidden = !open;
      },
      open: () => open,
      setOpen(v) {
        open = v;
        g.paint();
      },
      update() {
        const inc = g.chapters.filter((c) => c.include !== false);
        const first = inc.find((c) => c.first_page != null);
        const lastCh = [...inc].reverse().find((c) => c.last_page != null);
        const label = inc[0] && inc[0].first_page_label;
        const pages = pagesText(first ? first.first_page : null, lastCh ? lastCh.last_page : null, label);
        const secs = inc.reduce((n, c) => n + (c.status === 'ready' ? c.duration || 0 : (c.est_seconds || 0) / (book.pace || 1)), 0);
        meta.textContent = [pages, `${plural(inc.length, 'chapter')} · ${long(secs)}`].filter(Boolean).join(' · ');
        const ready = inc.filter((c) => c.status === 'ready').length;
        const done = ready === inc.length;
        if (book.status === 'ready' || !inc.length) right.replaceChildren();
        else if (done) right.replaceChildren(icon('check', 16));
        else right.replaceChildren(h('span', { text: `${ready}/${inc.length}` }));
        right.setAttribute('aria-label', book.status === 'ready' ? '' : done ? 'all chapters voiced' : `${ready} of ${inc.length} chapters ready`);
        right.classList.toggle('done', done);
        g.paint();
      },
    };
    head.addEventListener('click', () => {
      g.setOpen(!open);
      setPartOpen(title, open);
    });
    watchSticky(sentinel);
    return g;
  }

  function renderChapters() {
    const inc = includedChapters(book);
    const ready = inc.filter((c) => c.status === 'ready');
    const meta = chHead.querySelector('.section-meta');
    meta.textContent =
      book.status === 'ready' ? `${inc.length} · ${long(book.audio_seconds)}` : `${ready.length} of ${inc.length} ready`;

    const sorted = [...book.chapters].sort((a, b) => a.index - b.index);
    const hasParts = Array.isArray(book.parts) && book.parts.length > 0;
    const sig = sorted.map((c) => `${c.index}:${c.include !== false}:${c.part || ''}:${(c.sections || []).length}`).join(',');
    if (sig !== rowsSig) {
      rowsSig = sig;
      currentIdx = null;
      rows.clear();
      groups.clear();
      if (stickyIo) stickyIo.disconnect();
      list.replaceChildren();
      list.classList.toggle('has-parts', hasParts);
      const playing = partInPlay();
      const nParts = hasParts ? new Set(sorted.filter((c) => c.part).map((c) => c.part)).size : 0;
      let n = 0;
      let current = null;
      for (const c of sorted) {
        const num = c.include !== false ? ++n : null;
        const r = chapterRow(c, num);
        rows.set(c.index, r);
        const part = hasParts ? c.part || null : null;
        if (!part) current = null;
        else if (!current || current.title !== part) {
          // a short book keeps every part open; a long one opens the part being listened to
          current = { title: part, g: partGroup(part, nParts <= 3 || part === playing) };
          groups.set(part, current.g);
          list.append(current.g.el);
        }
        (current ? current.g.body : list).append(r.wrap);
        if (current) current.g.chapters.push(c);
      }
    }
    const pos = player.getPos(book.id) || {};
    const done = new Set(pos.done || []);
    for (const c of sorted) rows.get(c.index).update(c, pos, done);
    for (const g of groups.values()) {
      g.chapters = g.chapters.map((c) => sorted.find((x) => x.index === c.index) || c);
      g.update();
    }
    paintCurrent();
  }

  function chapterRow(c, num) {
    const numEl = h('span', { class: 'ch-num' });
    const titleEl = h('span', { class: 'ch-title' });
    const sub = h('span', { class: 'ch-sub' });
    const right = h('span', { class: 'ch-right' });
    const hair = h('span', { class: 'ch-hair', 'aria-hidden': 'true' });
    const el = h('button', { class: 'ch', type: 'button', dataset: { index: String(c.index) } }, numEl, h('span', { class: 'ch-main' }, titleEl, sub), right, hair);
    const dl = h('a', { class: 'ch-dl', download: '', rel: 'noopener', hidden: true, title: 'Download this chapter (MP3)' }, icon('download', 20));
    const exp = h('button', { class: 'ch-exp', type: 'button', hidden: true }, icon('chevronDown', 20));
    const secList = h('div', { class: 'sec-list', hidden: true });
    const line = h('div', { class: 'ch-line' }, el, dl, exp);
    const wrap = h('div', { class: 'ch-row', role: 'listitem' }, line, secList);
    let secKey = '';
    const paintSections = (ch) => {
      const secs = ch.sections || [];
      const open = openSections.has(ch.index) && secs.length > 0;
      exp.hidden = !secs.length;
      exp.setAttribute('aria-expanded', String(open));
      exp.setAttribute('aria-label', `${open ? 'Hide' : 'Show'} the ${secs.length} sections of ${ch.title}`);
      exp.classList.toggle('open', open);
      const k = `${open}|${secs.map((x) => `${x.title}@${x.page ?? x.page_label ?? ''}`).join('|')}`;
      if (k !== secKey) {
        secKey = k;
        secList.replaceChildren(
          ...secs.map((sec) => {
            // pages the book labels in roman have no number, but their label works just as well
            const label = sec.page_label && String(sec.page_label) !== String(sec.page) ? sec.page_label : sec.page;
            const pageEl = h('span', { class: 'sec-page', text: label != null ? String(label) : '' });
            const ask = Number.isInteger(sec.page) ? sec.page : sec.page_label || null;
            const canGo = ask != null;
            const row = h(canGo ? 'button' : 'div', { class: `sec${canGo ? '' : ' sec-flat'}`, ...(canGo ? { type: 'button' } : {}) }, h('span', { class: 'sec-title', text: sec.title }), pageEl);
            if (canGo) {
              row.setAttribute('aria-label', `${sec.title}, page ${label}. Play from here`);
              row.addEventListener('click', () => {
                player.unlock(); // inside the tap, so playback may start after a wait
                goToPage(book, ask);
              });
            }
            return row;
          }),
        );
      }
      secList.hidden = !open;
      el.classList.toggle('has-exp', secs.length > 0);
    };
    exp.addEventListener('click', () => {
      const ch = book.chapters.find((x) => x.index === c.index) || c;
      if (openSections.has(c.index)) openSections.delete(c.index);
      else openSections.add(c.index);
      paintSections(ch);
    });
    el.addEventListener('click', () => {
      const ch = book.chapters.find((x) => x.index === c.index);
      if (!ch || !chapterPlayable(ch)) return;
      if (state.offline && !offline.isChapterOffline(book, ch)) {
        toast('This chapter isn’t downloaded, and the server can’t be reached.', { kind: 'error' });
        return;
      }
      player.playChapter(book, ch.index);
    });
    let key = '';
    const r = {
      el,
      wrap,
      num,
      hair,
      numEl,
      update(ch, pos, done) {
        paintSections(ch);
        const included = ch.include !== false;
        const ready = included && ch.status === 'ready' && (!!ch.audio_url || typeof ch.offset === 'number');
        const partsReady = included && !ready ? readyParts(ch).length : 0;
        const partsTotal = included && !ready ? (ch.parts || []).length : 0;
        const playable = ready || partsReady > 0;
        const mp3 = ready && ch.mp3_url && !state.offline ? mediaUrl(ch.mp3_url) : null;
        dl.hidden = !mp3;
        el.classList.toggle('has-dl', !!mp3);
        if (mp3 && dl.getAttribute('href') !== mp3) {
          dl.href = mp3;
          dl.setAttribute('aria-label', `Download ${num ? 'chapter ' + num : ch.title} as MP3`);
        }
        const isDone = done.has(ch.index);
        const offlineOnly = state.offline && playable && !(ready && offline.isChapterOffline(book, ch));
        const pages = pagesText(ch.first_page, ch.last_page, ch.first_page_label);
        const k = [ch.title, ch.status, ch.duration, ch.done_chunks, ch.total_chunks, partsReady, partsTotal, included, isDone, offlineOnly, pages, pos.per && Math.round(pos.per[ch.index] / 30)].join('|');
        el.disabled = !playable || offlineOnly;
        el.classList.toggle('skipped', !included);
        el.classList.toggle('not-ready', included && !playable);
        el.classList.toggle('partial', partsReady > 0);
        el.classList.toggle('listened', isDone);
        if (!r.current) {
          const nk = isDone ? 'done' : 'n';
          if (numEl.dataset.k !== nk) {
            numEl.dataset.k = nk;
            numEl.replaceChildren(isDone ? icon('check', 16) : document.createTextNode(num ? String(num) : ''));
          }
          // saved progress hairline for chapters that aren't playing
          const per = pos.per && pos.per[ch.index];
          hair.style.setProperty('--p', String(!isDone && per > 1 && ch.duration ? Math.min(1, per / ch.duration) : 0));
        }
        if (k === key) return;
        key = k;
        titleEl.textContent = ch.title;
        let note = '';
        right.replaceChildren();
        if (!included) {
          right.append(h('span', { class: 'tag', text: 'Skipped' }));
        } else if (ch.status === 'ready') {
          right.append(h('span', { text: clock(ch.duration || 0) }));
          if (offlineOnly) note = 'Not downloaded';
          else if (isDone) note = 'Listened';
          else if (pos.per && pos.per[ch.index] > 15 && ch.duration) note = `${clock(Math.max(0, ch.duration - pos.per[ch.index]))} left`;
        } else if (partsTotal && (partsReady || ch.status === 'rendering')) {
          const text = `Voicing… ${partsReady} of ${partsTotal} parts`;
          if (ch.status === 'rendering') right.append(spinner('xs'));
          right.append(h('span', { class: 'accent', text }));
          if (partsReady && !offlineOnly) note = 'You can listen now';
          else if (offlineOnly) note = 'Not downloaded';
        } else if (ch.status === 'rendering') {
          right.append(spinner('xs'), h('span', { class: 'accent', text: `Voicing… ${ch.done_chunks || 0} of ${ch.total_chunks || '?'}` }));
        } else {
          right.append(h('span', { class: 'muted', text: 'Waiting' }));
        }
        sub.textContent = [pages, note].filter(Boolean).join(' · ');
        const statusWord = !included ? 'skipped' : ready ? `${clock(ch.duration || 0)}${isDone ? ', listened' : ''}` : partsReady ? `${partsReady} of ${partsTotal} parts ready, playable` : ch.status === 'rendering' ? 'being voiced' : 'waiting';
        el.setAttribute('aria-label', `${num ? 'Chapter ' + num + ': ' : ''}${ch.title}${pages ? ', ' + pages : ''}, ${statusWord}`);
      },
      current: false,
    };
    return r;
  }

  function paintCurrent() {
    const isBook = player.book && player.book.id === book.id;
    const idx = isBook ? player.index : null;
    if (idx !== currentIdx) {
      const old = currentIdx != null && rows.get(currentIdx);
      if (old) {
        old.current = false;
        old.el.classList.remove('is-current', 'is-playing');
        old.el.removeAttribute('aria-current');
        old.hair.classList.remove('live');
        old.numEl.dataset.k = '';
        const pos = player.getPos(book.id) || {};
        const ch = book.chapters.find((c) => c.index === currentIdx);
        if (ch) old.update(ch, pos, new Set(pos.done || []));
      }
      currentIdx = idx;
      const ch = idx != null && book.chapters.find((c) => c.index === idx);
      const g = ch && ch.part && groups.get(ch.part);
      if (g && !g.open()) {
        g.setOpen(true);
        setPartOpen(ch.part, true);
      }
      const r = idx != null && rows.get(idx);
      if (r) {
        r.current = true;
        r.el.classList.add('is-current');
        r.el.setAttribute('aria-current', 'true');
        r.hair.classList.add('live');
        r.numEl.dataset.k = 'eq';
        r.numEl.replaceChildren(equalizer());
      }
    }
    const r = currentIdx != null && rows.get(currentIdx);
    if (r) {
      r.el.classList.toggle('is-playing', player.playing);
      const d = player.duration;
      r.hair.style.setProperty('--p', String(d ? Math.min(1, player.time / d) : 0));
    }
  }

  function scrollToCurrent() {
    const r = currentIdx != null && rows.get(currentIdx);
    if (!r) return;
    r.el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    r.el.classList.remove('flash');
    void r.el.offsetWidth;
    r.el.classList.add('flash');
  }

  /* ---------- data ---------- */

  async function load() {
    clearTimeout(timer);
    try {
      const fresh = await api.getBook(id);
      if (!alive) return;
      book = fresh;
      fromSnapshot = false;
      state.offline = false;
      rememberBook(fresh);
      player.updateBook(fresh);
      if (ACTIVE.has(fresh.status) && fresh.status === 'queued') refreshStatus();
      renderAll();
    } catch (err) {
      if (!alive) return;
      if (err.network || err.timeout) {
        state.offline = true;
        if (book) renderAll();
        else page.replaceChildren(callout('offline', 'You’re offline and this book isn’t downloaded.'));
      } else if (err.status === 404) {
        forgetBook(id);
        toast('That book no longer exists on the server.', { kind: 'error' });
        navigate('#/library', { replace: true });
        return;
      } else if (err.status !== 401) {
        if (!book) page.replaceChildren(callout('error', err.message));
        else toast(err.message, { kind: 'error' });
      }
    }
    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    if (!alive || !book) return;
    if (state.offline) timer = setTimeout(load, 10000);
    else if (ACTIVE.has(book.status)) timer = setTimeout(load, 3000);
  }

  async function pauseRender() {
    try {
      book = await api.pauseBook(id);
      rememberBook(book);
      renderAll();
      schedule();
    } catch (err) {
      toast(err.message, { kind: 'error' });
    }
  }

  async function resumeRender() {
    if (!book.voice_id || book.voice_available === false) {
      navigate(`#/new/${encodeURIComponent(id)}/voice`);
      return;
    }
    try {
      book = await api.renderBook(id, book.voice_id, book.pace || 1);
      rememberBook(book);
      renderAll();
      refreshStatus();
      schedule();
    } catch (err) {
      toast(err.message, { kind: 'error' });
    }
  }

  async function startDownload() {
    try {
      await offline.download(book);
      if (alive) toast('Available offline', { kind: 'ok' });
    } catch (err) {
      if (err.name !== 'AbortError' && alive) toast(err.message || 'Download failed.', { kind: 'error' });
    }
  }

  function openActions() {
    if (!book) return;
    const st = offline.status(book);
    const job = offline.downloading(book.id);
    const canDl = offline.supported();
    const readyCount = playableChapters(book).length;
    const finished = book.status === 'ready';
    const later = 'Available when the book is finished';
    const actions = [];
    const more = book.stream_url ? !offline.isStreamOffline(book) : st.have < readyCount;
    if (job) actions.push({ label: 'Cancel download', icon: 'close', onSelect: () => job.abort() });
    else if (canDl && readyCount && more)
      actions.push({
        label: st.state === 'none' ? 'Download for offline' : book.stream_url ? 'Download finished book for offline' : 'Download new chapters',
        icon: 'download',
        hint: ACTIVE.has(book.status) ? 'Listen in the app without internet (chapters ready now)' : 'Listen in the app without internet',
        onSelect: startDownload,
      });
    if (st.state !== 'none')
      actions.push({
        label: 'Remove offline copy',
        icon: 'trash',
        hint: st.bytes ? `Frees about ${Math.max(1, Math.round(st.bytes / 1048576))} MB` : null,
        onSelect: async () => {
          await offline.remove(book.id);
          toast('Offline copy removed');
        },
      });
    if (!canDl && readyCount) actions.push({ label: 'Download for offline', icon: 'download', hint: 'Needs the app opened over HTTPS', disabled: true });
    const file = (url, label, ic, hint, name) => ({ label, icon: ic, href: finished && url ? mediaUrl(url) : null, download: name, disabled: !(finished && url), hint: finished && url ? hint : later });
    actions.push(file(book.mp3_url, 'Whole book as MP3', 'download', 'One file with chapter marks'));
    actions.push(file(book.mp3_zip_url, 'All chapters as MP3 (.zip)', 'zip', 'One MP3 per chapter'));
    actions.push(file(book.m4b_url, 'Save .m4b for Apple Books', 'book', 'Opens in Apple Books with chapters', `${safeName(book.title)}.m4b`));
    if (book.pages && book.status !== 'draft') actions.unshift({ label: 'Go to page', icon: 'book', hint: 'Start listening at a page of your PDF', onSelect: () => openGoToPage(book) });
    if (book.voice_available === false && !ACTIVE.has(book.status)) actions.push({ label: 'Change voice', icon: 'wave', hint: 'This voice isn’t available with the current engine', onSelect: () => navigate(`#/new/${encodeURIComponent(book.id)}/voice`) });
    if (['draft', 'paused', 'error'].includes(book.status)) actions.push({ label: 'Edit chapters & voice', icon: 'pencil', onSelect: () => navigate(`#/new/${encodeURIComponent(book.id)}/chapters`) });
    // a finished book can be narrated again with another voice (only changed chapters are re-voiced)
    if (book.status === 'ready') actions.push({ label: 'Change voice', icon: 'wave', onSelect: () => navigate(`#/new/${encodeURIComponent(book.id)}/voice`) });
    actions.push({ label: 'Delete book', icon: 'trash', danger: true, onSelect: confirmDelete });
    actionSheet({ title: book.title, message: 'Files you download are saved to Files, in Downloads. Chapter MP3s are on each chapter row.', actions });
  }

  async function confirmDelete() {
    const ok = await confirmSheet({ title: 'Delete this book?', message: `“${book.title}” and all of its audio will be deleted from your server and this phone.`, confirmLabel: 'Delete book', danger: true });
    if (!ok) return;
    try {
      await api.deleteBook(id);
    } catch (err) {
      if (err.status !== 404) {
        toast(err.message, { kind: 'error' });
        return;
      }
    }
    if (player.book && player.book.id === id) player.unload();
    await offline.remove(id);
    forgetBook(id);
    toast('Book deleted');
    navigate('#/library', { replace: true });
  }

  const offs = [
    player.on('track', () => {
      if (!book) return;
      paintCurrent();
      renderPrimary();
    }),
    player.on('state', () => {
      if (!book) return;
      paintCurrent();
      renderPrimary();
    }),
    player.on('time', () => book && paintCurrent()),
    player.on('book', () => {
      if (book && player.book && player.book.id === book.id && player.book !== book) {
        book = player.book;
        renderAll();
      }
    }),
    offline.events.on('progress', (bid) => bid === id && book && renderOffline()),
    stats.events.on('change', refreshProgress),
    player.on('time', throttle(refreshProgress, 5000)),
    offline.events.on('change', (bid) => {
      if (bid === id && book) {
        renderOffline();
        renderChapters();
      }
    }),
    bus.on('scroll-to-current', scrollToCurrent),
    bus.on('visible', load),
    bus.on('online', load),
    bus.on('status', () => book && book.status === 'queued' && renderRenderCard()),
  ];

  load().then(() => {
    if (!alive || !book) return;
    const sp = pendingStart(id);
    if (sp && book.status !== 'draft') goToPage(book, sp, { quiet: true });
    if (state.scrollToCurrent) {
      state.scrollToCurrent = false;
      setTimeout(scrollToCurrent, 60);
    }
    offline.verify(book).then(() => alive && book && renderOffline());
  });
  if (state.scrollToCurrent && fromSnapshot) {
    setTimeout(() => {
      if (state.scrollToCurrent) {
        state.scrollToCurrent = false;
        scrollToCurrent();
      }
    }, 60);
  }

  return () => {
    alive = false;
    clearTimeout(timer);
    io && io.disconnect();
    stickyIo && stickyIo.disconnect();
    offs.forEach((o) => o());
  };
}

function safeName(s) {
  return String(s || 'audiobook').replace(/[\\/:*?"<>|]+/g, ' ').trim().slice(0, 80) || 'audiobook';
}
