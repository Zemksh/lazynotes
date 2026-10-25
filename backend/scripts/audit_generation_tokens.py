"""Offline prompt/JSON size comparison; NO model calls or recording access.

Run from the repo root: PYTHONPATH=backend python backend/scripts/audit_generation_tokens.py
Uses existing synthetic benchmark transcripts and mocked study-note output.
Token estimates are characters / 4, NOT Gemma tokenizer counts. Schema JSON is
reported separately: Ollama's format constraint is not necessarily prompt text.
The first-round comparison uses recorded pre-change fixture sizes; other
comparisons remain hypothetical. This script does not change production behavior.
"""
import json
import math
import runpy
from pathlib import Path

from app.config import settings
from app.generation import CHUNK_TASK, SYSTEM, chunk_transcript, generation_payload, relevant_context
from app.schemas import Notes, Segment

ROOT = Path(__file__).resolve().parents[1]


def encoded(value, compact=False):
    return json.dumps(value, ensure_ascii=False, **({"separators": (",", ":")} if compact else {}))


def size(text):
    return {"characters": len(text), "estimated_tokens_chars_div_4": math.ceil(len(text) / 4)}


def matched_terms(topics, syllabus, chunk):
    # Candidate only: retain matching terminology, not syllabus propositions.
    from app.reliability import words
    spoken = words(" ".join(s.text for s in chunk))
    return sorted(spoken & words(topics + " " + syllabus))


def prompt_sizes(chunk, topics, syllabus, title):
    payload = {"task": CHUNK_TASK, "suggested_title": title,
               **relevant_context(topics, syllabus, chunk),
               "transcript": [s.model_dump() for s in chunk]}
    baseline = SYSTEM + encoded(payload)
    no_times = {**payload, "transcript": [{"id": s.id, "text": s.text} for s in chunk]}
    no_title = {k: v for k, v in no_times.items() if k != "suggested_title"}
    terms_only = {k: v for k, v in no_title.items() if k not in {"topics_context_only", "syllabus_context_only"}}
    terms = matched_terms(topics, syllabus, chunk)
    if terms:
        terms_only["terminology_context_only"] = terms
    # Keep SYSTEM unchanged to isolate payload savings; title instruction removal
    # and possible prompt tightening are deliberately not counted here.
    return {"baseline": size(baseline), "without_timestamps": size(SYSTEM + encoded(no_times)),
            "without_title": size(SYSTEM + encoded(no_title)),
            "matching_terms_only": size(SYSTEM + encoded(terms_only)),
            "compact_json_and_terms": size(SYSTEM + encoded(terms_only, compact=True))}


def output_sizes(notes):
    minimal = notes.model_dump(exclude_defaults=True)
    sections = {"sections": minimal["sections"]}
    refs = {"sections": [{("refs" if k == "source_segment_ids" else k): v
                          for k, v in section.items()} for section in sections["sections"]]}
    return {"expanded_fixture": size(encoded(notes.model_dump(), compact=True)),
            "optional_fields_omitted": size(encoded(minimal, compact=True)),
            "python_supplies_title": size(encoded(sections, compact=True)),
            "refs_wire_alias": size(encoded(refs, compact=True))}


def bullet_format_sizes(notes):
    """Formatting-only comparison: retain every original content string."""
    baseline = notes.model_dump(exclude_defaults=True, exclude={"title"})
    flat = json.loads(json.dumps(baseline))
    for section in flat["sections"]:
        if section.get("body"):
            section["bullets"] = [section.pop("body"), *section.get("bullets", [])]
    nested = json.loads(json.dumps(flat))
    for section in nested["sections"]:
        bullets = section.get("bullets", [])
        if len(bullets) > 1:
            section["bullets"] = [{"text": bullets[0], "children": bullets[1:]}]
    return {"paragraph_and_bullets": size(encoded(baseline, compact=True)),
            "flat_bullets_same_words": size(encoded(flat, compact=True)),
            "nested_bullets_same_words": size(encoded(nested, compact=True))}


def chunk_grid(segments):
    rows = []
    for budget in (6000, 8000, 10000, 12000, 16000):
        chunks = chunk_transcript(segments, budget)
        seen, repeated = set(), 0
        for chunk in chunks:
            repeated += sum(len(s.text) for s in chunk if s.id in seen)
            seen.update(s.id for s in chunk)
        rows.append({"character_budget": budget, "calls_before_retries": len(chunks),
                     "repeated_transcript_characters": repeated,
                     "total_prompt_estimate": sum(prompt_sizes(c, "", "", "Class")["baseline"]
                                                  ["estimated_tokens_chars_div_4"] for c in chunks),
                     "max_output_tokens_per_call": settings().ollama_num_predict})
    return rows


def main():
    fixtures = runpy.run_path(str(ROOT / "scripts" / "benchmark_generation.py"))
    study = runpy.run_path(str(ROOT / "tests" / "test_study_notes.py"))
    cases = {
        name: prompt_sizes(chunk, fixtures["TOPICS"], fixtures["SYLLABUS"], "Lecture notes")
        for name, chunk in fixtures["CHUNKS"].items()
    }
    cases["study"] = prompt_sizes(study["lecture"](), "Transport",
                                  "Diffusion and active transport.\nQuantum tunneling and particle physics.",
                                  "Membrane transport")
    # Controlled overhead stress case, not representative of a real lecture.
    seed = [*fixtures["TECH"], *fixtures["UNCLEAR"], *study["lecture"]()]
    synthetic = [Segment(id=f"s{i:05d}", start=i * 10, end=i * 10 + 10,
                         text=seed[i % len(seed)].text) for i in range(240)]
    # Exact character sizes captured by this audit before the approved changes.
    baseline_chars = {"technical": 2280, "unclear": 2141, "study": 2105}
    first_round = {}
    fixtures_to_measure = [
        (name, chunk, fixtures["TOPICS"], fixtures["SYLLABUS"])
        for name, chunk in fixtures["CHUNKS"].items()
    ] + [("study", study["lecture"](), "Transport",
          "Diffusion and active transport.\nQuantum tunneling and particle physics.")]
    for name, chunk, topics, syllabus in fixtures_to_measure:
        current = size(SYSTEM + encoded(generation_payload(chunk, topics, syllabus, {s.id for s in chunk})))
        first_round[name] = {"baseline_characters": baseline_chars[name], "current": current,
                             "character_reduction_percent": round(100 * (1 - current["characters"] / baseline_chars[name]), 1)}
    seen, total = set(), 0
    for chunk in chunk_transcript(synthetic, 10000):
        owned = {s.id for s in chunk} - seen
        total += size(SYSTEM + encoded(generation_payload(chunk, "", "", owned, continuation=bool(seen))))["estimated_tokens_chars_div_4"]
        seen.update(owned)
    first_round["synthetic_240_segments"] = {"baseline_estimated_tokens": 10381,
                                             "current_estimated_tokens": total,
                                             "calls_unchanged": 4}
    result = {"method": "characters / 4; excludes chat-template and grammar overhead; no inference",
              "bullet_format_same_content": bullet_format_sizes(study["study_notes"]()),
              "implemented_first_round": first_round,
              "prompt_fixtures": cases, "mock_output": output_sizes(study["study_notes"]()),
              "schema_transport_only": size(encoded(Notes.model_json_schema(), compact=True)),
              "synthetic_repetition_stress_only": chunk_grid(synthetic)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
