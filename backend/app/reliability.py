"""Internal screening, not factual verification or calibrated confidence.

Only transcript evidence is accepted. Findings never modify notes or transcripts.
Replace FaithfulnessChecker with an evaluated classifier/NLI implementation later.
"""
import json
import re
from pathlib import Path
from typing import Protocol

from .organization import near_duplicate_introductions
from .schemas import Notes, Segment, bullet_text
from .transcription import Diagnostics, review_diagnostics

STOP = set("a an the is are was were be been being of in on at to for from and or as by with this that it its".split())
NEGATIONS = {"no", "not", "never", "without", "cannot", "can't", "isn't", "doesn't"}


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+(?:'\w+)?", text.casefold())) - STOP


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


class FaithfulnessChecker(Protocol):
    def analyze(self, notes: Notes, transcript: list[Segment]) -> list[dict]: ...


class LexicalFaithfulness:
    """Cheap candidate flags: overlap is evidence retrieval, NOT entailment.

    English negation/stopwords, literal numbers; paraphrases, equations, units,
    and multilingual lectures need stronger evaluation. No finding means unknown,
    never 'verified'. Thresholds are screening rules, not hallucination rates.
    """

    def analyze(self, notes: Notes, transcript: list[Segment]) -> list[dict]:
        sources = {s.id: s.text for s in transcript}
        findings = []
        for index, section in enumerate(notes.sections):
            evidence = " ".join(sources[sid] for sid in section.source_segment_ids)
            candidates = sentences(evidence)
            claims = [("heading", section.heading)]
            if index == 0:
                claims.append(("title", notes.title))
            if section.subheading:
                claims.append(("subheading", section.subheading))
            claims += [(f"body.{i}", s) for i, s in enumerate(sentences(section.body))]
            for i, bullet in enumerate(section.bullets):
                claims.append((f"bullets.{i}", bullet_text(bullet)))
                if not isinstance(bullet, str):
                    claims += [(f"bullets.{i}.children.{j}", f"{bullet.text}: {child}")
                               for j, child in enumerate(bullet.children)]
            if section.comparison:
                claims += [(f"comparison.rows.{i}", "; ".join(
                    f"{column}: {cell}" for column, cell in zip(section.comparison.columns, row)
                )) for i, row in enumerate(section.comparison.rows)]
            for location, claim in claims:
                # The lecture title may be supported by any part of the lecture.
                scope = " ".join(sources.values()) if location == "title" else evidence
                claim_words = words(claim)
                overlap = len(claim_words & words(scope)) / max(1, len(claim_words))
                reasons = []
                if len(claim_words) >= 3 and overlap < 0.35:
                    reasons.append("low_lexical_support")
                numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", claim))
                if numbers - set(re.findall(r"\b\d+(?:\.\d+)?\b", scope)):
                    reasons.append("number_not_in_evidence")
                best = max(candidates, key=lambda s: len(words(s) & claim_words), default="")
                shared = len(words(best) & claim_words) / max(1, len(claim_words))
                if location not in {"heading", "subheading", "title"} and shared >= 0.6:
                    if bool(words(best) & NEGATIONS) != bool(claim_words & NEGATIONS):
                        reasons.append("possible_negation_mismatch")
                if reasons:
                    findings.append({
                        "section_index": index, "location": location,
                        "source_segment_ids": list(sources) if location == "title" else section.source_segment_ids,
                        "reasons": reasons, "lexical_overlap": round(overlap, 3),
                    })
        return findings


def save_reliability(
    folder: Path, transcript: list[Segment], diagnostics: list[Diagnostics],
    notes: Notes | None = None, checker: FaithfulnessChecker | None = None,
) -> None:
    report = {
        "version": 1,
        "organization": {
            "method": "lexical_openings_v1",
            "findings": near_duplicate_introductions(notes) if notes is not None else [],
        },
        "transcription": {
            "method": "whisper_heuristics_v1",
            "findings": [item.model_dump() for item in review_diagnostics(diagnostics)],
        },
        "faithfulness": {
            "method": type(checker).__name__ if checker else "LexicalFaithfulness",
            "status": "screened_not_verified" if notes is not None else "pending",
            "findings": (checker or LexicalFaithfulness()).analyze(notes, transcript) if notes is not None else [],
        },
    }
    temp = folder / "reliability.tmp"
    temp.write_text(json.dumps(report))
    temp.replace(folder / "reliability.json")
