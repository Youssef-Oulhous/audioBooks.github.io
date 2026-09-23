// Stats: streak, daily goal, the month heatmap, day detail, month totals, milestones.

import { h, fill, long } from '../util.js';
import { icon } from '../icons.js';
import { state as appState } from '../state.js';
import { callout, goalRing, openSheet } from '../ui.js';
import { waterCover } from '../water.js';
import * as stats from '../stats.js';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DOW = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
const DOW_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** "42 min", "1 h 05 m" */
export function dur(sec) {
  const m = Math.round((sec || 0) / 60);
  if (m < 60) return `${m} min`;
  return long(sec);
}

function bookFor(id, fallback) {
  const b = appState.books.get(id);
  return b || { id, title: fallback.title || 'A deleted book', author: fallback.author || '', cover_url: null, status: 'ready', chapters: [] };
}

export function mount(root) {
  let alive = true;
  const now = new Date();
  let y = now.getFullYear();
  let m = now.getMonth();
  let selected = stats.dateKey();
  let tick = null;
  let firstRender = true;
  const wc = (book, opts) => waterCover(book, { ...opts, rise: firstRender });

  const monthLabel = h('span', { class: 'month-label', 'aria-live': 'polite' });
  const prevBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Previous month' }, icon('chevronLeft', 22));
  const nextBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Next month' }, icon('chevronRight', 22));
  const head = h('header', { class: 'stats-head' }, h('h1', { class: 't-display', text: 'Stats' }), h('div', { class: 'month-switch' }, prevBtn, monthLabel, nextBtn));
  const note = h('div', { class: 'banners' });
  const body = h('div', { class: 'stats-body' });
  const page = h('div', { class: 'page page-tab page-stats' }, head, note, body);
  root.append(h('div', { class: 'sb-scrim', 'aria-hidden': 'true' }), page);

  prevBtn.addEventListener('click', () => shift(-1));
  nextBtn.addEventListener('click', () => shift(1));

  function isCurrentMonth() {
    const t = new Date();
    return y === t.getFullYear() && m === t.getMonth();
  }

  function shift(delta) {
    const d = new Date(y, m + delta, 1);
    const t = new Date();
    if (d > new Date(t.getFullYear(), t.getMonth(), 1)) return;
    y = d.getFullYear();
    m = d.getMonth();
    selected = isCurrentMonth() ? stats.dateKey() : defaultDay();
    render();
  }

  function defaultDay() {
    const n = stats.daysInMonth(y, m);
    for (let d = n; d >= 1; d--) {
      const k = `${stats.monthKey(y, m)}-${String(d).padStart(2, '0')}`;
      if (stats.listenOn(k) >= stats.ACTIVE_SECONDS) return k;
    }
    return `${stats.monthKey(y, m)}-01`;
  }

  /* ---------- sections ---------- */

  function heroRow() {
    const st = stats.streak();
    const goal = stats.goalMinutes();
    const ring = goalRing(92);
    ring.set(stats.listenOn(stats.dateKey()), goal);
    const ringBtn = h('button', { class: 'ring-tile', type: 'button', 'aria-label': `Today's goal: ${goal} minutes. Change the daily goal.` }, ring, h('span', { class: 'ring-tile-cap', text: "Today's goal" }));
    ringBtn.addEventListener('click', openGoal);
    return h(
      'section',
      { class: 'hero-row' },
      h(
        'div',
        { class: 'streak-tile' },
        h('span', { class: st.current ? 'flame' : 'muted' }, icon(st.current ? 'flame' : 'flameLine', 30)),
        h('div', { class: 'streak-num' }, h('b', { text: String(st.current) }), h('span', { text: st.current === 1 ? 'day' : 'days' })),
        h('div', { class: 'streak-sub', text: st.current ? `Longest ${st.longest} ${st.longest === 1 ? 'day' : 'days'}` : st.longest ? `Longest ${st.longest} days. Listen 5 minutes to start again.` : 'Listen 5 minutes a day to build a streak.' }),
      ),
      ringBtn,
    );
  }

  function calendar() {
    const today = stats.dateKey();
    const n = stats.daysInMonth(y, m);
    const anchor = isCurrentMonth() ? today : stats.dateKey(new Date(y, m + 1, 1));
    const usual = stats.usualDay(anchor);
    const lead = (new Date(y, m, 1).getDay() + 6) % 7;
    const grid = h('div', { class: 'cal-grid', role: 'grid', 'aria-label': `${MONTHS[m]} ${y}` });
    for (let i = 0; i < lead; i++) grid.append(h('span', { class: 'cal-pad', 'aria-hidden': 'true' }));
    for (let d = 1; d <= n; d++) {
      const k = `${stats.monthKey(y, m)}-${String(d).padStart(2, '0')}`;
      const future = k > today;
      const listen = stats.listenOn(k);
      const lv = future ? 0 : stats.level(listen, usual);
      const date = stats.parseKey(k);
      const cls = ['cal-day', `l${lv}`, future ? 'future' : '', k === today ? 'today' : '', k === selected ? 'selected' : ''].filter(Boolean).join(' ');
      const btn = h(
        'button',
        {
          class: cls,
          type: 'button',
          disabled: future,
          dataset: { date: k, level: String(lv) },
          'aria-label': `${DOW_LONG[date.getDay()]} ${d} ${MONTHS[m]}${future ? '' : `, ${listen >= 60 ? dur(listen) : 'no'} listening`}`,
          'aria-pressed': String(k === selected),
        },
        String(d),
      );
      btn.addEventListener('click', () => {
        selected = k;
        grid.querySelectorAll('.cal-day.selected').forEach((x) => {
          x.classList.remove('selected');
          x.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('selected');
        btn.setAttribute('aria-pressed', 'true');
        detailSlot.replaceChildren(dayDetail(k));
      });
      grid.append(btn);
    }
    const legend = h('div', { class: 'legend', 'aria-hidden': 'true' }, h('span', { text: 'Less' }), [0, 1, 2, 3, 4].map((lv) => h('i', { style: `background:var(--heat-${lv})` })), h('span', { text: 'More' }));
    return h(
      'section',
      { class: 'cal', 'aria-label': 'Listening calendar' },
      h('div', { class: 'cal-dow', 'aria-hidden': 'true' }, DOW.map((x) => h('span', { text: x }))),
      grid,
      legend,
      h('p', { class: 'usual', text: `Compared with your usual day (about ${Math.max(1, Math.round(usual / 60))} min)` }),
    );
  }

  const detailSlot = h('div', { class: 'detail-slot' });

  function dayDetail(k) {
    const d = stats.parseKey(k);
    const today = stats.dateKey();
    const label = k === today ? 'Today' : k === stats.addDays(today, -1) ? 'Yesterday' : `${DOW_LONG[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
    const day = stats.merged().days[k] || { listen: 0, app: 0 };
    const books = stats.booksOn(k);
    const finished = Object.entries(stats.merged().books).filter(([, b]) => b.finished_at === k);
    const rows = books.map((b) => {
      const book = bookFor(b.id, b);
      const pct = stats.isFinished(b.id) ? 100 : book.chapters && book.chapters.length ? stats.progress(book) : 0;
      return h(
        'a',
        { class: 'dd-book', href: appState.books.has(b.id) ? `#/book/${encodeURIComponent(b.id)}` : '#/stats' },
        wc(book, { pct, size: 'sm' }),
        h('div', { class: 'dd-book-text' }, h('div', { class: 'dd-book-title', text: b.title }), h('div', { class: 'dd-book-min', text: dur(b.seconds) }), b.finished ? h('div', { class: 'dd-finished' }, icon('sealFill', 16), h('span', { text: `Finished ${b.title}` })) : null),
      );
    });
    // books finished that day without listening time recorded on this device
    for (const [id, b] of finished) {
      if (!books.some((x) => x.id === id)) rows.push(h('div', { class: 'dd-finished' }, icon('sealFill', 16), h('span', { text: `Finished ${b.title}` })));
    }
    const nothing = day.listen < 1;
    return h(
      'section',
      { class: 'day-detail', 'aria-live': 'polite', dataset: { date: k } },
      h('h2', { class: 'dd-date', text: label }),
      h(
        'div',
        { class: 'dd-stats' },
        h('span', { class: 'dd-stat dd-listen' }, icon('headphones', 20), h('span', { text: `${dur(day.listen)} listened` })),
        h('span', { class: 'dd-stat dd-app' }, icon('device', 20), h('span', { text: `${dur(day.app)} in the app` })),
      ),
      nothing ? h('p', { class: 'dd-empty', text: k === today ? `Nothing yet today. Your goal is ${stats.goalMinutes()} minutes.` : 'No listening on this day.' }) : null,
      rows.length ? h('div', { class: 'dd-books' }, rows) : null,
    );
  }

  function monthBento() {
    const cur = isCurrentMonth();
    const t = new Date();
    const untilDay = cur ? t.getDate() : null;
    const tot = stats.monthTotals(y, m, untilDay);
    const prevDate = new Date(y, m - 1, 1);
    const prevDays = stats.daysInMonth(prevDate.getFullYear(), prevDate.getMonth());
    const prev = stats.monthTotals(prevDate.getFullYear(), prevDate.getMonth(), cur ? Math.min(untilDay, prevDays) : null);
    let delta = null;
    if (prev.listen >= 60 && tot.listen >= 0) {
      const pct = Math.round(((tot.listen - prev.listen) / prev.listen) * 100);
      delta = h('span', { class: `delta ${pct >= 0 ? 'up' : 'down'}` }, `${pct >= 0 ? '+' : '−'}${Math.abs(pct)}% vs ${MONTHS[prevDate.getMonth()]}`);
    }
    const finished = stats.finishedIn(y, m);
    const daysSoFar = cur ? t.getDate() : stats.daysInMonth(y, m);
    const stack = h('div', { class: 'covers-stack' }, finished.slice(0, 4).map((b) => wc(bookFor(b.id, b), { pct: 100, size: 'sm' })));
    return h(
      'section',
      { class: 'bento', 'aria-label': 'Month totals' },
      h('div', { class: 'bt bt-wide bt-listen' }, h('div', { class: 'bt-label' }, icon('headphones', 18), 'Time listened'), h('div', { class: 'bt-value', text: dur(tot.listen) }), delta),
      h(
        'div',
        { class: 'bt bt-tall bt-finished' },
        h('div', {}, h('div', { class: 'bt-label' }, icon('seal', 18), 'Books finished'), h('div', { class: 'bt-value', text: String(finished.length) })),
        finished.length ? stack : h('div', { class: 'bt-sub', text: 'Finish a book to see it here.' }),
      ),
      h('div', { class: 'bt bt-app' }, h('div', { class: 'bt-label' }, icon('device', 18), 'In the app'), h('div', { class: 'bt-value', text: dur(tot.app) })),
      h('div', { class: 'bt bt-active' }, h('div', { class: 'bt-label' }, icon('calendar', 18), 'Active days'), h('div', { class: 'bt-value', text: `${tot.active} of ${daysSoFar}` })),
    );
  }

  function finishedRow() {
    const finished = stats.finishedIn(y, m);
    if (!finished.length) return null;
    return [
      h('h2', { class: 'section-title spaced', text: isCurrentMonth() ? 'Finished this month' : `Finished in ${MONTHS[m]}` }),
      h(
        'div',
        { class: 'fin-row' },
        finished.map((b) => {
          const book = bookFor(b.id, b);
          return h('a', { class: 'fin-item', href: appState.books.has(b.id) ? `#/book/${encodeURIComponent(b.id)}` : '#/stats' }, wc(book, { pct: 100, size: 'md' }), h('div', { class: 'shelf-title', text: b.title }));
        }),
      ),
    ];
  }

  function milestones() {
    const st = stats.streak();
    const total = stats.totalListen();
    const anyFinished = Object.values(stats.merged().books).some((b) => b.finished_at);
    const list = [
      { name: 'First book finished', ic: 'seal', earned: anyFinished, left: 'Finish a book' },
      { name: '7 day streak', ic: 'flameLine', earned: st.longest >= 7, left: `${7 - Math.min(6, st.current)} more ${7 - st.current === 1 ? 'day' : 'days'}` },
      { name: '30 day streak', ic: 'flame', earned: st.longest >= 30, left: `${30 - Math.min(29, st.current)} more days` },
      { name: '10 hours', ic: 'clock', earned: total >= 36000, left: `${long(Math.max(60, 36000 - total))} to go` },
      { name: '50 hours', ic: 'trophy', earned: total >= 180000, left: `${long(Math.max(60, 180000 - total))} to go` },
    ];
    return [
      h('h2', { class: 'section-title spaced', text: 'Milestones' }),
      h(
        'div',
        { class: 'milestones' },
        list.map((x) => h('div', { class: `ms${x.earned ? ' earned' : ''}` }, h('span', { class: 'ms-ic' }, icon(x.ic, 22)), h('div', {}, h('div', { class: 'ms-name', text: x.name }), h('div', { class: 'ms-left', text: x.earned ? 'Earned' : x.left })))),
      ),
    ];
  }

  function skeleton() {
    return h(
      'div',
      { 'aria-hidden': 'true' },
      h('div', { class: 'hero-row' }, h('div', { class: 'sk', style: 'height:150px' }), h('div', { class: 'sk', style: 'height:150px' })),
      h('div', { class: 'cal' }, h('div', { class: 'cal-grid' }, Array.from({ length: 35 }, () => h('div', { class: 'sk', style: 'aspect-ratio:1;border-radius:8px' })))),
    );
  }

  function emptyMonth() {
    if (stats.hasAnyListening() || !isCurrentMonth()) {
      return h('div', { class: 'stats-empty' }, h('p', { class: 'stats-empty-title', text: isCurrentMonth() ? 'No listening yet this month' : `No listening in ${MONTHS[m]}` }), h('p', { text: 'Pick a book from your library. Every minute you listen shows up on this calendar.' }), h('a', { class: 'btn btn-primary btn-sm', href: '#/library' }, icon('books', 20), 'Open library'));
    }
    return h('div', { class: 'stats-empty' }, h('p', { class: 'stats-empty-title', text: 'Your first listening day will light up this calendar' }), h('p', { text: 'Start any book. The minutes you listen each day appear here, and busier days glow brighter.' }), h('a', { class: 'btn btn-primary btn-sm', href: '#/library' }, icon('books', 20), 'Open library'));
  }

  function openGoal() {
    const cur = stats.goalMinutes();
    openSheet(
      (close) => {
        const seg = h(
          'div',
          { class: 'seg seg-lg', role: 'radiogroup', 'aria-label': 'Daily goal in minutes' },
          stats.GOALS.map((g) => {
            const b = h('button', { class: 'seg-btn', type: 'button', role: 'radio', 'aria-checked': String(g === cur) }, h('span', { class: 'seg-main', text: String(g) }), h('span', { class: 'seg-sub', text: 'min' }));
            b.addEventListener('click', () => {
              stats.setGoal(g);
              close();
            });
            return b;
          }),
        );
        return h('div', { class: 'sheet-goal' }, h('div', { class: 'sheet-title', text: 'Daily goal' }), h('p', { class: 'sheet-msg', text: 'How many minutes do you want to listen each day?' }), seg, h('button', { class: 'btn btn-secondary btn-block', type: 'button', text: 'Cancel', onclick: () => close() }));
      },
      { label: 'Daily goal' },
    );
  }

  /* ---------- render ---------- */

  function render() {
    if (!alive) return;
    monthLabel.textContent = `${MONTHS[m]} ${y}`;
    nextBtn.disabled = isCurrentMonth();
    const earliest = Object.keys(stats.merged().days).sort()[0];
    const floor = earliest ? stats.parseKey(earliest) : new Date(now.getFullYear(), now.getMonth() - 12, 1);
    prevBtn.disabled = new Date(y, m, 1) <= new Date(floor.getFullYear(), floor.getMonth(), 1) && !isCurrentMonth() ? true : false;
    if (isCurrentMonth() && !earliest) prevBtn.disabled = true;
    note.replaceChildren();
    if (stats.state.fetchError && stats.state.fetchError.network) note.append(callout('offline', 'Offline. Showing the listening saved on this phone.'));
    if (!stats.state.fetched && !stats.hasAnyListening()) {
      fill(body, skeleton());
      return;
    }
    const tot = stats.monthTotals(y, m);
    const scrollY = window.scrollY;
    detailSlot.replaceChildren(dayDetail(selected));
    fill(
      body,
      heroRow(),
      calendar(),
      tot.listen >= 1 ? detailSlot : emptyMonth(),
      tot.listen >= 1 ? h('h2', { class: 'section-title spaced', text: isCurrentMonth() ? 'This month' : MONTHS[m] }) : null,
      tot.listen >= 1 ? monthBento() : null,
      finishedRow(),
      milestones(),
    );
    window.scrollTo(0, scrollY);
    firstRender = false;
  }

  const offs = [stats.events.on('change', render)];
  render();
  stats.refresh();
  // today's numbers move while a book plays
  tick = setInterval(() => {
    if (document.visibilityState === 'visible' && isCurrentMonth()) render();
  }, 15000);

  return () => {
    alive = false;
    clearInterval(tick);
    offs.forEach((o) => o());
  };
}
