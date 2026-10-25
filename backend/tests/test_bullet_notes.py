import json
import re

import pytest
from pydantic import ValidationError

from app import generation
from app.reliability import save_reliability
from app.schemas import BulletGroup, GeneratedNotes, Notes, Section, Segment, generation_schema


def test_generation_schema_requires_content_without_breaking_saved_notes():
    schema = generation_schema()
    section = schema['$defs']['Section']
    assert 'bullets' in section['required']
    assert section['properties']['bullets']['minItems'] == 1
    string = section['properties']['bullets']['items']['anyOf'][0]
    child = schema['$defs']['BulletGroup']['properties']['children']['items']
    for constraint in (string, child):
        assert constraint['minLength'] == 1
        for blank in ('', ' ', '\n\t'):
            assert re.fullmatch(constraint['pattern'], blank) is None
        assert re.fullmatch(constraint['pattern'], 'A definition.\nA qualification.')
    # Legacy paragraph-only results remain readable; do not mutate saved schema.
    assert 'bullets' not in Notes.model_json_schema()['$defs']['Section']['required']
    assert Notes(title='Old', sections=[Section(heading='Topic', body='Explanation',
                                               source_segment_ids=['s1'])])


@pytest.mark.parametrize('content,code', [({}, 'section_content_missing'),
                                         ({'bullets': [' ']}, 'blank_bullet')])
def test_empty_section_failures_have_actionable_private_diagnostics(content, code):
    with pytest.raises(ValidationError) as error:
        GeneratedNotes.model_validate({'sections': [dict(heading='Private title',
            source_segment_ids=['s1'], **content)]})
    assert generation.validation_details(error.value) == f'sections.0:{code}'


def raw():
    return [Segment(id="s1", start=0, end=1, text="Diffusion does not require ATP."),
            Segment(id="s2", start=1, end=2, text="Active transport requires ATP. Dye demonstrates diffusion.")]


def test_bullet_schema_legacy_nested_and_no_arbitrary_count_or_text_cap():
    legacy = Section(heading="Old", body="Existing paragraphs", source_segment_ids=["s1"])
    assert legacy.body == "Existing paragraphs"
    bullets = [f"Important detail {i}" for i in range(40)] + ["technical detail " * 200,
               BulletGroup(text="Example", children=["Dye movement.", "Only under the stated conditions."])]
    section = Section(heading="Transport", bullets=bullets, source_segment_ids=["s1"])
    assert Section.model_validate_json(section.model_dump_json()) == section
    assert len(section.bullets) == 42
    with pytest.raises(ValidationError):
        BulletGroup(text="Parent", children=[" "])
    with pytest.raises(ValidationError):
        BulletGroup(text="Parent", children=[])


def test_nested_generation_checkpoints_and_internal_reliability(monkeypatch, tmp_path):
    generated = GeneratedNotes(sections=[Section(heading="Transport", subheading="Energy",
        bullets=["Diffusion does not require ATP.", BulletGroup(text="Active transport",
            children=["Requires ATP.", "Has 99 stages."])], source_segment_ids=["s1", "s2"])])
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": generated.model_dump_json(exclude_defaults=True)}}
    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(generation.httpx, "post", post)
    notes = generation.generate_notes(raw(), "Transport", "", "Known title", tmp_path / 'drafts')
    assert notes.title == "Known title"
    assert notes.sections[0].body == ""
    assert notes.sections[0].bullets == generated.sections[0].bullets
    assert generation.generate_notes(raw(), "Transport", "", "Known title", tmp_path / 'drafts') == notes
    save_reliability(tmp_path, raw(), [], notes)
    report = json.loads((tmp_path / 'reliability.json').read_text())
    assert any(f['location'] == 'bullets.1.children.1' and 'number_not_in_evidence' in f['reasons']
               for f in report['faithfulness']['findings'])
    assert len(calls) == 1
    assert calls[0]['json']['format'] == generation_schema()
    assert "Use bullets by default" in calls[0]['json']['messages'][0]['content']
    assert "review" not in notes.model_dump_json()


def test_parent_aware_deduplication_preserves_unique_children_and_refs():
    def draft(sid, bullets):
        return Notes(title="Class", sections=[Section(heading="Transport", bullets=bullets, source_segment_ids=[sid])])
    first = draft("s1", ["ATP required", BulletGroup(text="Diffusion", children=["Moves down a gradient."])])
    second = draft("s2", ["ATP required", BulletGroup(text="Diffusion", children=["Moves down a gradient.",
        "Example: dye spreads through water.", "Does not require ATP."]),
        BulletGroup(text="Active transport", children=["Does not require ATP."]), "ATP is needed"])
    before = second.model_dump_json()
    notes = generation.consolidate([first, second], raw())
    all_bullets = [b for s in notes.sections for b in s.bullets]
    assert all_bullets.count("ATP required") == 1
    assert "ATP is needed" in all_bullets  # Paraphrases are never merged.
    groups = {b.text: b.children for b in all_bullets if isinstance(b, BulletGroup)}
    assert groups["Diffusion"] == ["Moves down a gradient.", "Example: dye spreads through water.", "Does not require ATP."]
    assert groups["Active transport"] == ["Does not require ATP."]
    assert notes.sections[0].source_segment_ids == ["s1", "s2"]
    assert second.model_dump_json() == before


def test_plain_parent_can_gain_nested_details_without_losing_sources():
    notes = [Notes(title="Class", sections=[Section(heading="Topic", bullets=bullets, source_segment_ids=[sid])])
             for sid, bullets in [("s1", ["Diffusion"]), ("s2", [BulletGroup(text="Diffusion", children=["Dye example."])])]]
    merged = generation.consolidate(notes, raw())
    assert len(merged.sections) == 1
    assert merged.sections[0].bullets == [BulletGroup(text="Diffusion", children=["Dye example."])]
    assert merged.sections[0].source_segment_ids == ["s1", "s2"]
