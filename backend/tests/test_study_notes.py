import json
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import generation
from app.media import ProcessingError
from app.reliability import LexicalFaithfulness, save_reliability
from app.schemas import Comparison, Notes, Result, Section, Segment
from app.transcription import Diagnostics


def test_offline_token_audit_never_calls_model(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline audit must not run inference")
    monkeypatch.setattr(generation.httpx, "post", forbidden)
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_generation_tokens.py"
    audit = runpy.run_path(str(script))
    audit["main"]()
    report = json.loads(capsys.readouterr().out)
    assert "characters / 4" in report["method"]
    assert report["bullet_format_same_content"]["flat_bullets_same_words"]["characters"] == 374
    assert report["bullet_format_same_content"]["nested_bullets_same_words"]["characters"] == 396
    for case in report["prompt_fixtures"].values():
        assert case["without_timestamps"]["characters"] < case["baseline"]["characters"]
    assert report["mock_output"]["refs_wire_alias"]["characters"] == 353
    assert report["synthetic_repetition_stress_only"][2]["calls_before_retries"] == 4
    implemented = report["implemented_first_round"]
    assert implemented["synthetic_240_segments"]["calls_unchanged"] == 4
    assert implemented["synthetic_240_segments"]["current_estimated_tokens"] < 10381


def lecture():
    return [
        Segment(id="s1", start=0, end=10, text="Diffusion moves particles down a concentration gradient. It does not require ATP."),
        Segment(id="s2", start=10, end=20, text="Active transport requires ATP. It moves particles against a concentration gradient."),
    ]


def study_notes():
    return Notes(title="Membrane transport", sections=[
        Section(heading="Transport", subheading="Diffusion", body="Particles move down a concentration gradient.",
                bullets=["Does not require ATP."], source_segment_ids=["s1"]),
        Section(heading="Transport", subheading="Comparison", comparison=Comparison(
            columns=["Mechanism", "Energy"], rows=[["Diffusion", "No ATP"], ["Active transport", "ATP required"]]),
            source_segment_ids=["s1", "s2"]),
    ])


def test_structure_and_legacy_result():
    notes = study_notes()
    assert Notes.model_validate_json(notes.model_dump_json()) == notes
    with pytest.raises(ValidationError):
        Comparison(columns=["A", "B"], rows=[["missing"]])
    with pytest.raises(ValidationError):
        Section(heading="Empty", source_segment_ids=["s1"])
    with pytest.raises(ValidationError):
        Section(heading="Empty", bullets=[" "], source_segment_ids=["s1"])
    legacy = {"job_id": "job", "mock": False, "duration_seconds": 20,
              "transcript": [s.model_dump() for s in lecture()],
              "notes": {"title": "Old", "sections": [{"heading": "Old topic", "body": "Old notes",
                        "source_segment_ids": ["s1"]}], "review_items": [{"reason": "PRIVATE"}]},
              "warnings": ["PRIVATE"]}
    restored = Result.model_validate(legacy)
    assert restored.notes.sections[0].body == "Old notes"
    assert "PRIVATE" not in restored.model_dump_json()


def test_mocked_generation_grounding_and_no_verification_calls(monkeypatch, tmp_path):
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": study_notes().model_dump_json(exclude={"title"})}}
        return Response()
    monkeypatch.setattr(generation.httpx, "post", post)
    notes = generation.generate_notes(lecture(), "Transport", "Transport includes quantum tunneling.", "Class", tmp_path)
    assert notes.sections == study_notes().sections
    assert notes.title == "Class"
    assert "quantum" not in notes.model_dump_json()
    payload = json.loads(calls[0]["messages"][1]["content"])
    assert "quantum" in payload["syllabus_context_only"]
    assert "quantum" not in json.dumps(payload["transcript"])
    assert "Only the transcript is evidence" in calls[0]["messages"][0]["content"]
    save_reliability(tmp_path, lecture(), [], notes)
    assert len(calls) == 1  # Neither consolidation nor reliability invokes the LLM.


def test_internal_screening_flags_not_corrections(tmp_path):
    notes = Notes(title="Transport", sections=[Section(
        heading="Diffusion", body="Diffusion does require ATP.",
        bullets=["There are 99 stages.", "Quantum tunneling explains electron teleportation."],
        source_segment_ids=["s1"],
    )])
    original = notes.model_dump_json()
    transcript = lecture()
    raw = [s.model_dump() for s in transcript]
    save_reliability(tmp_path, transcript, [Diagnostics("s1", -2, 0.1, 1), Diagnostics("s2", 0, 0.9, 1)], notes)
    report = json.loads((tmp_path / "reliability.json").read_text())
    reasons = {r for f in report["faithfulness"]["findings"] for r in f["reasons"]}
    assert {"number_not_in_evidence", "low_lexical_support", "possible_negation_mismatch"} <= reasons
    assert len(report["transcription"]["findings"]) == 2
    assert report["faithfulness"]["status"] == "screened_not_verified"
    assert notes.model_dump_json() == original
    assert [s.model_dump() for s in transcript] == raw
    assert "faithfulness" not in Result(job_id="x", mock=False, duration_seconds=20, notes=notes,
                                        transcript=transcript).model_dump_json()


def test_supported_content_and_replaceable_checker(tmp_path):
    notes = Notes(title="Diffusion", sections=[Section(heading="Diffusion",
        body=lecture()[0].text, source_segment_ids=["s1"])])
    assert LexicalFaithfulness().analyze(notes, lecture()) == []
    class Replacement:
        def analyze(self, notes, transcript): return [{"test": "replacement"}]
    save_reliability(tmp_path, lecture(), [], notes, Replacement())
    assert json.loads((tmp_path / "reliability.json").read_text())["faithfulness"]["method"] == "Replacement"


def test_consolidation_preserves_all_blocks_and_groups_topics():
    notes = study_notes()
    other = Notes(title="Other", sections=[Section(heading="Other", bullets=["Important distinct fact"],
                                                   source_segment_ids=["s2"])])
    drafts = [Notes(title=notes.title, sections=[notes.sections[0]]), other,
              Notes(title=notes.title, sections=[notes.sections[1], notes.sections[0]])]
    merged = generation.consolidate(drafts, lecture())
    assert [s.heading for s in merged.sections] == ["Transport", "Other", "Transport"]
    assert [merged.sections[0], merged.sections[2]] == notes.sections
    assert merged.sections[1].bullets == ["Important distinct fact"]


def test_fingerprint_tracks_prompt_schema_and_exact_selected_context(monkeypatch):
    def fingerprint(syllabus="Transport context"):
        return generation.draft_fingerprint(lecture(), "topic", syllabus, "Class", "gemma", 3000)
    initial = fingerprint()
    monkeypatch.setattr(generation, "SYSTEM", generation.SYSTEM + " New instruction.")
    assert fingerprint() != initial
    current = fingerprint()
    monkeypatch.setattr(generation.Notes, "model_json_schema", lambda: {"changed": True})
    assert fingerprint() != current
    prefix = "unrelated paragraph\n" * 300
    assert fingerprint(prefix + "Diffusion uses ATP") != fingerprint(prefix + "Diffusion does not use ATP")


def test_partial_checkpoint_resume_skips_completed_chunks(monkeypatch, tmp_path):
    chunks = [[s] for s in lecture()]
    monkeypatch.setattr(generation, "chunk_transcript", lambda *args: chunks)
    calls = []
    fail = True
    def ask(payload, segments, **kwargs):
        calls.append(segments[0].id)
        if fail and segments[0].id == "s2":
            raise ProcessingError("Temporary failure")
        return Notes(title="Transport", sections=[Section(heading="Transport", body=segments[0].text,
                                                           source_segment_ids=[segments[0].id])])
    monkeypatch.setattr(generation, "ask_notes", ask)
    with pytest.raises(ProcessingError):
        generation.generate_notes(lecture(), "", "", "Class", tmp_path)
    fail = False
    notes = generation.generate_notes(lecture(), "", "", "Class", tmp_path)
    assert calls == ["s1", "s2", "s2"]
    assert len(notes.sections) == 2
    # A syntactically valid checkpoint with invalid refs must be regenerated.
    path = next(p for p in tmp_path.glob('*.json') if '"s1"' in p.read_text())
    path.write_text(path.read_text().replace('"s1"', '"invented"'))
    generation.generate_notes(lecture(), "", "", "Class", tmp_path)
    assert calls[-1] == "s1"
