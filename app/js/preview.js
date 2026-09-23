// A separate, single <audio> for voice samples. Only one sample plays at a time,
// and starting one pauses the book.

import { Emitter, silentWav } from './util.js';
import { player } from './player.js';

class Preview extends Emitter {
  constructor() {
    super();
    this.audio = new Audio();
    this.audio.preload = 'auto';
    this.current = null; // voice id
    this.unlocked = false;
    const changed = () => this.emit('change');
    this.audio.addEventListener('playing', changed);
    this.audio.addEventListener('pause', changed);
    this.audio.addEventListener('ended', () => {
      this.current = null;
      changed();
    });
    this.audio.addEventListener('error', () => {
      if (this.current) {
        this.current = null;
        this.emit('error', 'Couldn’t play this sample.');
        changed();
      }
    });
    player.on('state', () => {
      if (player.playing && this.isPlaying()) this.stop();
    });
  }

  isPlaying(id) {
    return !!this.current && !this.audio.paused && (id == null || id === this.current);
  }

  /** Call synchronously inside a tap handler before a slow request. */
  unlock() {
    if (this.unlocked) return;
    this.unlocked = true;
    try {
      this.audio.src = silentWav();
      const p = this.audio.play();
      if (p && p.catch) p.catch(() => {});
    } catch {
      /* ignore */
    }
  }

  play(id, url) {
    if (player.playing) player.pause();
    this.current = id;
    this.audio.src = url;
    const p = this.audio.play();
    this.emit('change');
    if (p && p.catch) {
      p.catch((err) => {
        if (err && err.name === 'AbortError') return;
        this.current = null;
        this.emit('change');
        if (err && err.name === 'NotAllowedError') this.emit('blocked');
      });
    }
  }

  stop() {
    this.current = null;
    try {
      this.audio.pause();
    } catch {
      /* ignore */
    }
    this.emit('change');
  }
}

export const preview = new Preview();
