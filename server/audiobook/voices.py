"""Narrator voices.

AuK: a voice is a natural-language description ("Instruct TTS"). The first time a voice is
used, AuK invents it from the description and speaks a short sample sentence; that sample is
kept and becomes the reference audio for every chunk of a book ("zero-shot TTS"), so the
narrator sounds the same from the first page to the last.

Kokoro (via Pipecat): voices are the model's fixed speakers; the sample is just a preview.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from . import audio
from .config import settings
from .engine_manager import engine_manager
from .textprep import estimate_seconds


SAMPLE_TEXT = {
    "en": "The old house stood at the end of the lane, quiet and patient, as if it had been waiting a long time for someone to come home.",
    "zh": "那座老房子静静地立在小路的尽头，安静而耐心，仿佛已经等了很久，等着有人回家。",
    "fr": "La vieille maison se dressait au bout du chemin, calme et patiente, comme si elle attendait depuis longtemps que quelqu'un rentre enfin.",
    "es": "La vieja casa se alzaba al final del camino, tranquila y paciente, como si llevara mucho tiempo esperando a que alguien volviera a casa.",
    "it": "La vecchia casa si ergeva in fondo al sentiero, quieta e paziente, come se aspettasse da tempo che qualcuno tornasse a casa.",
    "pt": "A velha casa ficava no fim da estrada, calma e paciente, como se esperasse há muito tempo que alguém voltasse para casa.",
    "hi": "पुराना घर गली के आखिर में शांत और धैर्य से खड़ा था, मानो बहुत समय से किसी के घर लौटने का इंतज़ार कर रहा हो।",
    "ja": "古い家は小道の突き当たりに静かに立っていた。まるで誰かが帰ってくるのを、ずっと待っているかのように。",
}


@dataclass
class Voice:
    id: str
    name: str
    language: str
    gender: str
    tagline: str
    description: str
    seed: int
    custom: bool = False
    engine_voice: str | None = None  # fixed speaker id for preset engines (Kokoro)
    accent: str = ""


AUK_PRESETS = [
    Voice("james", "James", "en", "male", "Deep, calm classic narrator",
          "A calm, mature male narrator in his fifties with a deep, warm and resonant voice. He reads an audiobook "
          "at a steady, unhurried pace with clear articulation, gentle expression and natural pauses.", 1101),
    Voice("ava", "Ava", "en", "female", "Warm, gentle storyteller",
          "A warm female storyteller in her early thirties with a soft, clear and friendly voice. She narrates an "
          "audiobook at a relaxed pace, expressive but never theatrical, with a smooth and soothing tone.", 2202),
    Voice("marcus", "Marcus", "en", "male", "Crisp, confident non-fiction",
          "A confident, articulate man in his thirties with a crisp, bright and engaging voice. He reads non-fiction "
          "at a lively but comfortable pace, sounding knowledgeable, clear and energetic.", 3303),
    Voice("eleanor", "Eleanor", "en", "female", "Wise, elegant classics reader",
          "An elegant elderly woman with a refined, gentle and slightly low voice, full of warmth and wisdom. She reads "
          "classic literature slowly and thoughtfully, with graceful phrasing and quiet emotion.", 4404),
    Voice("leo", "Leo", "en", "male", "Relaxed, friendly and modern",
          "A young man in his twenties with a relaxed, friendly and natural voice. He reads like he is telling a story "
          "to a friend, conversational and easygoing, at a natural pace with a light smile in his voice.", 5505),
    Voice("sophie", "Sophie", "en", "female", "Bright, lively young voice",
          "A bright, cheerful young woman with a light, clear and lively voice. She reads young-adult and children's "
          "stories with energy and playful expression, at a brisk but easy-to-follow pace.", 6606),
    Voice("victor", "Victor", "en", "male", "Dramatic baritone for thrillers",
          "A dramatic male baritone with a rich, dark and slightly husky voice. He narrates thrillers and fantasy with "
          "suspense and gravity, measured pace, deliberate pauses and a sense of mystery.", 7707),
    Voice("mei", "Mei", "zh", "female", "温柔知性的女声",
          "一位三十岁左右的女性，声音温柔、清澈而知性，普通话标准。她以平稳舒缓的语速朗读有声书，吐字清晰，语气自然亲切，富有感情但不夸张。", 8808),
    Voice("chen", "Chen", "zh", "male", "沉稳醇厚的男中音",
          "一位四十多岁的男性，嗓音低沉醇厚、富有磁性，普通话标准。他以沉稳从容的节奏朗读有声书，停顿自然，情感真挚，娓娓道来。", 9909),
]


def _k(key: str, name: str, lang: str, gender: str, tagline: str, accent: str = "") -> Voice:
    return Voice(f"k_{key}", name, lang, gender, tagline, f"Kokoro voice {key}", 0, engine_voice=key, accent=accent)


# The two Kokoro v1.0 speakers the app offers (Pipecat's Kokoro service). Any other Kokoro voice id
# (e.g. ff_siwis for French) can be added here with one line.
KOKORO_PRESETS = [
    _k("af_heart", "Heart", "en", "female", "Warm, natural storyteller", "American"),
    _k("bm_george", "George", "en", "male", "Classic British narrator", "British"),
]


def _presets() -> list[Voice]:
    return KOKORO_PRESETS if settings.engine == "kokoro" else AUK_PRESETS


def _custom_allowed() -> bool:
    return settings.engine != "kokoro"


class VoiceStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.dir = settings.voices_dir
        self.samples = self.dir / "samples"
        self.custom_file = self.dir / "custom.json"

    def init(self) -> None:
        self.samples.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- catalogue

    def _custom(self) -> list[Voice]:
        if not self.custom_file.exists():
            return []
        return [Voice(**v) for v in json.loads(self.custom_file.read_text(encoding="utf-8"))]

    def _save_custom(self, voices: list[Voice]) -> None:
        tmp = self.custom_file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(v) for v in voices], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.custom_file)

    def all(self) -> list[Voice]:
        with self.lock:
            return _presets() + (self._custom() if _custom_allowed() else [])

    def get(self, voice_id: str) -> Voice | None:
        return next((v for v in self.all() if v.id == voice_id), None)

    def create(self, name: str, description: str, language: str, gender: str) -> Voice:
        if not _custom_allowed():
            raise ValueError("The Kokoro engine has fixed voices; custom voices need the AuK engine.")
        name = re.sub(r"\s+", " ", name).strip()[:40] or "My voice"
        description = re.sub(r"\s+", " ", description).strip()
        if len(description) < 10:
            raise ValueError("Describe the voice in a sentence or two (age, tone, pace…).")
        if len(description) > 600:
            raise ValueError("Keep the description under 600 characters.")
        voice = Voice(
            id="v_" + uuid.uuid4().hex[:8], name=name, language=language if language in ("en", "zh") else "en",
            gender=gender if gender in ("male", "female") else "female", tagline="Your custom voice",
            description=description, seed=int(time.time()) % 100000, custom=True,
        )
        with self.lock:
            self._save_custom(self._custom() + [voice])
        return voice

    def delete(self, voice_id: str) -> None:
        with self.lock:
            voices = self._custom()
            if not any(v.id == voice_id for v in voices):
                raise KeyError(voice_id)
            self._save_custom([v for v in voices if v.id != voice_id])
            for suffix in (".wav", ".json"):
                (self.samples / f"{voice_id}{suffix}").unlink(missing_ok=True)

    # ---------------------------------------------------------------- samples

    def sample_meta(self, voice_id: str) -> dict | None:
        meta = self.samples / f"{voice_id}.json"
        wav = self.samples / f"{voice_id}.wav"
        if not (meta.exists() and wav.exists()):
            return None
        return json.loads(meta.read_text(encoding="utf-8"))

    def sample_path(self, voice_id: str) -> Path:
        return self.samples / f"{voice_id}.wav"

    def ensure_sample(self, voice: Voice, retake: bool = False) -> dict:
        """Create the voice's reference sample (or a fresh take). Blocks while the model works."""
        with self.lock:
            meta = self.sample_meta(voice.id)
            if meta and not retake:
                return meta
            take = (meta["take"] + 1) if meta else 1
        text = SAMPLE_TEXT.get(voice.language, SAMPLE_TEXT["en"])
        seed = voice.seed + (take - 1) * 7919
        seconds = estimate_seconds(text, voice.language)

        def make():
            engine = engine_manager.engine
            if engine.voice_mode == "preset":
                return engine.speak_preset(voice.engine_voice, text, voice.language, 1.0), engine.sample_rate
            return engine.design_voice(voice.description, text, voice.language, seconds, seed), engine.sample_rate

        raw, sample_rate = engine_manager.run(make, priority=True)
        clip = audio.level(audio.trim_silence(raw, sample_rate), sample_rate)
        if len(clip) < sample_rate:  # under a second: the model produced (near) silence
            raise RuntimeError("The voice sample came out silent. Try another take.")
        with self.lock:
            audio.write_wav(self.sample_path(voice.id), clip, sample_rate)
            meta = {"take": take, "seed": seed, "text": text, "seconds": round(len(clip) / sample_rate, 3),
                    "language": voice.language, "created_at": int(time.time())}
            tmp = self.samples / f"{voice.id}.json.tmp"
            tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.samples / f"{voice.id}.json")
        return meta

    def to_api(self, voice: Voice) -> dict:
        meta = self.sample_meta(voice.id)
        return {
            "id": voice.id, "name": voice.name, "language": voice.language, "gender": voice.gender,
            "tagline": voice.tagline, "description": voice.description, "custom": voice.custom,
            "accent": voice.accent, "retakable": voice.engine_voice is None,
            "preview_url": f"/api/voices/{voice.id}/preview.wav?v={meta['take']}" if meta else None,
            "take": meta["take"] if meta else 0,
        }


voice_store = VoiceStore()
