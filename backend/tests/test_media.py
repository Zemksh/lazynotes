import io
import wave

import pytest

from app.config import settings
from app.media import ProcessingError, decode_audio


def test_whisper_file_decoder_compatibility(tmp_path, audio):
    whisper_audio = pytest.importorskip("faster_whisper.audio")
    source = tmp_path / "recording.wav"
    source.write_bytes(audio)
    samples = whisper_audio.decode_audio(str(source), sampling_rate=16000)
    assert samples.shape == (16000,)
    assert not samples.any()


def test_duration_limit_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_DURATION_SECONDS", "1")
    settings.cache_clear()
    source = tmp_path / "audio.upload"
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 32000)
    source.write_bytes(buffer.getvalue())
    with pytest.raises(ProcessingError, match="Choose a recording"):
        decode_audio(source, tmp_path / "decoded.wav")
    assert not (tmp_path / "decoded.wav").exists()


def test_external_file_playlist_is_rejected(tmp_path, audio):
    media = tmp_path / "external.wav"
    media.write_bytes(audio)
    source = tmp_path / "audio.upload"
    source.write_text(f"ffconcat version 1.0\nfile '{media}'\n")
    with pytest.raises(ProcessingError, match="could not be read"):
        decode_audio(source, tmp_path / "decoded.wav")
