import json

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter

from app.generation import ask_notes, chunk_transcript, consolidate
from app.media import ProcessingError, extract_syllabus
from app.schemas import Notes, Result, Section, Segment, validate_sources
from app.transcription import Diagnostics, review_diagnostics


def sample(sid="s1"):
    return Notes(
        title="Class",
        sections=[Section(heading="An idea", body="A claim from class.", source_segment_ids=[sid])],
    )


def test_schema_and_source_integrity():
    segments = [Segment(id="s1", start=12, end=18, text="A claim from class.")]
    validate_sources(sample(), segments)
    with pytest.raises(ValueError, match="nonexistent"):
        validate_sources(sample("invented"), segments)
    with pytest.raises(ValueError, match="lost"):
        validate_sources(sample(), segments, {"s1", "s2"})
    with pytest.raises(ValidationError):
        Notes.model_validate({"title": "Empty", "sections": []})
    with pytest.raises(ValidationError):
        Segment(id="x", start=5, end=3, text="Incorrect")
    with pytest.raises(ValidationError):
        Result(job_id="job", mock=False, duration_seconds=18, notes=sample(), transcript=segments * 2)


def test_chunks_are_bounded_with_limited_overlap():
    segments = [Segment(id=f"s{i}", start=i, end=i + 1, text="word " * 40) for i in range(40)]
    chunks = chunk_transcript(segments, 1000)
    assert len(chunks) > 1
    assert {s.id for chunk in chunks for s in chunk} == {s.id for s in segments}
    assert all(sum(len(s.model_dump_json()) for s in chunk) <= 1000 for chunk in chunks)
    assert all(len({s.id for s in a} & {s.id for s in b}) <= 1 for a, b in zip(chunks, chunks[1:]))


def test_malformed_model_output_retries_only_once(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "not json"}}

    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("app.generation.httpx.post", post)
    with pytest.raises(ProcessingError, match="could not be checked"):
        ask_notes({"transcript": "untrusted"}, [])
    assert len(calls) == 2
    assert calls[0]["json"]["format"]["type"] == "object"
    assert "untrusted DATA" in calls[0]["json"]["messages"][0]["content"]


def test_source_retry_can_recover(monkeypatch):
    responses = iter([sample("fake"), sample()])

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": next(responses).model_dump_json()}}

    monkeypatch.setattr("app.generation.httpx.post", lambda *args, **kwargs: Response())
    notes = ask_notes({}, [Segment(id="s1", start=0, end=1, text="a claim")])
    assert notes.sections[0].source_segment_ids == ["s1"]


def test_consolidation_preserves_all_source_references(monkeypatch):
    captured = []

    def combine(payload, segments, required=None):
        captured.append((len(json.dumps(payload)), required))
        return Notes(
            title="Combined",
            sections=[
                Section(heading="Together", body="Combined notes", source_segment_ids=sorted(required))
            ],
        )

    monkeypatch.setattr("app.generation.ask_notes", combine)
    segments = [Segment(id=f"s{i}", start=i, end=i + 1, text="A claim") for i in range(9)]
    notes = consolidate([sample(s.id) for s in segments], segments)
    assert set(notes.sections[0].source_segment_ids) == {s.id for s in segments}
    assert len(captured) == 8
    assert all(size < 30000 for size, _ in captured)


def test_image_only_pdf_is_explained(tmp_path):
    pdf = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    with pytest.raises(ProcessingError, match="no extractable text"):
        extract_syllabus(pdf)


def test_diagnostics_are_labelled_heuristics():
    flags = review_diagnostics([Diagnostics("s1", -1.5, 0.1, 1.2), Diagnostics("s2", -0.2, 0.1, 1.2)])
    assert len(flags) == 1
    assert flags[0].kind == "heuristic"
    assert flags[0].source_segment_ids == ["s1"]
