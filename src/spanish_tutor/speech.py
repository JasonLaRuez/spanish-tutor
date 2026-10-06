"""Spanish speech for every skill: text-to-speech with Piper, offline on the CPU.

The tutor's replies, clicked words, lesson cards and narrated texts are all spoken by the
same small engine. Piper (GPL-3.0) was chosen because it's free and runs locally, so
anyone can use the project without a paid service (Jason, 2026-10-06).

Voices (Jason's choices, 2026-10-06, after listening to samples):
- Mexican Spanish, the default: es_MX-claude-high (Apache-2.0) with less generator noise
  (0.333 and 0.333, against Piper's 0.667 and 0.8). At the defaults both Mexican voices
  sounded distorted and robotic; of four tuning variants on three sentences, this was the
  best. Faster speech didn't help. There was no clipping (at most 0.004% of samples near
  full scale).
- Spain: es_ES-davefx-medium (CC0) at the defaults, which sounds like a real person.

Measured on Jason's CPU: a voice loads in ~1.5-2 s; synthesis runs ~26x faster than real
time (a 112-character reply in 0.27 s, 3 sentences in ~0.6 s). So audio is made on demand,
a sentence at a time, and nothing is cached on disk; the browser caches what it has played.

The voice files (63 MB each) are pinned to one commit of the rhasspy/piper-voices
repository and checked against their SHA-256 when downloaded, like the embedding model's
pinned revision.

    python -m spanish_tutor.speech download         # both voices, into data/raw/piper/
    python -m spanish_tutor.speech say "Hola" --accent es --out hola.wav
"""

import argparse
import hashlib
import io
import shutil
import sys
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.request import urlopen

from spanish_tutor.config import DATA_DIR

VOICE_DIR = DATA_DIR / "raw" / "piper"
REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"  # rhasspy/piper-voices, 2026-09-17
URL = "https://huggingface.co/rhasspy/piper-voices/resolve/{revision}/{folder}/{file}"
MAX_CHARS = 1500  # a long paragraph; the reader sends one sentence at a time


@dataclass(frozen=True)
class Voice:
    accent: str
    label: str
    name: str  # Piper's voice name: the file stem
    folder: str  # in the voices repository
    sha256: str  # of the .onnx model
    license: str
    noise_scale: float | None = None  # None: the voice's own default
    noise_w_scale: float | None = None

    @property
    def files(self) -> tuple[str, str]:
        return f"{self.name}.onnx", f"{self.name}.onnx.json"


VOICES = {
    "mx": Voice(
        "mx",
        "México",
        "es_MX-claude-high",
        "es/es_MX/claude/high",
        "3ef40a71ea63852cd8ab7e6fa7d2ecdcfa67a0b47c9c48e3f10e02ee02083ea0",
        "Apache-2.0",
        noise_scale=0.333,
        noise_w_scale=0.333,
    ),
    "es": Voice(
        "es",
        "España",
        "es_ES-davefx-medium",
        "es/es_ES/davefx/medium",
        "6658b03b1a6c316ee4c265a9896abc1393353c2d9e1bca7d66c2c442e222a917",
        "CC0",
    ),
}
DEFAULT_ACCENT = "mx"


class SpeechError(ValueError):
    """A request that can't be spoken: empty or too-long text, or an unknown accent."""


class VoiceMissing(RuntimeError):
    """The voice's files aren't downloaded."""


class Synthesizer(Protocol):
    def synthesize_wav(self, text: str, wav_file: wave.Wave_write, **kwargs: Any) -> None:
        """Write `text` as speech into an open WAV file (piper.PiperVoice)."""


def voice_for(accent: str) -> Voice:
    if accent not in VOICES:
        raise SpeechError(f"Unknown accent {accent!r}: use one of {', '.join(VOICES)}.")
    return VOICES[accent]


def checked_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise SpeechError("There's no text to say.")
    if len(text) > MAX_CHARS:
        raise SpeechError(f"Text is too long to say at once ({len(text)} > {MAX_CHARS}).")
    return text


def installed(voice: Voice, directory: Path = VOICE_DIR) -> bool:
    return all((directory / file).exists() for file in voice.files)


def download(voice: Voice, directory: Path = VOICE_DIR) -> None:
    """Fetch the voice's model and config at the pinned revision; check the model's hash."""
    directory.mkdir(parents=True, exist_ok=True)
    for file in voice.files:
        target = directory / file
        if target.exists():
            continue
        partial = target.with_name(target.name + ".part")
        url = URL.format(revision=REVISION, folder=voice.folder, file=file)
        with urlopen(url) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        if file.endswith(".onnx") and (digest := sha256(partial)) != voice.sha256:
            partial.unlink()
            raise RuntimeError(f"{file}: SHA-256 {digest} doesn't match the pinned {voice.sha256}.")
        partial.replace(target)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_piper(path: Path) -> Synthesizer:
    from piper import PiperVoice

    return PiperVoice.load(str(path))


class Speaker:
    """Speaks text in one of the accents. Each voice loads on first use (~1.5-2 s) and
    stays loaded; one lock per voice keeps synthesis to one request at a time."""

    def __init__(
        self,
        directory: Path = VOICE_DIR,
        load: Callable[[Path], Synthesizer] = _load_piper,
    ):
        self.directory = directory
        self._load = load
        self._models: dict[str, Synthesizer] = {}
        self._locks = {accent: threading.Lock() for accent in VOICES}

    def available(self) -> dict[str, bool]:
        """Which accents can be spoken (their voice files are downloaded)."""
        return {accent: installed(voice, self.directory) for accent, voice in VOICES.items()}

    def wav(self, text: str, accent: str = DEFAULT_ACCENT) -> bytes:
        """`text` spoken in `accent`, as a WAV file."""
        voice = voice_for(accent)
        text = checked_text(text)
        with self._locks[accent]:
            model = self._model(voice)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                model.synthesize_wav(text, wav_file, syn_config=synthesis_config(voice))
        return buffer.getvalue()

    def _model(self, voice: Voice) -> Synthesizer:
        if voice.accent not in self._models:
            if not installed(voice, self.directory):
                raise VoiceMissing(
                    f"The {voice.label} voice isn't downloaded. "
                    "Run: uv run python -m spanish_tutor.speech download"
                )
            self._models[voice.accent] = self._load(self.directory / voice.files[0])
        return self._models[voice.accent]


def synthesis_config(voice: Voice) -> Any:
    from piper import SynthesisConfig

    return SynthesisConfig(noise_scale=voice.noise_scale, noise_w_scale=voice.noise_w_scale)


def main() -> None:
    parser = argparse.ArgumentParser(description="Spanish speech (Piper voices).")
    commands = parser.add_subparsers(dest="command", required=True)
    get = commands.add_parser("download", help="download the voices (63 MB each)")
    get.add_argument("--accent", choices=list(VOICES), nargs="*", default=list(VOICES))
    say = commands.add_parser("say", help="speak a text into a WAV file")
    say.add_argument("text")
    say.add_argument("--accent", choices=list(VOICES), default=DEFAULT_ACCENT)
    say.add_argument("--out", type=Path, default=Path("speech.wav"))
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "download":
        for accent in args.accent:
            voice = VOICES[accent]
            print(f"{voice.label}: {voice.name} ({voice.license}) …", flush=True)
            download(voice)
        print(f"Voices in {VOICE_DIR}")
    else:
        args.out.write_bytes(Speaker().wav(args.text, args.accent))
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
