from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import settings
from .media import ProcessingError
from .schemas import ReviewItem, Segment


@dataclass(frozen=True)
class Diagnostics:
    segment_id: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float


def review_diagnostics(items: list[Diagnostics]) -> list[ReviewItem]:
    """Replaceable heuristic interface, NOT calibrated correctness/confidence."""
    return [
        ReviewItem(
            reason="Some words may be unclear. Check them against the recording.",
            source_segment_ids=[item.segment_id],
            kind="heuristic",
        )
        for item in items
        if item.avg_logprob < -1.0 or item.compression_ratio > 2.4
    ][:200]


@lru_cache(maxsize=1)
def whisper_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ProcessingError(
            "Transcription is not installed on the server. Install the backend transcription dependencies and retry."
        ) from None
    cfg = settings()
    return WhisperModel(
        cfg.whisper_model,
        device=cfg.whisper_device,
        compute_type=cfg.whisper_compute_type,
        cpu_threads=cfg.whisper_cpu_threads,
        download_root=str(cfg.model_cache_path),
    )


def transcribe(path: Path) -> tuple[list[Segment], list[Diagnostics]]:
    # faster-whisper restores original time offsets after Silero VAD filtering.
    chunks, _ = whisper_model().transcribe(
        str(path), vad_filter=True, beam_size=5, condition_on_previous_text=False
    )
    segments, diagnostics = [], []
    for raw in chunks:
        if not raw.text.strip():
            continue
        sid = f"s{len(segments) + 1:05d}"
        segments.append(Segment(id=sid, start=raw.start, end=raw.end, text=raw.text))
        diagnostics.append(Diagnostics(sid, raw.avg_logprob, raw.no_speech_prob, raw.compression_ratio))
    if not segments:
        raise ProcessingError("No speech was found. Check the recording and try a clearer audio file.")
    return segments, diagnostics
