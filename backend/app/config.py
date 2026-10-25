from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    storage_path: Path = Path("./data")
    redis_url: str = "redis://localhost:6379/0"
    mock_mode: bool = False
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = Field(4, ge=1)
    model_cache_path: Path = Path("./data/models")
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout_seconds: int = Field(300, ge=1)
    max_audio_mb: int = Field(250, ge=1)
    max_pdf_mb: int = Field(10, ge=1)
    max_duration_seconds: int = Field(10800, ge=1)
    result_retention_hours: int = Field(72, ge=1)
    pending_retention_hours: int = Field(24, ge=1)
    job_time_limit_seconds: int = Field(14400, ge=60)
    chunk_characters: int = Field(10000, ge=2000, le=16000)
    context_characters: int = Field(4000, ge=500, le=8000)

    @property
    def db_path(self) -> Path:
        return self.storage_path / "jobs.sqlite3"


@lru_cache
def settings() -> Settings:
    return Settings()
