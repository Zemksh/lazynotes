import json

from app import generation
from app.organization import near_duplicate_introductions
from app.reliability import save_reliability
from app.schemas import Notes, Section, Segment


def section(body="", sid="s1", heading="Transport", **kwargs):
    return Section(heading=heading, body=body, source_segment_ids=[sid], **kwargs)


def merge(*sections):
    sources = [Segment(id=f"s{i}", start=i, end=i + 1, text="Fixture transcript") for i in range(1, 5)]
    drafts = [Notes(title="Class", sections=[s]) for s in sections]
    return generation.consolidate(drafts, sources)


def test_exact_sentences_bullets_and_sources_preserve_unique_content():
    common = "Diffusion follows a concentration gradient."
    first = section(common, bullets=["Active transport requires cellular energy."])
    second = section(common + " The lecturer demonstrated movement using dye.", sid="s2",
                     bullets=["Active transport requires cellular energy.", "Equilibrium does not stop molecular motion."])
    original = second.model_dump_json()
    notes = merge(first, second)
    assert notes.sections[0].source_segment_ids == ["s1", "s2"]
    assert notes.sections[1].body == "The lecturer demonstrated movement using dye."
    assert notes.sections[1].bullets == ["Equilibrium does not stop molecular motion."]
    assert notes.sections[1].source_segment_ids == ["s2"]
    assert second.model_dump_json() == original
    assert len(merge(first, first, first).sections) == 1


def test_distinct_meanings_context_dependent_steps_and_equations_survive():
    a = section("Transport does require ATP. Temperature is 20 C. E = mc².",
                bullets=["Repeat the previous step.", "It increases the flow."])
    b = section("Transport does not require ATP. Temperature is 30 C. E = mc³.", sid="s2",
                bullets=["Repeat the previous step.", "It increases the flow."])
    notes = merge(a, b)
    assert notes.sections == [a, b]
    # Same wording in explicitly different subtopics is not assumed redundant.
    assert len(merge(section("Pressure changes the measured flow.", subheading="Acids"),
                     section("Pressure changes the measured flow.", sid="s2", subheading="Bases")).sections) == 2


def test_heading_structure_and_lecture_order():
    notes = merge(section("A first explanation of existence.", heading="Introduction to Existentialism"),
                  section("The waiter illustrates a difficult choice.", sid="s2", heading="Examples of Existentialism"),
                  section("A different topic appears here.", sid="s3", heading="Ethics"),
                  section("A later application introduces new details.", sid="s4", heading="Applications of Existentialism"))
    assert [(s.heading, s.subheading) for s in notes.sections] == [
        ("Existentialism", "Introduction"), ("Existentialism", "Examples"),
        ("Ethics", ""), ("Existentialism", "Applications")]


def test_saved_introduction_pattern_detected_not_deleted(tmp_path):
    # Opening sentences from the previously inspected saved drafts; distinct
    # continuations are short fixture text, not a rerun of the recording.
    first = section("Existentialism is a philosophical approach that grapples with the problem of human existence. "
                    "Freedom brings responsibility.", heading="Introduction to Existentialism")
    second = section("Existentialism is a philosophical approach that centers on the problem of human existence. "
                     "The waiter example illustrates remaining in an unfulfilling role. "
                     "Dizziness of freedom describes anxiety.", sid="s2", heading="Introduction to Existentialism")
    notes = merge(first, second)
    assert [s.body for s in notes.sections] == [first.body, second.body]
    findings = near_duplicate_introductions(notes)
    assert len(findings) == 1
    assert findings[0]["source_segment_ids"] == ["s1", "s2"]
    raw = [Segment(id=f"s{i}", start=i, end=i+1, text=s.body) for i,s in enumerate([first,second], 1)]
    save_reliability(tmp_path, raw, [], notes)
    report = json.loads((tmp_path / "reliability.json").read_text())
    assert report["organization"]["findings"] == findings
    assert "organization" not in notes.model_dump()


def test_continuation_payload_and_fingerprint(monkeypatch):
    raw = [Segment(id="s1", start=0, end=1, text="New content")]
    assert "continuation" not in generation.generation_payload(raw, "", "", {"s1"})
    assert "Preserve every new" in generation.generation_payload(raw, "", "", {"s1"}, True)["continuation"]
    def fingerprint(continuation):
        return generation.draft_fingerprint(raw, "", "", "Class", "gemma", 2500, {"s1"}, continuation)
    first, later = fingerprint(False), fingerprint(True)
    assert first != later
    monkeypatch.setattr(generation, "CONTINUATION", "Updated instruction")
    assert fingerprint(True) != later
    assert fingerprint(False) == first


def test_complete_duplicate_union_survives_partial_duplicates_and_no_model_calls(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("No inference during consolidation")
    monkeypatch.setattr(generation.httpx, "post", forbidden)
    a = section("Particles move down the gradient.")
    b = section("Particles move down the gradient. Equilibrium preserves random molecular motion.", sid="s2")
    c = b.model_copy(update={"source_segment_ids": ["s3"]})
    notes = merge(a, b, c)
    assert {sid for s in notes.sections for sid in s.source_segment_ids} == {"s1", "s2", "s3"}
    assert sum(s.body.count("Particles move down the gradient.") for s in notes.sections) == 1
    assert "Equilibrium preserves random molecular motion." in notes.sections[-1].body
