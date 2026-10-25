import io
import wave

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("MOCK_MODE", "true")
    settings.cache_clear()
    db.init_db()
    yield
    settings.cache_clear()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.main.process_job.apply_async", lambda **kwargs: None)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def audio():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    return buffer.getvalue()
