"""Speech: accents, text checks, lazy voice loading, and (slow) real Piper synthesis."""

import io
import wave

import pytest
from fakes import fake_speaker

from spanish_tutor.speech import (
    MAX_CHARS,
    VOICE_DIR,
    VOICES,
    Speaker,
    SpeechError,
    VoiceMissing,
    installed,
    sha256,
)


def seconds(audio: bytes) -> float:
    with wave.open(io.BytesIO(audio), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def test_text_is_spoken_as_a_wav_file(tmp_path):
    speaker, loads = fake_speaker(tmp_path)
    audio = speaker.wav("  Hola, ¿qué tal?  ", "mx")
    assert audio[:4] == b"RIFF" and seconds(audio) == pytest.approx(0.15, abs=0.01)  # 15 characters
    ((_, voice),) = loads
    assert voice.said[0][0] == "Hola, ¿qué tal?"  # stripped


def test_each_voice_loads_once_on_first_use(tmp_path):
    speaker, loads = fake_speaker(tmp_path)
    assert loads == []  # nothing loaded until something is said
    speaker.wav("uno", "mx")
    speaker.wav("dos", "mx")
    speaker.wav("tres", "es")
    assert [path.name for path, _ in loads] == [
        "es_MX-claude-high.onnx",
        "es_ES-davefx-medium.onnx",
    ]


def test_the_mexican_voice_is_tuned_and_the_spanish_one_is_not(tmp_path):
    speaker, loads = fake_speaker(tmp_path)
    speaker.wav("hola", "mx")
    speaker.wav("hola", "es")
    mx_config = loads[0][1].said[0][1]
    es_config = loads[1][1].said[0][1]
    assert (mx_config.noise_scale, mx_config.noise_w_scale) == (0.333, 0.333)
    assert (es_config.noise_scale, es_config.noise_w_scale) == (None, None)  # the voice's own


def test_the_default_accent_is_mexican(tmp_path):
    speaker, loads = fake_speaker(tmp_path)
    speaker.wav("hola")
    assert loads[0][0].name == "es_MX-claude-high.onnx"


@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_CHARS + 1)])
def test_empty_or_too_long_text_is_refused(tmp_path, text):
    speaker, _ = fake_speaker(tmp_path)
    with pytest.raises(SpeechError):
        speaker.wav(text, "mx")


def test_an_unknown_accent_is_refused(tmp_path):
    speaker, _ = fake_speaker(tmp_path)
    with pytest.raises(SpeechError, match="Unknown accent"):
        speaker.wav("hola", "ar")


def test_a_voice_that_isnt_downloaded_says_how_to_get_it(tmp_path):
    speaker, loads = fake_speaker(tmp_path, accents=("es",))
    assert speaker.available() == {"mx": False, "es": True}
    with pytest.raises(VoiceMissing, match="speech download"):
        speaker.wav("hola", "mx")
    assert loads == []


def test_sha256_of_a_file(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert sha256(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.slow
@pytest.mark.skipif(not all(installed(v) for v in VOICES.values()), reason="voices not downloaded")
@pytest.mark.parametrize("accent", list(VOICES))
def test_real_voices_speak_a_sentence(accent):
    """The downloaded models are the pinned ones, and they speak (~0.3 s for a sentence)."""
    voice = VOICES[accent]
    assert sha256(VOICE_DIR / voice.files[0]) == voice.sha256
    audio = Speaker().wav("Mi familia y yo vivimos en una casa pequeña cerca del mar.", accent)
    assert 2.5 < seconds(audio) < 8
