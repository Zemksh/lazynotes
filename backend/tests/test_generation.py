import json

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter

from app.generation import ask_notes, chunk_transcript, consolidate, generate_notes
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
            return {"message": {"content": next(responses).model_dump_json(exclude={"title"})}}

    monkeypatch.setattr("app.generation.httpx.post", lambda *args, **kwargs: Response())
    notes = ask_notes({}, [Segment(id="s1", start=0, end=1, text="a claim")])
    assert notes.sections[0].source_segment_ids == ["s1"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_ask_notes_reports_schema_failure(monkeypatch, caplog):
    monkeypatch.setattr(
        "app.generation.httpx.post",
        lambda *args, **kwargs: FakeResponse({"message": {"content": json.dumps({"title": "", "sections": []})}, "done_reason": "stop"}),
    )
    with pytest.raises(ProcessingError, match="could not be checked"):
        ask_notes({"transcript": "untrusted"}, [])
    assert "category=schema" in caplog.text


def test_schema_retry_has_safe_field_details(monkeypatch, caplog):
    calls = []
    invalid = {"sections": [{"heading": "Private lecture", "bullets": [
        {"text": "Private parent", "children": []}], "source_segment_ids": ["s1"]}],
        "private arbitrary key": "Private lecture content"}
    responses = iter([invalid, sample().model_dump(exclude={"title"})])

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse({"message": {"content": json.dumps(next(responses))}})

    monkeypatch.setattr("app.generation.httpx.post", post)
    result = ask_notes({}, [Segment(id="s1", start=0, end=1, text="a claim")])
    assert result.sections
    assert len(calls) == 2
    retry = calls[1]["messages"][-1]["content"]
    assert "children:too_short" in retry
    assert "unknown_field:extra_forbidden" in retry
    assert "attempt=0" in caplog.text
    for private in ("Private", "private arbitrary key"):
        assert private not in retry
        assert private not in caplog.text


def test_validation_diagnostics_are_bounded():
    from app.generation import validation_details
    from app.schemas import GeneratedNotes

    with pytest.raises(ValidationError) as caught:
        GeneratedNotes.model_validate({"sections": [{}] * 100})
    assert len(validation_details(caught.value).split("; ")) == 8


def test_ask_notes_reports_source_reference_failure(monkeypatch, caplog):
    content = json.dumps(
        {"sections": [{"heading": "An idea", "body": "A claim from class.", "source_segment_ids": ["s99999"]}]}
    )
    monkeypatch.setattr(
        "app.generation.httpx.post",
        lambda *args, **kwargs: FakeResponse({"message": {"content": content}, "done_reason": "stop"}),
    )
    with pytest.raises(ProcessingError, match="could not be checked"):
        ask_notes({"transcript": "untrusted"}, [Segment(id="s1", start=0, end=1, text="a claim")])
    assert "category=source_refs" in caplog.text


def test_ask_notes_reports_truncation(monkeypatch, caplog):
    monkeypatch.setattr(
        "app.generation.httpx.post",
        lambda *args, **kwargs: FakeResponse({"message": {"content": "{}"}, "done_reason": "length"}),
    )
    with pytest.raises(ProcessingError, match="could not be checked"):
        ask_notes({"transcript": "untrusted"}, [])
    assert "category=truncated" in caplog.text


def test_ask_notes_reports_duplicate_source_references(monkeypatch, caplog):
    content = json.dumps(
        {"sections": [{"heading": "An idea", "body": "A claim from class.", "source_segment_ids": ["s1", "s1"]}]}
    )
    monkeypatch.setattr(
        "app.generation.httpx.post",
        lambda *args, **kwargs: FakeResponse({"message": {"content": content}, "done_reason": "stop"}),
    )
    with pytest.raises(ProcessingError, match="could not be checked"):
        ask_notes({"transcript": "untrusted"}, [Segment(id="s1", start=0, end=1, text="a claim")])
    assert "category=duplicate_refs" in caplog.text


def test_ask_notes_rejects_reliability_inside_notes(monkeypatch, caplog):
    content = json.dumps(
        {
            "sections": [{"heading": "An idea", "body": "A claim from class.", "source_segment_ids": ["s1"]}],
            "review_items": [{"reason": "Unclear", "source_segment_ids": ["s1"], "kind": "heuristic"}],
        }
    )
    monkeypatch.setattr(
        "app.generation.httpx.post",
        lambda *args, **kwargs: FakeResponse({"message": {"content": content}, "done_reason": "stop"}),
    )
    with pytest.raises(ProcessingError, match="could not be checked"):
        ask_notes({"transcript": "untrusted"}, [Segment(id="s1", start=0, end=1, text="a claim")])
    assert "category=schema" in caplog.text


def test_consolidation_makes_no_model_calls_and_merges_source_ids(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Consolidation must not call the notes model")

    monkeypatch.setattr("app.generation.ask_notes", forbidden)
    segments = [Segment(id=f"s{i}", start=i, end=i + 1, text="A claim") for i in range(9)]
    notes = consolidate([sample(s.id) for s in segments], segments)
    # Identical content across overlapping chunks is deduplicated; source IDs are merged.
    assert len(notes.sections) == 1
    assert set(notes.sections[0].source_segment_ids) == {s.id for s in segments}


def test_consolidation_preserves_distinct_content():
    segments = [
        Segment(id="s1", start=0, end=1, text="first"),
        Segment(id="s2", start=1, end=2, text="second"),
    ]
    drafts = [
        Notes(
            title="Class",
            sections=[Section(heading="One", body="First idea.", source_segment_ids=["s1"])],
        ),
        Notes(
            title="Class",
            sections=[Section(heading="Two", body="Second idea.", source_segment_ids=["s1", "s2"])],
        ),
    ]
    notes = consolidate(drafts, segments)
    assert {s.heading for s in notes.sections} == {"One", "Two"}
    assert {s.body for s in notes.sections} == {"First idea.", "Second idea."}


def test_generate_notes_reuses_cached_draft(tmp_path, monkeypatch):
    calls = []

    def fake_ask(payload, segments, required=None, **kwargs):
        calls.append(payload)
        return Notes(
            title="Class",
            sections=[Section(heading="An idea", body="A claim from class.", source_segment_ids=[segments[0].id])],
        )

    monkeypatch.setattr("app.generation.ask_notes", fake_ask)
    segments = [Segment(id="s1", start=0, end=1, text="A claim from class.")]
    first = generate_notes(segments, "topic", "", "Class", cache_dir=tmp_path)
    second = generate_notes(segments, "topic", "", "Class", cache_dir=tmp_path)
    assert len(calls) == 1
    assert first.sections == second.sections


def test_generate_notes_invalidates_draft_cache_when_context_changes(tmp_path, monkeypatch):
    calls = []

    def fake_ask(payload, segments, required=None, **kwargs):
        calls.append(payload)
        return Notes(
            title="Class",
            sections=[Section(heading="An idea", body="A claim from class.", source_segment_ids=[segments[0].id])],
        )

    monkeypatch.setattr("app.generation.ask_notes", fake_ask)
    segments = [Segment(id="s1", start=0, end=1, text="A claim from class.")]
    generate_notes(segments, "claim", "", "Class", cache_dir=tmp_path)
    generate_notes(segments, "claim terminology", "", "Class", cache_dir=tmp_path)
    assert len(calls) == 2


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
