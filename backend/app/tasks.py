import json
import logging
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from celery import Celery
from filelock import FileLock, Timeout

from . import db
from .config import settings
from .generation import generate_notes
from .media import ProcessingError, decode_audio, extract_syllabus
from .schemas import Notes, Result, Section, Segment
from .transcription import review_diagnostics, transcribe

cfg = settings()
celery = Celery("lazynotes", broker=cfg.redis_url)
celery.conf.update(
    task_ignore_result=True,
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=cfg.job_time_limit_seconds,
    task_soft_time_limit=max(30, cfg.job_time_limit_seconds - 30),
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": cfg.job_time_limit_seconds + 300},
    beat_schedule={
        "retention-cleanup": {"task": "app.tasks.cleanup", "schedule": 300.0},
        "recover-dispatch": {"task": "app.tasks.recover_dispatch", "schedule": 60.0},
    },
)
logger = logging.getLogger(__name__)


def safe_traceback(exc: BaseException) -> str:
    """Keep code locations and chained types, never messages, source lines or locals."""
    lines, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        lines.append(type(exc).__name__)
        frame = exc.__traceback__
        while frame is not None:
            code = frame.tb_frame.f_code
            lines.append(f"  {code.co_filename}:{frame.tb_lineno} in {code.co_name}")
            frame = frame.tb_next
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        if exc is not None:
            lines.append("Caused by / during handling of:")
    return "\n".join(lines)


def job_path(job_id: str) -> Path:
    return settings().storage_path / "jobs" / job_id


def job_lock(job_id: str) -> FileLock:
    root = settings().storage_path / "locks"
    root.mkdir(parents=True, exist_ok=True)
    return FileLock(root / f"{job_id}.lock")


def discard_inputs(folder: Path):
    for name in ("audio.upload", "decoded.wav", "syllabus.pdf", "input.json"):
        (folder / name).unlink(missing_ok=True)


def mock_result(job_id: str, duration: float, title: str) -> Result:
    transcript = [
        Segment(
            id="s00001",
            start=0,
            end=min(duration, 3),
            text="This is an example transcript for development. It does not describe your recording.",
        )
    ]
    return Result(
        job_id=job_id,
        mock=True,
        duration_seconds=duration,
        transcript=transcript,
        notes=Notes(
            title=title,
            sections=[
                Section(
                    heading="Development example",
                    body="Example notes for testing this app. Your recording was not transcribed.",
                    source_segment_ids=["s00001"],
                )
            ],
        ),
        warnings=["Development mock mode: transcript and notes are examples, not your class."],
    )


@celery.task(name="app.tasks.process_job")
def process_job(job_id: str):
    db.init_db()
    # Also enforce single processing if an operator accidentally starts multiple workers.
    with FileLock(settings().storage_path / "processing.lock"):
        with job_lock(job_id):
            job = db.get_job(job_id)
            if not job or job["state"] in {"ready", "failed", "deleted"}:
                return
            folder = job_path(job_id)
            started = time.monotonic()
            stage = "load_input"
            try:
                if job["expires_at"] <= time.time():
                    raise ProcessingError("This pending class expired. Please upload it again.")
                # A redelivery after a worker crash fails explicitly; it never silently duplicates output.
                if job["state"] != "queued":
                    raise ProcessingError(
                        "Processing was interrupted on the server. Please upload this class again."
                    )
                data = json.loads((folder / "input.json").read_text())
                db.transition(job_id, "transcribing")
                stage = "decode_audio"
                duration = decode_audio(folder / "audio.upload", folder / "decoded.wav")
                stage = "extract_syllabus"
                syllabus = data["syllabus_text"]
                if (folder / "syllabus.pdf").exists():
                    syllabus = extract_syllabus(folder / "syllabus.pdf")
                if job["mock"]:
                    stage = "mock_result"
                    db.transition(job_id, "preparing")
                    result = mock_result(job_id, duration, job["title"])
                else:
                    stage = "transcribe"
                    transcript, diagnostics = transcribe(folder / "decoded.wav")
                    stage = "save_transcript"
                    # Keep the raw transcript distinct from notes and diagnostics.
                    (folder / "transcript.json").write_text(json.dumps([s.model_dump() for s in transcript]))
                    (folder / "diagnostics.json").write_text(json.dumps([asdict(d) for d in diagnostics]))
                    db.transition(job_id, "preparing")
                    stage = "generate_notes"
                    notes = generate_notes(transcript, data["topics"], syllabus, job["title"])
                    stage = "assemble_result"
                    notes.review_items.extend(review_diagnostics(diagnostics))
                    result = Result(
                        job_id=job_id,
                        mock=False,
                        duration_seconds=duration,
                        notes=notes,
                        transcript=transcript,
                        warnings=[
                            "Unclear passages may be flagged, but not every transcription mistake can be detected."
                        ],
                    )
                # Revalidate after consolidation / adding heuristic flags.
                stage = "validate_result"
                result = Result.model_validate_json(result.model_dump_json())
                if db.get_job(job_id)["state"] == "deleted":
                    return
                temp = folder / "result.tmp"
                stage = "save_result"
                temp.write_text(result.model_dump_json())
                temp.replace(folder / "result.json")
                db.transition(job_id, "ready", result_location="result.json")
            except Exception as exc:
                logger.error(
                    "Job %s failed stage=%s elapsed_seconds=%.1f\n%s",
                    job_id, stage, time.monotonic() - started, safe_traceback(exc),
                )
                current = db.get_job(job_id)
                if current and current["state"] not in {"deleted", "failed", "ready"}:
                    message = (
                        str(exc)
                        if isinstance(exc, ProcessingError)
                        else "Processing stopped on the server. Check server setup and try this class again."
                    )
                    db.transition(job_id, "failed", error=message)
                for name in ("result.tmp", "result.json", "transcript.json", "diagnostics.json"):
                    (folder / name).unlink(missing_ok=True)
            finally:
                discard_inputs(folder)
                current = db.get_job(job_id)
                if current and current["state"] == "deleted":
                    shutil.rmtree(folder, ignore_errors=True)


@celery.task(name="app.tasks.cleanup")
def cleanup(now: float | None = None):
    db.init_db()
    now = time.time() if now is None else now
    removed = 0
    for job in db.cleanup_candidates(now):
        try:
            with job_lock(job["id"]).acquire(timeout=0):
                shutil.rmtree(job_path(job["id"]), ignore_errors=True)
                db.remove_job(job["id"])
                removed += 1
        except Timeout:
            continue
    # Interrupted uploads / crashes before the metadata commit leave no accessible job.
    root = settings().storage_path / "jobs"
    if root.exists():
        for folder in root.iterdir():
            if folder.is_dir() and folder.stat().st_mtime < now - 86400 and not db.get_job(folder.name):
                shutil.rmtree(folder, ignore_errors=True)
    return removed


@celery.task(name="app.tasks.recover_dispatch")
def recover_dispatch():
    """Recover a process crash between committing job metadata and publishing to Redis."""
    db.init_db()
    for job in db.undispatched_jobs(time.time()):
        process_job.apply_async(args=[job["id"]], task_id=job["id"], retry=False)
        db.mark_dispatched(job["id"])
