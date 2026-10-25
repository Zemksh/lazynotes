import json
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from filelock import Timeout
from starlette.concurrency import run_in_threadpool

from . import db
from .config import settings
from .schemas import Result
from .tasks import discard_inputs, job_lock, job_path, process_job, resume_notes_generation


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Lazy Notes", version="0.1.0", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


class BodyLimit:
    """Bound multipart input even when Content-Length is absent or dishonest."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = (settings().max_audio_mb + settings().max_pdf_mb + 1) * 1024 * 1024
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                raise HTTPException(413, "Upload is too large.")
            return message

        await self.app(scope, limited_receive, send)


app.add_middleware(BodyLimit)


def access_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if not credentials or not re.fullmatch(r"[0-9a-f]{64}", credentials.credentials):
        raise HTTPException(401, "A valid class access token is required.")
    return credentials.credentials


def require_job(job_id: uuid.UUID, token: str):
    job = db.get_job(str(job_id))
    if not db.authorized(job, token):
        raise HTTPException(404, "This class was not found or its access token is invalid.")
    if job["state"] == "deleted" or job["expires_at"] <= time.time():
        raise HTTPException(410, "This class has expired or was removed from the server.")
    return job


def public_job(job):
    return {
        key: job[key]
        for key in ("id", "title", "state", "created_at", "updated_at", "expires_at", "error", "mock")
    }


async def save_upload(upload: UploadFile, path, limit: int):
    size = 0
    with path.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "The selected file exceeds the server size limit.")
            await run_in_threadpool(output.write, chunk)
    if size == 0:
        raise HTTPException(422, "The selected file is empty.")


@app.get("/health")
def health():
    cfg = settings()
    return {
        "status": "ok",
        "mock": cfg.mock_mode,
        "max_audio_mb": cfg.max_audio_mb,
        "max_pdf_mb": cfg.max_pdf_mb,
        "max_duration_seconds": cfg.max_duration_seconds,
    }


@app.post("/jobs", status_code=202)
async def create_job(
    request_id: uuid.UUID = Form(...),
    topics: str = Form(..., max_length=4000),
    title: str = Form("", max_length=160),
    syllabus_text: str = Form("", max_length=100000),
    audio: UploadFile = File(...),
    syllabus: UploadFile | None = File(None),
    token: str = Depends(access_token),
):
    if not topics.strip():
        raise HTTPException(422, "Tell us what was covered in this class.")
    if syllabus and syllabus_text.strip():
        raise HTTPException(422, "Use either syllabus text or a PDF, not both.")
    key = db.digest(f"{request_id}:{token}")
    existing = db.find_request(key)
    if existing:
        if existing["state"] == "deleted" or existing["expires_at"] <= time.time():
            raise HTTPException(410, "This upload has expired. Start a new class.")
        return public_job(existing)
    cfg = settings()
    job_id = str(uuid.uuid4())
    folder = job_path(job_id)
    folder.mkdir(parents=True)
    committed = False
    try:
        await save_upload(audio, folder / "audio.upload", cfg.max_audio_mb * 1024 * 1024)
        if syllabus:
            await save_upload(syllabus, folder / "syllabus.pdf", cfg.max_pdf_mb * 1024 * 1024)
            with (folder / "syllabus.pdf").open("rb") as handle:
                if b"%PDF-" not in handle.read(1024):
                    raise HTTPException(422, "Choose a valid PDF syllabus, or paste its text.")
        title = title.strip() or topics.strip().splitlines()[0][:80]
        (folder / "input.json").write_text(
            json.dumps({"topics": topics.strip(), "syllabus_text": syllabus_text})
        )
        try:
            job = db.create_job(job_id, key, token, title)
        except sqlite3.IntegrityError:
            return public_job(db.find_request(key))
        committed = True
        try:
            await run_in_threadpool(
                process_job.apply_async, args=[job_id], task_id=job_id, retry=False, connection_timeout=3
            )
            db.mark_dispatched(job_id)
        except Exception:
            db.transition(
                job_id,
                "failed",
                error="The processing queue is unavailable. Try again after the server is restarted.",
            )
            discard_inputs(folder)
            job = db.get_job(job_id)
        return public_job(job)
    finally:
        await audio.close()
        if syllabus:
            await syllabus.close()
        if not committed:
            shutil.rmtree(folder, ignore_errors=True)


@app.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, token: str = Depends(access_token)):
    return public_job(require_job(job_id, token))


@app.get("/jobs/{job_id}/result", response_model=Result)
def get_result(job_id: uuid.UUID, token: str = Depends(access_token)):
    job = require_job(job_id, token)
    if job["state"] != "ready":
        raise HTTPException(409, "These notes are not ready yet.")
    try:
        return Result.model_validate_json((job_path(str(job_id)) / "result.json").read_text())
    except FileNotFoundError:
        raise HTTPException(410, "These notes are no longer available on the server.") from None


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: uuid.UUID, token: str = Depends(access_token)):
    job = require_job(job_id, token)
    db.transition(job["id"], "deleted")
    try:
        with job_lock(job["id"]).acquire(timeout=0):
            shutil.rmtree(job_path(job["id"]), ignore_errors=True)
    except Timeout:
        pass  # Hidden immediately; active worker removes files in its finally block.


@app.post("/jobs/{job_id}/resume", status_code=202)
def resume_job(job_id: uuid.UUID, token: str = Depends(access_token)):
    job = require_job(job_id, token)
    if job["state"] != "failed":
        raise HTTPException(409, "Only failed classes can be resumed.")
    resume_notes_generation.apply_async(args=[str(job_id)], task_id=str(job_id), retry=False, connection_timeout=3)
    return public_job(db.get_job(str(job_id)))
