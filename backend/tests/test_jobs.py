import json
import time
import uuid
from types import SimpleNamespace

import pytest

from app import db
from app.media import ProcessingError
from app.schemas import Notes, Section
from app.tasks import cleanup, job_lock, job_path, process_job, recover_dispatch, resume_notes_generation

TOKEN = "a" * 64
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def upload(client, audio, request_id=None, **extra):
    return client.post(
        "/jobs",
        headers=AUTH,
        data={"request_id": request_id or str(uuid.uuid4()), "topics": "A standalone class", **extra},
        files={"audio": ("../../recording.wav", audio, "audio/wav")},
    )


def test_transitions_are_persistent_and_enforced():
    jid = str(uuid.uuid4())
    db.create_job(jid, "key", TOKEN, "Class")
    with pytest.raises(ValueError):
        db.transition(jid, "ready")
    for state in ("transcribing", "preparing", "ready"):
        db.transition(jid, state)
        assert db.get_job(jid)["state"] == state
    with pytest.raises(ValueError):
        db.transition(jid, "transcribing")
    assert db.get_job(jid)["expires_at"] > time.time() + 71 * 3600


def test_authentication_idempotency_and_result_gate(client, audio):
    request_id = str(uuid.uuid4())
    response = upload(client, audio, request_id)
    assert response.status_code == 202
    job = response.json()
    assert "token_hash" not in job
    assert upload(client, audio, request_id).json()["id"] == job["id"]
    path = f"/jobs/{job['id']}"
    assert client.get(path).status_code == 401
    for endpoint in (path, path + "/result"):
        assert client.get(endpoint, headers={"Authorization": f"Bearer {'b' * 64}"}).status_code == 404
    assert client.get(path + "/result", headers=AUTH).status_code == 409
    assert client.delete(path, headers={"Authorization": f"Bearer {'b' * 64}"}).status_code == 404
    assert (job_path(job["id"]) / "audio.upload").exists()


def test_mock_worker_real_ffmpeg_and_download(client, audio):
    job_id = upload(client, audio).json()["id"]
    process_job.run(job_id)
    assert db.get_job(job_id)["state"] == "ready"
    response = client.get(f"/jobs/{job_id}/result", headers=AUTH)
    assert response.status_code == 200
    result = response.json()
    assert result["mock"] is True
    assert result["duration_seconds"] == 1
    assert result["notes"]["sections"][0]["source_segment_ids"] == ["s00001"]
    assert not (job_path(job_id) / "audio.upload").exists()
    process_job.run(job_id)  # Duplicate delivery cannot overwrite completed results.
    assert client.delete(f"/jobs/{job_id}", headers=AUTH).status_code == 204
    assert client.get(f"/jobs/{job_id}/result", headers=AUTH).status_code == 410
    assert not job_path(job_id).exists()


def test_failure_cleanup_and_safe_error(client):
    jid = upload(client, b"not audio").json()["id"]
    process_job.run(jid)
    job = db.get_job(jid)
    assert job["state"] == "failed"
    assert "could not be read" in job["error"]
    assert list(job_path(jid).iterdir()) == []


def test_failure_logs_locations_without_private_data(client, audio, monkeypatch, caplog):
    def broken_decoder(*args):
        try:
            raise ValueError("PRIVATE_LECTURE_TEXT")
        except ValueError as exc:
            raise TypeError("PRIVATE_TOKEN") from exc

    monkeypatch.setattr("app.tasks.decode_audio", broken_decoder)
    jid = upload(client, audio).json()["id"]
    process_job.run(jid)
    assert f"Job {jid} failed stage=decode_audio" in caplog.text
    assert "elapsed_seconds=" in caplog.text
    assert "TypeError" in caplog.text and "ValueError" in caplog.text
    assert "in broken_decoder" in caplog.text
    assert "test_jobs.py:" in caplog.text
    assert "PRIVATE_" not in caplog.text
    assert "PRIVATE_" not in db.get_job(jid)["error"]
    assert db.get_job(jid)["state"] == "failed"
    assert list(job_path(jid).iterdir()) == []


def test_retention_keeps_retrieval_window_and_skips_active_lock(client, audio):
    jid = upload(client, audio).json()["id"]
    process_job.run(jid)
    expiry = db.get_job(jid)["expires_at"]
    assert cleanup.run(now=expiry - 1) == 0
    with job_lock(jid):
        assert cleanup.run(now=expiry + 1) == 0
    assert cleanup.run(now=expiry + 1) == 1
    assert db.get_job(jid) is None
    assert not job_path(jid).exists()


def test_queue_failure_is_persisted_and_cleans_inputs(client, audio, monkeypatch):
    def unavailable(**kwargs):
        raise ConnectionError("secret connection details")

    monkeypatch.setattr("app.main.process_job.apply_async", unavailable)
    response = upload(client, audio)
    assert response.status_code == 202
    job = response.json()
    assert job["state"] == "failed"
    assert "secret" not in job["error"]
    assert list(job_path(job["id"]).iterdir()) == []


def test_upload_validation_and_cleanup(client, audio, monkeypatch):
    assert upload(client, audio, topics=" ").status_code == 422
    assert upload(client, b"").status_code == 422
    monkeypatch.setenv("MAX_AUDIO_MB", "1")
    from app.config import settings

    settings.cache_clear()
    assert upload(client, b"x" * (1024 * 1024 + 1)).status_code == 413
    assert list((settings().storage_path / "jobs").iterdir()) == []


def test_expired_access_and_replayed_upload_are_denied(client, audio):
    request_id = str(uuid.uuid4())
    jid = upload(client, audio, request_id).json()["id"]
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET expires_at=0 WHERE id=?", (jid,))
    assert client.get(f"/jobs/{jid}", headers=AUTH).status_code == 410
    assert upload(client, audio, request_id).status_code == 410


def test_worker_redelivery_after_crash_fails_plainly(client, audio):
    jid = upload(client, audio).json()["id"]
    db.transition(jid, "transcribing")
    process_job.run(jid)
    assert "interrupted" in db.get_job(jid)["error"]
    assert not (job_path(jid) / "audio.upload").exists()


def test_dispatch_recovery_after_api_crash(monkeypatch):
    jid = str(uuid.uuid4())
    db.create_job(jid, "recovery-key", TOKEN, "Recover me")
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET created_at=? WHERE id=?", (time.time() - 120, jid))
    dispatched = []
    monkeypatch.setattr("app.tasks.process_job.apply_async", lambda **kwargs: dispatched.append(kwargs))
    recover_dispatch.run()
    recover_dispatch.run()
    assert len(dispatched) == 1
    assert dispatched[0]["args"] == [jid]


def test_total_request_size_limit(client, monkeypatch):
    monkeypatch.setenv("MAX_AUDIO_MB", "1")
    monkeypatch.setenv("MAX_PDF_MB", "1")
    from app.config import settings

    settings.cache_clear()
    response = upload(client, b"x" * (3 * 1024 * 1024 + 1000))
    assert response.status_code == 413


def test_real_processing_never_falls_back_to_mock(client, audio, monkeypatch):
    from app.config import settings

    monkeypatch.setenv("MOCK_MODE", "false")
    settings.cache_clear()

    def failed_transcription(path):
        raise RuntimeError("private transcript must not appear in errors")

    monkeypatch.setattr("app.tasks.transcribe", failed_transcription)
    jid = upload(client, audio).json()["id"]
    process_job.run(jid)
    job = db.get_job(jid)
    assert job["state"] == "failed"
    assert not job["mock"]
    assert "private" not in job["error"]
    assert client.get(f"/jobs/{jid}/result", headers=AUTH).status_code == 409


def test_delete_during_processing_does_not_resurrect_job(client, audio, monkeypatch):
    from app.config import settings
    from app.schemas import Segment

    monkeypatch.setenv("MOCK_MODE", "false")
    settings.cache_clear()
    jid = upload(client, audio).json()["id"]

    def transcribe_then_delete(path):
        assert client.delete(f"/jobs/{jid}", headers=AUTH).status_code == 204
        return [Segment(id="s1", start=0, end=1, text="Raw words")], []

    monkeypatch.setattr("app.tasks.transcribe", transcribe_then_delete)
    process_job.run(jid)
    assert db.get_job(jid)["state"] == "deleted"
    assert not job_path(jid).exists()


def test_notes_failure_retains_transcript_and_inputs(client, audio, monkeypatch):
    from app.config import settings

    monkeypatch.setenv("MOCK_MODE", "false")
    settings.cache_clear()
    from app.schemas import Segment

    monkeypatch.setattr(
        "app.tasks.transcribe", lambda path: ([Segment(id="s00001", start=0, end=1, text="A claim")], [])
    )

    def failing_generate(*args, **kwargs):
        raise ProcessingError("The notes could not be checked against the transcript. Please try this class again.")

    monkeypatch.setattr("app.tasks.generate_notes", failing_generate)
    jid = upload(client, audio).json()["id"]
    process_job.run(jid)
    assert db.get_job(jid)["state"] == "failed"
    # Note-generation failures retain the transcript, recording and checkpoints for manual resume.
    assert (job_path(jid) / "transcript.json").exists()
    assert (job_path(jid) / "audio.upload").exists()
    assert not (job_path(jid) / "result.json").exists()


def test_resume_notes_requires_transcript(client, audio):
    jid = str(uuid.uuid4())
    db.create_job(jid, "resume-key", TOKEN, "Resume me")
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (jid,))
    with pytest.raises(ProcessingError):
        resume_notes_generation.run(jid)


def test_resume_notes_rebuilds_result_from_retained_artifacts(client, audio, monkeypatch):
    jid = str(uuid.uuid4())
    db.create_job(jid, "resume-key", TOKEN, "Resume me")
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (jid,))
    folder = job_path(jid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "transcript.json").write_text(json.dumps([{"id": "s00001", "start": 0, "end": 1, "text": "A claim"}]))
    (folder / "input.json").write_text(json.dumps({"topics": "A standalone class", "syllabus_text": ""}))

    def fake_generate(segments, topics, syllabus, title, cache_dir=None):
        return Notes(
            title="Resumed",
            sections=[Section(heading="An idea", body="A claim from class.", source_segment_ids=[segments[0].id])],
        )

    monkeypatch.setattr("app.tasks.generate_notes", fake_generate)
    resume_notes_generation.run(jid)
    assert db.get_job(jid)["state"] == "ready"
    assert (folder / "result.json").exists()


def test_resume_endpoint_only_failed_jobs(client, audio, monkeypatch):
    jid = upload(client, audio).json()["id"]
    assert client.post(f"/jobs/{jid}/resume", headers=AUTH).status_code == 409
    dispatched = []

    def fake_apply_async(**kwargs):
        dispatched.append(kwargs)

    monkeypatch.setattr("app.main.resume_notes_generation", SimpleNamespace(apply_async=fake_apply_async))
    jid_failed = str(uuid.uuid4())
    db.create_job(jid_failed, "resume-key-2", TOKEN, "Resume me")
    with db.connection() as connection:
        connection.execute("UPDATE jobs SET state='failed' WHERE id=?", (jid_failed,))
    response = client.post(f"/jobs/{jid_failed}/resume", headers=AUTH)
    assert response.status_code == 202
    assert dispatched == [{"args": [jid_failed], "task_id": jid_failed, "retry": False, "connection_timeout": 3}]


def test_internal_checks_and_pdf_context_survive_resume(client, audio, monkeypatch):
    from app.config import settings
    from app.schemas import Segment
    from app.transcription import Diagnostics

    monkeypatch.setenv("MOCK_MODE", "false")
    settings.cache_clear()
    jid = upload(client, audio).json()["id"]
    folder = job_path(jid)
    (folder / "syllabus.pdf").write_bytes(b"fixture")
    monkeypatch.setattr("app.tasks.extract_syllabus", lambda path: "PDF context only")
    monkeypatch.setattr("app.tasks.transcribe", lambda path: (
        [Segment(id="s1", start=0, end=1, text="Diffusion does not require ATP.")],
        [Diagnostics("s1", -2, 0, 1)],
    ))
    calls = []
    def generate(segments, topics, syllabus, title, cache_dir=None):
        calls.append(syllabus)
        if len(calls) == 1:
            raise ProcessingError("Temporary fixture failure")
        return Notes(title="Diffusion", sections=[Section(heading="Diffusion",
            bullets=["There are 99 stages."], source_segment_ids=["s1"])])
    monkeypatch.setattr("app.tasks.generate_notes", generate)
    process_job.run(jid)
    assert db.get_job(jid)["state"] == "failed"
    report = json.loads((folder / "reliability.json").read_text())
    assert report["transcription"]["findings"]
    assert report["faithfulness"]["status"] == "pending"
    resume_notes_generation.run(jid)
    assert calls == ["PDF context only", "PDF context only"]
    assert db.get_job(jid)["state"] == "ready"
    result = client.get(f"/jobs/{jid}/result", headers=AUTH).json()
    assert result["duration_seconds"] == 1
    assert "review_items" not in result["notes"]
    assert "faithfulness" not in result and not result["warnings"]
    assert result["notes"]["sections"][0]["source_segment_ids"] == ["s1"]
    assert (folder / "transcript.json").exists()
    assert json.loads((folder / "reliability.json").read_text())["faithfulness"]["findings"]
    assert not (folder / "audio.upload").exists()
