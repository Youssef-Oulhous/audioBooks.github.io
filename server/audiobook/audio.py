"""WAV I/O, trimming, loudness levelling, chapter assembly and AAC / M4B encoding (ffmpeg)."""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np


def ffmpeg_exe() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg  # ships a static ffmpeg build

    return imageio_ffmpeg.get_ffmpeg_exe()


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
        width = w.getsampwidth()
    if width != 2:
        raise ValueError(f"{path}: expected 16-bit PCM")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _frame_rms(audio: np.ndarray, sample_rate: int, frame_ms: float = 20.0) -> np.ndarray:
    n = max(1, int(sample_rate * frame_ms / 1000))
    usable = len(audio) // n * n
    if usable == 0:
        return np.array([float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0])
    frames = audio[:usable].reshape(-1, n)
    return np.sqrt(np.mean(frames**2, axis=1) + 1e-12)


def trim_silence(audio: np.ndarray, sample_rate: int, floor_db: float = -40.0, pad: float = 0.05) -> np.ndarray:
    """Cut leading/trailing silence (relative to the loudest 20 ms frame), keeping a little padding."""
    if len(audio) == 0:
        return audio
    rms = _frame_rms(audio, sample_rate)
    threshold = rms.max() * 10 ** (floor_db / 20)
    active = np.nonzero(rms > threshold)[0]
    if len(active) == 0:
        return audio[:0]
    frame = max(1, int(sample_rate * 0.02))
    start = max(0, active[0] * frame - int(pad * sample_rate))
    end = min(len(audio), (active[-1] + 1) * frame + int(pad * sample_rate))
    out = audio[start:end].copy()
    fade = min(len(out) // 4, int(0.01 * sample_rate))
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out


def level(audio: np.ndarray, sample_rate: int, target_db: float = -19.0, peak: float = 0.95, max_gain_db: float = 18.0) -> np.ndarray:
    """Match every chunk to the same speech loudness so volume doesn't jump between chunks."""
    if len(audio) == 0:
        return audio
    rms = _frame_rms(audio, sample_rate)
    voiced = rms[rms > rms.max() * 10 ** (-35 / 20)]
    if len(voiced) == 0:
        return audio
    current_db = 20 * np.log10(float(np.sqrt(np.mean(voiced**2))) + 1e-9)
    gain = 10 ** (min(max_gain_db, target_db - current_db) / 20)
    out = audio * gain
    top = float(np.max(np.abs(out)))
    if top > peak:
        out *= peak / top
    return out.astype(np.float32)


def silence(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(max(0, int(round(seconds * sample_rate))), dtype=np.float32)


def assemble(parts: list[tuple[Path, float]], sample_rate: int, lead_in: float = 0.6, tail: float = 1.4) -> np.ndarray:
    """Concatenate chunk WAVs, each followed by its pause."""
    pieces = [silence(lead_in, sample_rate)]
    for path, pause in parts:
        pieces.append(read_wav(path))
        pieces.append(silence(pause, sample_rate))
    pieces.append(silence(tail, sample_rate))
    return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-1500:]}")
    return proc


def _meta_args(meta: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in meta.items():
        args += ["-metadata", f"{key}={value}"]
    return args


def encode_m4a(wav: Path, out: Path, bitrate: str, meta: dict[str, str]) -> None:
    tmp = out.with_suffix(".tmp.m4a")
    _run([ffmpeg_exe(), "-hide_banner", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", bitrate, "-ac", "1",
          *_meta_args(meta), "-movflags", "+faststart", "-f", "mp4", str(tmp)])
    tmp.replace(out)


def encode_mp3(wav: Path, out: Path, bitrate: str, meta: dict[str, str]) -> None:
    tmp = out.with_suffix(".tmp.mp3")
    _run([ffmpeg_exe(), "-hide_banner", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", bitrate, "-ac", "1",
          *_meta_args(meta), "-id3v2_version", "3", "-f", "mp3", str(tmp)])
    tmp.replace(out)


def encode_flac(wav: Path, out: Path) -> None:
    """Lossless chapter master: the whole-book files are encoded once from these, so chapter
    offsets are sample-exact (joining already-compressed files would drift at every seam)."""
    tmp = out.with_suffix(".tmp.flac")
    _run([ffmpeg_exe(), "-hide_banner", "-y", "-i", str(wav), "-c:a", "flac", "-compression_level", "5", "-f", "flac", str(tmp)])
    tmp.replace(out)


def encode_chapter(wav: Path, base: Path, aac_bitrate: str, mp3_bitrate: str, meta: dict[str, str]) -> None:
    """A finished chapter's three files at once: .m4a (streaming), .mp3 (download), .flac (master)."""
    targets = {
        ".m4a": ["-c:a", "aac", "-b:a", aac_bitrate, "-ac", "1", *_meta_args(meta), "-movflags", "+faststart", "-f", "mp4"],
        ".mp3": ["-c:a", "libmp3lame", "-b:a", mp3_bitrate, "-ac", "1", *_meta_args(meta), "-id3v2_version", "3", "-f", "mp3"],
        ".flac": ["-c:a", "flac", "-compression_level", "5", "-f", "flac"],
    }
    procs = {
        ext: subprocess.Popen([ffmpeg_exe(), "-hide_banner", "-y", "-i", str(wav), *args, str(base.with_suffix(".tmp" + ext))],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        for ext, args in targets.items()
    }
    for ext, proc in procs.items():
        err = proc.communicate()[1]
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err[-1500:]}")
        base.with_suffix(".tmp" + ext).replace(base.with_suffix(ext))


def decode_to_wav(src: Path, out: Path, sample_rate: int) -> None:
    _run([ffmpeg_exe(), "-hide_banner", "-y", "-i", str(src), "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(out)])


def media_seconds(path: Path) -> float:
    """Duration as ffmpeg reports it (includes AAC priming), for exact chapter marks."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise RuntimeError(f"Could not read duration of {path}")
    h, mnt, sec = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(sec)


def _concat_line(path: Path) -> str:
    return "file '" + str(path.resolve()).replace("'", "'\\''") + "'\n"


def _meta_escape(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def build_book(
    masters: list[tuple[Path, str, float]],
    m4b_out: Path,
    mp3_out: Path,
    meta: dict[str, str],
    cover: Path | None,
    aac_bitrate: str,
    mp3_bitrate: str,
) -> list[float]:
    """Encode the whole book once from the chapter masters (FLAC): an .m4b (AAC, chapter markers,
    cover — also used as the player's continuous stream) and an .mp3 (ID3 chapter markers, cover).
    `masters` = (flac path, chapter title, exact seconds). Returns each chapter's start offset."""
    work = m4b_out.parent
    listing = work / "book_concat.txt"
    listing.write_text("".join(_concat_line(p) for p, _, _ in masters), encoding="utf-8")
    offsets, lines, start = [], [";FFMETADATA1"] + [f"{k}={_meta_escape(v)}" for k, v in meta.items()], 0.0
    for _, title, seconds in masters:
        offsets.append(round(start, 3))
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(start * 1000)}", f"END={round((start + seconds) * 1000)}",
                  f"title={_meta_escape(title)}"]
        start += seconds
    meta_file = work / "book_meta.txt"
    meta_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    has_cover = bool(cover and cover.exists())
    inputs = [ffmpeg_exe(), "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(meta_file)]
    if has_cover:
        inputs += ["-i", str(cover)]
    art = ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"] if has_cover else []
    tmp_m4b, tmp_mp3 = m4b_out.with_suffix(".tmp.m4b"), mp3_out.with_suffix(".tmp.mp3")
    jobs = [
        inputs + ["-map", "0:a", *art, "-c:a", "aac", "-b:a", aac_bitrate, "-ac", "1", "-map_metadata", "1", "-map_chapters", "1",
                  "-movflags", "+faststart", "-f", "mp4", str(tmp_m4b)],
        inputs + ["-map", "0:a", *art, "-c:a", "libmp3lame", "-b:a", mp3_bitrate, "-ac", "1", "-map_metadata", "1",
                  "-map_chapters", "1", "-id3v2_version", "3", "-f", "mp3", str(tmp_mp3)],
    ]
    procs = [subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) for cmd in jobs]
    errors = [p.communicate()[1] for p in procs]
    for proc, err in zip(procs, errors):
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err[-1500:]}")
    tmp_m4b.replace(m4b_out)
    tmp_mp3.replace(mp3_out)
    listing.unlink(missing_ok=True)
    meta_file.unlink(missing_ok=True)
    return offsets
