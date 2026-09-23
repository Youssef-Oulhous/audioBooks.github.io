// The one and only <audio> element. It is created once, attached to the DOM and
// lives for the whole session, so playback continues while browsing the app.
//
// Three playback modes:
//  - "stream"  (book finished): the whole book is ONE file (Book.stream_url) and
//    chapters are seeks to Chapter.offset. No file switch at chapter boundaries,
//    which is what keeps audio going on a locked iPhone.
//  - "chapter" (still rendering, or offline with downloaded chapter files): each
//    chapter is its own file; on `ended` we continue with the next chapter.
//  - "part"    (chapter still being made): the chapter plays as its ~30 s parts, one
//    after another as they become ready. Chapter time = sum of the earlier parts'
//    durations + time in the part (the chapter file is exactly its parts joined).
//
// Switches happen at natural moments only (a part/chapter boundary, or play/resume):
// part -> chapter file once the chapter is ready, chapter/part -> stream once the book is.
// All times exposed to the UI (time, duration, seekTo) are relative to the current chapter.

import { Emitter, store, throttle, clamp, silentWav } from './util.js';
import { mediaUrl, absolute } from './config.js';
import { getBook } from './api.js';
import * as offline from './offline.js';

export const RATES = [0.8, 1, 1.25, 1.5, 1.75, 2];
export const SLEEP_OPTIONS = [
  { value: 'off', label: 'Off' },
  { value: 15, label: '15 minutes' },
  { value: 30, label: '30 minutes' },
  { value: 45, label: '45 minutes' },
  { value: 60, label: '1 hour' },
  { value: 'chapter', label: 'End of chapter' },
];
export const BACK_SECONDS = 15;
export const FORWARD_SECONDS = 30;
const RESTART_THRESHOLD = 3; // "previous" restarts the chapter when more than this far in
const WAIT_POLL_MS = 2500;
const LIVE_POLL_MS = 5000;


export function rateLabel(r) {
  return (Number.isInteger(r) ? r.toFixed(1) : String(r)) + '×';
}

/** Included chapters sorted by index. */
export function includedChapters(book) {
  return (book?.chapters || []).filter((c) => c.include !== false).sort((a, b) => a.index - b.index);
}

export function readyParts(c) {
  return (c.parts || []).filter((p) => p.ready && p.url);
}

/** A chapter can be listened to: finished, or at least one of its parts is. */
export function chapterPlayable(c) {
  if (c.include === false) return false;
  if (c.status === 'ready' && (c.audio_url || typeof c.offset === 'number')) return true;
  return readyParts(c).length > 0;
}

export function playableChapters(book) {
  return includedChapters(book).filter(chapterPlayable);
}

/** True when the book can be played as one continuous file. */
export function hasStream(book) {
  if (!book || !book.stream_url) return false;
  const inc = includedChapters(book);
  return inc.length > 0 && inc.every((c) => typeof c.offset === 'number');
}

/** The page shown at `t` seconds, from [page, seconds] marks: the last mark at or before t. */
export function pageAt(marks, t) {
  if (!marks || !marks.length) return null;
  let page = marks[0][0];
  for (const [p, mt] of marks) {
    if (mt <= t + 0.05) page = p;
    else break;
  }
  return page;
}

class Player extends Emitter {
  constructor() {
    super();
    const a = (this.audio = document.createElement('audio'));
    a.preload = 'auto';
    a.setAttribute('preload', 'auto');
    a.setAttribute('playsinline', '');
    a.setAttribute('webkit-playsinline', '');
    a.setAttribute('x-webkit-airplay', 'allow');
    a.hidden = true;
    document.body.append(a);

    this.book = null;
    this.index = -1;
    this.part = -1; // part index in "part" mode
    this._partObj = null; // the part being played (kept even when the chapter's parts list empties)
    this.mode = 'chapter';
    this.loaded = false; // src is set for the current book/chapter
    this.startAt = 0; // chapter-relative position while primed (not loaded)
    this._pendingAbs = null; // media position to apply once metadata is known
    this._src = '';
    this._seeking = false;
    this._streamBroken = null; // stream_url that failed to load -> use chapter files
    this.rate = RATES.includes(store.get('rate', 1)) ? store.get('rate', 1) : 1;
    this.sleep = { mode: 'off', until: 0 };
    this.waiting = null; // null | 'part' | 'chapter': waiting for audio that is still being made
    this._waitTarget = null;
    this.stalled = false;
    this._blob = null;
    this._blobTried = false;
    this._srcRetried = false;
    this._sleepTimer = null;
    this._waitTimer = null;
    this._liveTimer = null;
    this._handlersSet = false;
    this._unlocked = false;
    this._partDur = new Map(); // "book:chapter" -> part durations seen so far
    this.furthestHint = null; // (book) => furthest book-time reached on any device, set by app.js

    this._saveSoon = throttle(() => this.savePos(), 5000);
    this._posSoon = throttle(() => this._positionState(), 1000);
    this._bind();
    this._mediaSession();
  }

  /* ---------- getters ---------- */

  get chapter() {
    return this.book ? this.book.chapters.find((c) => c.index === this.index) || null : null;
  }
  get playing() {
    return this.loaded && !this.audio.paused && !this.audio.ended;
  }
  /** Kept for the UI: waiting for the next chapter. */
  get waitingNext() {
    return this.waiting === 'chapter';
  }
  offsetOf(index) {
    const c = this.book && this.book.chapters.find((x) => x.index === index);
    return c && typeof c.offset === 'number' ? c.offset : null;
  }
  /** Where the current chapter starts inside the loaded media (stream mode). */
  get chapterStart() {
    return this.mode === 'stream' ? this.offsetOf(this.index) || 0 : 0;
  }
  get absTime() {
    if (this._pendingAbs != null) return this._pendingAbs;
    return this.audio.currentTime || 0;
  }
  /** Seconds into the current chapter. */
  get time() {
    if (!this.loaded) return this.startAt || 0;
    if (this.mode === 'part') {
      const ch = this.chapter;
      return (ch ? this._partStart(this.book, ch, this.part) : 0) + this.absTime;
    }
    return Math.max(0, this.absTime - this.chapterStart);
  }
  /** Length of the current chapter (an estimate while it's still made of parts). */
  get duration() {
    const ch = this.chapter;
    const d = this.audio.duration;
    if (this.loaded && this.mode === 'stream') {
      const nxt = includedChapters(this.book).find((c) => c.index > this.index && typeof c.offset === 'number');
      if (nxt) return Math.max(0, nxt.offset - this.chapterStart);
      if (Number.isFinite(d) && d > 0) return Math.max(0, d - this.chapterStart);
      return (ch && ch.duration) || 0;
    }
    if (this.loaded && this.mode === 'part' && ch) {
      return this._durs(this.book, ch).reduce((n, x) => n + x, 0) || (ch.est_seconds || 0) / (this.book.pace || 1);
    }
    if (this.loaded && Number.isFinite(d) && d > 0) return d;
    return (ch && ch.duration) || 0;
  }
  /** Current part's own length (part mode). */
  get partDuration() {
    const d = this.audio.duration;
    if (Number.isFinite(d) && d > 0) return d;
    return (this._partObj && this._partObj.duration) || 0;
  }

  /* ---------- parts ---------- */

  /** Durations of a chapter's parts: known ones (remembered), unknown ones estimated. */
  _durs(book, c) {
    const key = `${book.id}:${c.index}`;
    let known = this._partDur.get(key) || [];
    const parts = c.parts || [];
    if (Array.isArray(c.part_durations) && c.part_durations.length && c.part_durations.every((x) => x > 0)) {
      known = c.part_durations.slice(); // exact, from the server, once the chapter is finished
      this._partDur.set(key, known);
    } else if (parts.length) {
      known = parts.map((p, i) => (p.duration > 0 ? p.duration : known[i] > 0 ? known[i] : null));
      this._partDur.set(key, known);
    }
    const vals = known.filter((x) => x > 0);
    const est = vals.length ? vals.reduce((n, x) => n + x, 0) / vals.length : ((c.est_seconds || 0) / (book.pace || 1)) / Math.max(1, known.length) || 30;
    return known.map((x) => (x > 0 ? x : est));
  }

  _partStart(book, c, n) {
    const d = this._durs(book, c);
    let s = 0;
    for (let i = 0; i < n && i < d.length; i++) s += d[i];
    return s;
  }

  /** Chapter time -> {part, offset}. */
  _partAtTime(book, c, t) {
    const d = this._durs(book, c);
    let s = 0;
    for (let i = 0; i < d.length; i++) {
      if (t < s + d[i] || i === d.length - 1) return { part: i, offset: Math.max(0, Math.min(t - s, d[i])) };
      s += d[i];
    }
    return { part: 0, offset: 0 };
  }

  /** Current page of the PDF from the marks of whatever is playing (null when unknown). */
  currentPage() {
    const ch = this.chapter;
    if (!ch) return null;
    // marks carry the page as the reader sees it (a number, or "xii" for roman pages)
    const src = this.loaded && this.mode === 'part' && this._partObj ? this._partObj : ch;
    const marks = src === ch ? ch.page_marks : src.marks;
    const at = pageAt(marks, src === ch ? (this.loaded ? this.time : this.startAt || 0) : this.absTime);
    return at != null ? at : src.first_page != null ? src.first_page : src.first_page_label || null;
  }

  /** The part of the book the current chapter belongs to ("Part II"), when it has one. */
  currentPart() {
    const ch = this.chapter;
    return (ch && ch.part) || null;
  }

  /** Position in the book: {pos, total, left (seconds, adjusted for speed)} */
  bookInfo() {
    const b = this.book;
    if (!b) return null;
    const inc = includedChapters(b);
    const pos = inc.findIndex((c) => c.index === this.index) + 1;
    let left = Math.max(0, this.duration - this.time);
    for (const c of inc) if (c.index > this.index) left += c.duration || (c.est_seconds || 0) / (b.pace || 1);
    return { pos, total: inc.length, left: left / (this.rate || 1) };
  }

  /** Can this book be played as one file right now? */
  canStream(book) {
    if (!hasStream(book)) return false;
    if (this._streamBroken === book.stream_url) return false;
    if (navigator.onLine === false && !offline.isStreamOffline(book)) return false;
    return true;
  }

  /** Last included chapter starting at or before `abs` seconds of the stream. */
  chapterAtAbs(abs) {
    let found = null;
    for (const c of includedChapters(this.book)) {
      if (typeof c.offset === 'number' && c.offset <= abs + 0.05) found = c;
    }
    return found || includedChapters(this.book)[0] || null;
  }

  /* ---------- saved positions ---------- */

  getPos(bookId) {
    return store.get('pos:' + bookId, null);
  }

  savePos() {
    if (!this.book || this.index < 0) return;
    const id = this.book.id;
    const pos = this.getPos(id) || {};
    const t = this.time;
    pos.i = this.index; // chapter index + chapter time: works in every mode
    pos.t = Math.round(t * 10) / 10;
    pos.mode = this.loaded ? this.mode : pos.mode || 'chapter';
    pos.time = this.loaded && this.mode === 'stream' ? Math.round(this.absTime * 10) / 10 : null; // stream position
    pos.part = this.loaded && this.mode === 'part' ? this.part : null; // exact part position, when in parts
    pos.pt = this.loaded && this.mode === 'part' ? Math.round(this.absTime * 10) / 10 : null;
    pos.per = pos.per || {};
    pos.per[this.index] = pos.t;
    pos.done = pos.done || [];
    const d = this.duration;
    if (this.mode !== 'part' && d && t / d > 0.97 && !pos.done.includes(this.index)) pos.done.push(this.index);
    pos.at = Date.now();
    store.set('pos:' + id, pos);
  }

  _markDone(index) {
    if (!this.book) return;
    const pos = this.getPos(this.book.id) || { per: {}, done: [] };
    pos.done = pos.done || [];
    pos.per = pos.per || {};
    if (!pos.done.includes(index)) pos.done.push(index);
    pos.per[index] = 0;
    pos.at = Date.now();
    store.set('pos:' + this.book.id, pos);
  }

  /* ---------- loading ---------- */

  /** Show a book/chapter in the player without loading audio yet (restored session). */
  prime(book, index, time = 0) {
    if (this.loaded) return;
    this.book = book;
    this.index = index;
    this.startAt = time || 0;
    this._meta();
    this.emit('track');
  }

  /**
   * Load chapter `index` at `time` seconds into it, as a stream seek, a chapter file or a
   * part. `part`/`partTime` jump straight to a part (go to page, exact resume).
   */
  load(book, index, { time = 0, autoplay = true, part = null, partTime = 0 } = {}) {
    const ch = book.chapters.find((c) => c.index === index);
    if (!ch) return false;
    this._durs(book, ch); // remember part durations before they disappear
    if (this.canStream(book) && typeof ch.offset === 'number') return this._loadStream(book, index, ch.offset + Math.max(0, time), autoplay);
    if (ch.status === 'ready' && ch.audio_url) return this._loadChapter(book, index, time, autoplay);
    const parts = ch.parts || [];
    const ready = readyParts(ch);
    if (!ready.length) return false;
    let p;
    let off;
    if (part != null && parts[part] && parts[part].ready) {
      p = part;
      off = partTime || 0;
    } else {
      const at = this._partAtTime(book, ch, time);
      p = at.part;
      off = at.offset;
      if (!parts[p] || !parts[p].ready) {
        // not voiced yet: the first ready part after it (or the first ready one)
        const nx = parts.find((x, i) => i > p && x.ready) || ready[0];
        p = parts.indexOf(nx);
        off = 0;
      }
    }
    return this._loadPart(book, index, p, off, autoplay);
  }

  _setSrc(src, abs) {
    this._revokeBlob();
    this._blobTried = false;
    this._srcRetried = false;
    this.stalled = false;
    this._src = src;
    this._pendingAbs = abs > 0 ? abs : null;
    this.audio.defaultPlaybackRate = this.rate;
    this.audio.src = src;
    this.audio.playbackRate = this.rate;
  }

  _after(book, autoplay) {
    store.set('last', { bookId: book.id });
    this._meta();
    this.savePos();
    this.emit('track');
    this._positionState();
    this._liveUpdates();
    if (autoplay) this._play();
    else this.emit('state');
    return true;
  }

  _begin(book, index, mode) {
    if (this.loaded) this.savePos();
    this._clearWait();
    this.book = book;
    this.mode = mode;
    this.index = index;
    this.loaded = true;
  }

  _loadStream(book, index, abs, autoplay) {
    const src = mediaUrl(book.stream_url);
    const same = this.loaded && this.mode === 'stream' && this._src === src && this.book && this.book.id === book.id;
    this._begin(book, index, 'stream');
    this.part = -1;
    this._partObj = null;
    if (same) this._seekAbs(abs);
    else this._setSrc(src, abs);
    return this._after(book, autoplay);
  }

  _loadChapter(book, index, time, autoplay) {
    const ch = book.chapters.find((c) => c.index === index);
    this._begin(book, index, 'chapter');
    this.part = -1;
    this._partObj = null;
    this._setSrc(mediaUrl(ch.audio_url), time);
    return this._after(book, autoplay);
  }

  _loadPart(book, index, p, offset, autoplay) {
    const ch = book.chapters.find((c) => c.index === index);
    const pt = ch && ch.parts && ch.parts[p];
    if (!pt || !pt.url) return false;
    this._begin(book, index, 'part');
    this.part = p;
    this._partObj = pt;
    this._setSrc(mediaUrl(pt.url), offset);
    return this._after(book, autoplay);
  }

  unload() {
    this._clearWait();
    this.setSleep('off');
    this.audio.pause();
    this.loaded = false;
    this.audio.removeAttribute('src');
    try {
      this.audio.load();
    } catch {
      /* ignore */
    }
    this._revokeBlob();
    this._src = '';
    this.book = null;
    this.index = -1;
    this.part = -1;
    this._partObj = null;
    this.startAt = 0;
    this._pendingAbs = null;
    clearInterval(this._liveTimer);
    if ('mediaSession' in navigator) {
      try {
        navigator.mediaSession.metadata = null;
      } catch {
        /* ignore */
      }
    }
    this.emit('track');
    this.emit('state');
  }

  _play() {
    const p = this.audio.play();
    if (p && p.catch) {
      p.catch((err) => {
        if (err && err.name === 'NotAllowedError') this.emit('blocked');
        this.emit('state');
      });
    }
  }

  /**
   * Call inside a tap before something slow (go to page, voicing a start page), so iOS
   * lets playback start later without another tap.
   */
  unlock() {
    if (this._unlocked || this.playing || this.loaded) return;
    const a = this.audio;
    try {
      a.src = silentWav();
      const p = a.play();
      if (p && p.then) p.then(() => !this.loaded && a.pause(), () => {});
    } catch {
      /* ignore */
    }
  }

  _seekAbs(abs) {
    if (this.audio.readyState < 1) {
      this._pendingAbs = abs;
      return;
    }
    this._seeking = true;
    this.audio.currentTime = Math.max(0, abs);
    if (this.mode === 'stream') this._syncChapter();
  }

  /* ---------- transport ---------- */

  play() {
    if (!this.book) return;
    if (!this.loaded) {
      const ch = this.chapter;
      const pos = this.getPos(this.book.id);
      if (ch && chapterPlayable(ch)) {
        const exact = pos && pos.i === ch.index && pos.part != null ? { part: pos.part, partTime: pos.pt } : {};
        this.load(this.book, this.index, { time: this.startAt, autoplay: true, ...exact });
      } else this.resumeBook(this.book);
      return;
    }
    const ch = this.chapter;
    // Natural moment to move to a better source: book finished -> stream, chapter finished -> file.
    if ((this.audio.paused || this.audio.ended) && ch) {
      const toStream = this.mode !== 'stream' && this.canStream(this.book);
      const toFile = this.mode === 'part' && ch.status === 'ready' && ch.audio_url;
      if (toStream || toFile) {
        let idx = this.index;
        let t = this.time;
        if (this.audio.ended && this.mode === 'chapter') {
          const n = playableChapters(this.book).find((c) => c.index > idx);
          if (n) {
            idx = n.index;
            t = 0;
          }
        }
        this.load(this.book, idx, { time: t, autoplay: true });
        return;
      }
    }
    if (this.audio.ended) {
      if (this.mode === 'part') {
        this._afterPart();
        return;
      }
      if (this.mode === 'chapter') {
        // e.g. after "sleep at end of chapter": carry on with the next chapter
        if (this.next({ auto: true }) || this.waiting) return;
        this.audio.currentTime = 0;
      } else {
        this._seekAbs(this.chapterStart);
      }
    }
    this._play();
  }

  pause() {
    this.audio.pause();
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  /** Seek within the current chapter (seconds from its start). */
  seekTo(t) {
    const d = this.duration;
    const target = clamp(t, 0, d ? Math.max(0, d - 0.3) : Math.max(0, t));
    if (!this.loaded) {
      this.startAt = target;
    } else if (this.mode === 'part') {
      const ch = this.chapter;
      const at = this._partAtTime(this.book, ch, target);
      if (at.part === this.part) {
        this._seekAbs(Math.min(at.offset, Math.max(0, this.partDuration - 0.3)));
      } else if (ch.parts && ch.parts[at.part] && ch.parts[at.part].ready) {
        this._loadPart(this.book, this.index, at.part, at.offset, this.playing);
      } else {
        this.emit('notice', 'That part isn’t voiced yet.');
        return;
      }
    } else {
      this._seekAbs(this.chapterStart + target);
    }
    this.emit('time');
    this._positionState();
    this._saveSoon();
  }

  seekBy(delta) {
    if (this.loaded && this.mode === 'stream' && this.audio.readyState >= 1) {
      // one continuous file: skipping can cross chapter boundaries
      const d = this.audio.duration;
      this._seekAbs(clamp(this.audio.currentTime + delta, 0, Number.isFinite(d) ? Math.max(0, d - 0.3) : Infinity));
      this.emit('time');
      this._positionState();
      this._saveSoon();
      return;
    }
    this.seekTo(this.time + delta);
  }

  /** Play a chapter from where it was left (or from the start). */
  playChapter(book, index) {
    if (this.book && this.book.id === book.id && this.index === index && this.loaded) {
      this.book = book;
      if (!this.playing) this.play();
      return;
    }
    const ch = book.chapters.find((c) => c.index === index);
    if (!ch) return;
    const pos = this.getPos(book.id);
    let t = (pos && pos.per && pos.per[index]) || 0;
    if (ch.duration && t > ch.duration - 5) t = 0;
    this.load(book, index, { time: t, autoplay: true });
  }

  /**
   * Where "continue listening" would pick up: {chapter, time, started, part, partTime}.
   * A finished chapter moves on to the next playable one.
   */
  resumePoint(book) {
    const list = playableChapters(book);
    if (!list.length) return null;
    let cur = this.book && this.book.id === book.id && this.index >= 0 ? { i: this.index, t: this.time, part: this.mode === 'part' && this.loaded ? this.part : null, pt: this.absTime } : this.getPos(book.id);
    // Nothing saved on this phone: pick up at the furthest point reached on any device (stats).
    if (!cur && this.furthestHint) {
      const f = this.furthestHint(book);
      if (f > 30) cur = this._posFromBookTime(book, f);
    }
    let ch = cur ? list.find((c) => c.index === cur.i) : null;
    let t = ch ? cur.t || 0 : 0;
    const dur = ch ? (this.book && this.book.id === book.id && this.index === ch.index ? this.duration : ch.duration) || 0 : 0;
    if (ch && dur && ch.status === 'ready' && t > dur - 3) {
      const nxt = list.find((c) => c.index > ch.index);
      if (nxt) {
        ch = nxt;
        t = 0;
      }
    }
    if (!ch) {
      ch = (cur && list.find((c) => c.index > cur.i)) || list[0];
      t = 0;
    }
    const exact = cur && cur.i === ch.index && cur.part != null && ch.status !== 'ready' && ch.parts && ch.parts[cur.part] && ch.parts[cur.part].ready;
    return { chapter: ch, time: t, started: !!cur && (t > 1 || ch.index !== list[0].index), part: exact ? cur.part : null, partTime: exact ? cur.pt || 0 : 0 };
  }

  /** Map a position in book-time (seconds of included audio) to {i: chapter index, t: time in chapter}. */
  _posFromBookTime(book, t) {
    let rest = t;
    const inc = includedChapters(book);
    for (const c of inc) {
      const d = c.duration || (c.est_seconds || 0) / (book.pace || 1);
      if (rest < d || c === inc[inc.length - 1]) return { i: c.index, t: Math.max(0, Math.min(rest, d)) };
      rest -= d;
    }
    return null;
  }

  /** Continue a book from its saved position (or the first playable chapter). */
  resumeBook(book) {
    if (this.book && this.book.id === book.id && this.index >= 0) {
      this.book = book;
      const ch = this.chapter;
      if (this.loaded || (ch && chapterPlayable(ch))) {
        this.play();
        return true;
      }
    }
    const rp = this.resumePoint(book);
    if (!rp) return false;
    return this.load(book, rp.chapter.index, { time: rp.time, autoplay: true, part: rp.part, partTime: rp.partTime });
  }

  /** Jump to chapter `index` (start) without changing play/pause state. */
  _goTo(index, auto = false) {
    const b = this.book;
    if (!this.loaded) {
      this.startAt = 0;
      this.index = index;
      this._meta();
      this.emit('track');
      this.emit('time');
      return;
    }
    if (this.mode === 'stream' && this.offsetOf(index) != null) {
      this.index = index;
      this._seekAbs(this.offsetOf(index));
      this._meta();
      this.savePos();
      this.emit('track');
      this.emit('time');
      this._positionState();
      return;
    }
    this.load(b, index, { time: 0, autoplay: auto || this.playing });
  }

  next({ auto = false } = {}) {
    const b = this.book;
    if (!b) return false;
    if (auto) {
      // Carry on with the very next chapter, from its beginning: play it if it's there, else wait.
      const nxt = includedChapters(b).find((c) => c.index > this.index);
      if (!nxt) {
        this.emit('finished');
        return false;
      }
      if ((nxt.status === 'ready' && (nxt.audio_url || typeof nxt.offset === 'number')) || (nxt.parts && nxt.parts[0] && nxt.parts[0].ready)) {
        this.load(b, nxt.index, { time: 0, autoplay: true, part: nxt.status === 'ready' ? null : 0 });
        return true;
      }
      this._waitFor('chapter', { index: nxt.index });
      this.emit('notice', `Chapter ${includedChapters(b).indexOf(nxt) + 1} is still being voiced. It will play as soon as it’s ready.`);
      return false;
    }
    const n = playableChapters(b).find((c) => c.index > this.index);
    if (n) {
      this._goTo(n.index);
      return true;
    }
    const pending = includedChapters(b).find((c) => c.index > this.index);
    this.emit('notice', pending ? `Chapter ${includedChapters(b).indexOf(pending) + 1} is still being voiced.` : 'This is the last chapter.');
    return false;
  }

  prev() {
    const b = this.book;
    if (!b) return;
    if (this.time > RESTART_THRESHOLD) {
      this.seekTo(0);
      return;
    }
    const list = playableChapters(b).filter((c) => c.index < this.index);
    const p = list[list.length - 1];
    if (!p) {
      this.seekTo(0);
      return;
    }
    this._goTo(p.index);
  }

  setRate(r) {
    this.rate = r;
    store.set('rate', r);
    this.audio.defaultPlaybackRate = r;
    this.audio.playbackRate = r;
    this.emit('rate');
    this._positionState();
  }

  cycleRate() {
    const i = RATES.indexOf(this.rate);
    this.setRate(RATES[(i + 1) % RATES.length]);
  }

  /** mode: 'off' | minutes (number) | 'chapter' */
  setSleep(mode) {
    clearInterval(this._sleepTimer);
    this._sleepTimer = null;
    if (mode === 'off' || mode == null) this.sleep = { mode: 'off', until: 0 };
    else if (mode === 'chapter') this.sleep = { mode: 'chapter', until: 0 };
    else {
      this.sleep = { mode: Number(mode), until: Date.now() + Number(mode) * 60000 };
      // The interval drives the countdown on screen; timeupdate (below) also checks the
      // wall clock, because timers are throttled while the phone is locked.
      this._sleepTimer = setInterval(() => this._checkSleep(), 1000);
    }
    this.emit('sleep');
  }

  sleepLeft() {
    return typeof this.sleep.mode === 'number' ? Math.max(0, this.sleep.until - Date.now()) : 0;
  }

  _checkSleep() {
    if (typeof this.sleep.mode !== 'number') return;
    if (Date.now() >= this.sleep.until) {
      this.pause();
      this.setSleep('off');
      this.emit('slept');
    } else {
      this.emit('sleeptick');
    }
  }

  /** Called whenever fresh book data arrives (polling). Never interrupts playback. */
  updateBook(book) {
    if (!this.book || this.book.id !== book.id) return;
    this.book = book;
    for (const c of book.chapters) if (c.parts && c.parts.length) this._durs(book, c);
    if (this.mode === 'part' && this._partObj) {
      // keep the freshest copy of the part being played (marks, duration)
      const ch = this.chapter;
      const fresh = ch && ch.parts && ch.parts[this.part];
      if (fresh && fresh.ready) this._partObj = fresh;
    }
    if (this.waiting && this._checkWait(book)) return;
    this._meta();
    this.emit('book');
  }

  /* ---------- waiting for audio that is still being made ---------- */

  _waitFor(kind, target) {
    this.waiting = kind;
    this._waitTarget = target;
    this.emit('state');
    clearTimeout(this._waitTimer);
    const tick = async () => {
      if (!this.waiting || !this.book) return;
      try {
        this.updateBook(await getBook(this.book.id));
      } catch {
        /* keep waiting */
      }
      if (this.waiting) this._waitTimer = setTimeout(tick, WAIT_POLL_MS);
    };
    this._waitTimer = setTimeout(tick, WAIT_POLL_MS);
  }

  /** Is what we're waiting for there now? Starts it and returns true. */
  _checkWait(book) {
    const tg = this._waitTarget || {};
    const ch = book.chapters.find((c) => c.index === tg.index);
    if (!ch) return false;
    if (this.waiting === 'part') {
      if (ch.status === 'ready' && ch.audio_url) {
        this._clearWait();
        this.load(book, ch.index, { time: tg.time || 0, autoplay: true });
        return true;
      }
      if (ch.parts && ch.parts[tg.part] && ch.parts[tg.part].ready) {
        this._clearWait();
        this._loadPart(book, ch.index, tg.part, 0, true);
        return true;
      }
      return false;
    }
    if (this.waiting === 'chapter') {
      if ((ch.status === 'ready' && (ch.audio_url || typeof ch.offset === 'number')) || (ch.parts && ch.parts[0] && ch.parts[0].ready)) {
        this._clearWait();
        this.load(book, ch.index, { time: 0, autoplay: true, part: ch.status === 'ready' ? null : 0 });
        return true;
      }
    }
    return false;
  }

  _clearWait() {
    if (this.waiting) {
      this.waiting = null;
      this._waitTarget = null;
      this.emit('state');
    }
    clearTimeout(this._waitTimer);
  }

  /** While a book is still being made, refresh it now and then so new parts and chapters are seen. */
  _liveUpdates() {
    clearInterval(this._liveTimer);
    if (!this.book || this.book.status === 'ready') return;
    this._liveTimer = setInterval(async () => {
      if (!this.book || this.book.status === 'ready' || !this.loaded) {
        clearInterval(this._liveTimer);
        return;
      }
      if (this.audio.paused && !this.waiting) return;
      try {
        this.updateBook(await getBook(this.book.id));
      } catch {
        /* offline for a moment */
      }
    }, LIVE_POLL_MS);
  }

  /** A part ended: next part, the chapter file, or the next chapter. */
  _afterPart() {
    const b = this.book;
    const ch = this.chapter;
    if (!b || !ch) return;
    const nextPart = this.part + 1;
    const chTime = this._partStart(b, ch, nextPart);
    const lastPart = nextPart >= this._durs(b, ch).length;
    if (lastPart) this._markDone(this.index);
    if (this.sleep.mode === 'chapter' && lastPart) {
      this.setSleep('off');
      this.emit('slept');
      this.emit('state');
      return;
    }
    if (ch.status === 'ready' && ch.audio_url) {
      // the chapter is finished now: carry on in its file at the same place
      if (lastPart) {
        if (!this.next({ auto: true })) this.emit('state');
      } else this.load(b, ch.index, { time: chTime, autoplay: true });
      return;
    }
    if (lastPart) {
      if (!this.next({ auto: true })) this.emit('state');
      return;
    }
    const pt = ch.parts && ch.parts[nextPart];
    if (pt && pt.ready) {
      this._loadPart(b, ch.index, nextPart, 0, true);
      return;
    }
    this._waitFor('part', { index: ch.index, part: nextPart, time: chTime });
  }

  _revokeBlob() {
    if (this._blob) {
      URL.revokeObjectURL(this._blob);
      this._blob = null;
    }
  }

  /** Stream mode: keep `index` in sync with the playhead. */
  _syncChapter() {
    if (this.mode !== 'stream' || !this.book) return;
    const c = this.chapterAtAbs(this.audio.currentTime || 0);
    if (!c || c.index === this.index) return;
    const prev = this.index;
    const natural = !this._seeking && !this.audio.paused && c.index > prev;
    if (natural) this._markDone(prev);
    this.index = c.index;
    this._meta();
    this._positionState();
    this.savePos();
    this.emit('track');
  }

  /* ---------- audio element events ---------- */

  _bind() {
    const a = this.audio;
    a.addEventListener('loadedmetadata', () => {
      if (this._pendingAbs != null) {
        const d = a.duration;
        const target = this._pendingAbs;
        this._pendingAbs = null;
        if (!Number.isFinite(d) || target < d - 0.5) {
          this._seeking = true;
          a.currentTime = target;
        }
      }
      a.playbackRate = this.rate;
      this._syncChapter();
      this._positionState();
      this.emit('time');
    });
    a.addEventListener('durationchange', () => this.emit('time'));
    a.addEventListener('seeking', () => (this._seeking = true));
    a.addEventListener('seeked', () => {
      this._seeking = false;
      this._syncChapter();
      this._positionState();
      this.emit('time');
    });
    a.addEventListener('timeupdate', () => {
      if (!this.loaded) return;
      if (this.mode === 'stream') {
        // "sleep at end of chapter" has no `ended` event inside one file: stop at the boundary
        if (this.sleep.mode === 'chapter' && !this._seeking && !a.paused) {
          const end = this.chapterStart + this.duration;
          const nxt = includedChapters(this.book).find((c) => c.index > this.index);
          if (nxt && a.currentTime >= end - 0.35) {
            a.pause();
            this._markDone(this.index);
            this._goTo(nxt.index);
            this.setSleep('off');
            this.emit('slept');
            return;
          }
        }
        this._syncChapter();
      }
      this.emit('time');
      this._posSoon();
      if (!a.paused) this._saveSoon();
      if (typeof this.sleep.mode === 'number') this._checkSleep();
    });
    const onState = () => {
      if ('mediaSession' in navigator) {
        try {
          navigator.mediaSession.playbackState = a.paused ? 'paused' : 'playing';
        } catch {
          /* ignore */
        }
      }
      this.emit('state');
    };
    a.addEventListener('play', onState);
    a.addEventListener('playing', () => {
      this.stalled = false;
      this._unlocked = true;
      if (!this._handlersSet) this._mediaSession(); // (re)install once playback has started (iOS)
      this._handlersSet = true;
      this._meta();
      this._positionState();
      onState();
    });
    // Pauses can come from the lock screen, AirPods or another app's audio.
    a.addEventListener('pause', () => {
      if (this.loaded) this.savePos();
      onState();
    });
    const onStall = () => {
      if (!a.paused) {
        this.stalled = true;
        this.emit('state');
      }
    };
    a.addEventListener('waiting', onStall);
    a.addEventListener('stalled', onStall);
    a.addEventListener('canplay', () => {
      if (this.stalled) {
        this.stalled = false;
        this.emit('state');
      }
    });
    a.addEventListener('ratechange', () => {
      // iOS resets playbackRate when the source changes; re-apply ours.
      if (this.loaded && Math.abs(a.playbackRate - this.rate) > 0.001 && a.readyState >= 1) a.playbackRate = this.rate;
      this._positionState();
    });
    a.addEventListener('ended', () => this._onEnded());
    a.addEventListener('error', () => this._onError());
    window.addEventListener('pagehide', () => this.loaded && this.savePos());
    document.addEventListener('visibilitychange', () => {
      // never pause or reload the element here, just remember where we are
      if (document.visibilityState === 'hidden' && this.loaded) this.savePos();
      if (document.visibilityState === 'visible') this._checkSleep();
    });
  }

  _onEnded() {
    if (!this.loaded) return;
    if (this.mode === 'part') {
      this._afterPart();
      return;
    }
    this._markDone(this.index);
    if (this.mode === 'stream') {
      if (this.sleep.mode === 'chapter') this.setSleep('off');
      this.emit('finished');
      this.emit('state');
      return;
    }
    if (this.sleep.mode === 'chapter') {
      this.setSleep('off');
      this.emit('slept');
      this.emit('state');
      return;
    }
    if (!this.next({ auto: true })) this.emit('state');
  }

  _onError() {
    if (!this.loaded || !this.audio.getAttribute('src')) return;
    const ch = this.chapter;
    const book = this.book;
    if (!ch || !book) return;
    if (this.mode === 'part') {
      // The part may be gone because its chapter was just finished: use the chapter file.
      const t = this.time;
      const wasPlaying = true;
      getBook(book.id)
        .then((fresh) => {
          this.updateBook(fresh);
          const c = fresh.chapters.find((x) => x.index === ch.index);
          if (c && c.status === 'ready' && c.audio_url) this.load(fresh, c.index, { time: t, autoplay: wasPlaying });
          else this._reportError();
        })
        .catch(() => this._reportError());
      return;
    }
    const url = this.mode === 'stream' ? book.stream_url : ch.audio_url;
    const resumeAbs = this._pendingAbs != null ? this._pendingAbs : this.audio.currentTime || 0;
    const wasPlaying = !this.audio.paused || this._pendingAbs != null;
    if (!this._srcRetried && !this._blob && navigator.onLine !== false) {
      // A tunnel can drop a single connection: pick the same file up again where we were,
      // before falling back to the offline copy or to the chapter files.
      this._srcRetried = true;
      this._pendingAbs = resumeAbs;
      this.audio.src = url;
      this.audio.load();
      if (wasPlaying) this._play();
      return;
    }
    if (!this._blobTried && offline.supported()) {
      this._blobTried = true;
      offline.blobUrl(url).then((blob) => {
        if (blob && this.book === book && this.index === ch.index) {
          this._revokeBlob();
          this._blob = blob;
          this._pendingAbs = resumeAbs;
          this.audio.src = blob;
          this._play();
        } else {
          this._fallbackOrReport(book, ch, resumeAbs, wasPlaying);
        }
      });
      return;
    }
    this._fallbackOrReport(book, ch, resumeAbs, wasPlaying);
  }

  _fallbackOrReport(book, ch, resumeAbs, wasPlaying) {
    // The single-file stream failed (e.g. server unreachable): fall back to chapter files,
    // which may be downloaded for offline use.
    if (this.mode === 'stream' && ch.audio_url) {
      this._streamBroken = book.stream_url;
      const t = Math.max(0, resumeAbs - (typeof ch.offset === 'number' ? ch.offset : 0));
      this._loadChapter(book, ch.index, t, true);
      return;
    }
    this._reportError();
  }

  _reportError() {
    this.emit('error', navigator.onLine === false ? 'You’re offline and this chapter isn’t downloaded.' : 'Couldn’t play this chapter. Check the connection to your server.');
    this.emit('state');
  }

  /* ---------- Media Session (lock screen / Control Center / AirPods) ---------- */

  _mediaSession() {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    const set = (action, fn) => {
      try {
        ms.setActionHandler(action, fn);
      } catch {
        /* unsupported action */
      }
    };
    set('play', () => this.play());
    set('pause', () => this.pause());
    set('stop', () => this.pause());
    set('seekbackward', (d) => this.seekBy(-((d && d.seekOffset) || BACK_SECONDS)));
    set('seekforward', (d) => this.seekBy((d && d.seekOffset) || FORWARD_SECONDS));
    set('previoustrack', () => this.prev());
    set('nexttrack', () => this.next());
    // positionState is reported per chapter, so seekTime is chapter-relative too
    set('seekto', (d) => {
      if (d && Number.isFinite(d.seekTime)) this.seekTo(d.seekTime);
    });
  }

  _meta() {
    if (!('mediaSession' in navigator) || !this.book || typeof MediaMetadata === 'undefined') return;
    const b = this.book;
    const ch = this.chapter;
    const artwork = b.cover_url
      ? [{ src: absolute(mediaUrl(b.cover_url)), sizes: '512x512', type: 'image/jpeg' }]
      : [{ src: absolute('icons/icon-512.png'), sizes: '512x512', type: 'image/png' }];
    const title = (ch && ch.title) || b.title;
    const cur = navigator.mediaSession.metadata;
    if (cur && cur.title === title && cur.album === b.title) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title,
        artist: b.author || b.voice_name || 'AuK Audiobooks',
        album: b.title,
        artwork,
      });
    } catch {
      /* ignore */
    }
  }

  _positionState() {
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
    const d = this.duration;
    if (!this.loaded || !Number.isFinite(d) || d <= 0) return;
    try {
      navigator.mediaSession.setPositionState({
        duration: d,
        playbackRate: this.audio.playbackRate || 1,
        position: clamp(this.time, 0, d),
      });
    } catch {
      /* ignore */
    }
  }
}

export const player = new Player();
