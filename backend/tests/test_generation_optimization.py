import json

import pytest

from app import generation
from app.config import settings
from app.media import ProcessingError
from app.schemas import GeneratedNotes, Segment


def segments():
    return [Segment(id="s1", start=0, end=5, text="ATP stands for adenosine triphosphate."),
            Segment(id="s2", start=5, end=10, text="It provides energy for active transport.")]


def response(ids):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"message": {"content": json.dumps({"sections": [{"heading": "Energy",
                "body": "ATP provides energy for active transport.", "source_segment_ids": ids}]})}}
    return Response()


def test_wire_input_title_and_overlap_checkpoint(monkeypatch, tmp_path):
    raw = segments()
    original = [s.model_dump() for s in raw]
    monkeypatch.setattr(generation, "chunk_transcript", lambda *args: [[raw[0]], raw])
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return response(["s1"] if len(calls) == 1 else ["s1", "s2"])
    monkeypatch.setattr(generation.httpx, "post", post)
    notes = generation.generate_notes(raw, "ATP; Quantum gravity", "ATP terminology", "Known title", tmp_path)
    assert notes.title == "Known title"
    assert [s.model_dump() for s in raw] == original
    assert len(calls) == 2
    for call in calls:
        assert "title" not in call["format"]["properties"]
        payload = json.loads(call["messages"][1]["content"])
        assert "suggested_title" not in payload
        for s in payload["transcript"] + payload.get("context_only", []):
            assert set(s) == {"id", "text"}
        assert call["options"]["num_predict"] == settings().ollama_num_predict
        assert call["model"] == settings().ollama_model
    second = json.loads(calls[1]["messages"][1]["content"])
    assert [s["id"] for s in second["transcript"]] == ["s2"]
    assert [s["id"] for s in second["context_only"]] == ["s1"]
    assert generation.generate_notes(raw, "ATP; Quantum gravity", "ATP terminology", "Known title", tmp_path) == notes
    assert len(calls) == 2
    # Invalid overlap-only cached output must not be trusted on resume.
    paths = sorted(tmp_path.glob('*.json'))
    for path in paths:
        cached = json.loads(path.read_text())
        if cached["sections"][0]["source_segment_ids"] == ["s1", "s2"]:
            cached["sections"][0]["source_segment_ids"] = ["s1"]
            path.write_text(json.dumps(cached))
    generation.generate_notes(raw, "ATP; Quantum gravity", "ATP terminology", "Known title", tmp_path)
    assert len(calls) == 3


def test_overlap_only_section_retries_without_dropping_cross_boundary_content(monkeypatch):
    replies = iter([response(["s1"]), response(["s1", "s2"])])
    monkeypatch.setattr(generation.httpx, "post", lambda *a, **k: next(replies))
    notes = generation.ask_notes({}, segments(), title="Lecture", owned={"s2"})
    assert notes.title == "Lecture"
    assert notes.sections[0].source_segment_ids == ["s1", "s2"]
    assert notes.sections[0].body == "ATP provides energy for active transport."


def test_overlap_only_section_fails_after_bounded_retry(monkeypatch):
    calls = []
    def post(*a, **k):
        calls.append(1)
        return response(["s1"])
    monkeypatch.setattr(generation.httpx, "post", post)
    with pytest.raises(ProcessingError):
        generation.ask_notes({}, segments(), owned={"s2"})
    assert len(calls) == 2


def test_context_preserves_terms_and_expansions_but_not_unrelated_paragraphs():
    raw = segments() + [Segment(id="s3", start=10, end=15, text="AI models and pH measurements use C++.")]
    context = generation.relevant_context(
        "ATP; AI models; pH; Quantum gravity",
        "A.T.P. (adenosine triphosphate) stores chemical energy.\n"
        "AI (artificial intelligence) models.\npH measures acidity.\nC++ programming language.\n"
        "Introduction to quantum gravity.\nThis lecture includes unrelated history.", raw)
    assert "adenosine triphosphate" in context["syllabus_context_only"]
    assert "artificial intelligence" in context["syllabus_context_only"]
    assert "pH measures acidity" in context["syllabus_context_only"]
    assert "C++ programming" in context["syllabus_context_only"]
    assert "gravity" not in json.dumps(context) and "history" not in json.dumps(context)
    assert generation.relevant_context("History", "Quantum gravity", segments()) == {}


def test_fingerprint_tracks_ownership_schema_prompt_and_only_selected_context(monkeypatch):
    def key(syllabus="ATP glossary", owned=None):
        return generation.draft_fingerprint(segments(), "ATP", syllabus, "Known", "gemma", 2500, owned)
    baseline = key()
    assert key(owned={"s2"}) != baseline
    assert key("ATP new glossary") != baseline
    assert key("ATP glossary\nUnrelated medieval history") == baseline
    original_schema = GeneratedNotes.model_json_schema()
    monkeypatch.setattr(GeneratedNotes, "model_json_schema", lambda: {**original_schema, "version": "changed"})
    assert key() != baseline
    baseline = key()
    monkeypatch.setattr(generation, "SYSTEM", generation.SYSTEM + " Updated instruction")
    assert key() != baseline
