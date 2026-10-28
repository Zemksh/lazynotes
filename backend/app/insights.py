"""Optional, CPU-only analysis of saved outputs; never invokes transcription or an LLM."""
import math
import re
from typing import Literal

import numpy as np
from pydantic import Field
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
from threadpoolctl import threadpool_limits

from .schemas import Result, Section, StrictModel, bullet_text
from .transcription import Diagnostics, review_diagnostics


class FlaggedPassage(StrictModel):
    text: str
    reason: str
    headings: list[str]


class TranscriptionInsights(StrictModel):
    method: Literal["rule_based"] = "rule_based"
    status: Literal["available", "partial", "unavailable", "mock"]
    total_passages: int
    analyzed_passages: int
    flagged_passages: int
    affected_sections: int
    passages: list[FlaggedPassage]


class ConceptGroup(StrictModel):
    terms: list[str]
    section_indices: list[int]
    headings: list[str]


class ConceptInsights(StrictModel):
    method: Literal["tfidf_agglomerative"] = "tfidf_agglomerative"
    status: Literal["available", "insufficient_text"]
    groups: list[ConceptGroup] = Field(default_factory=list)
    ungrouped_sections: int = 0


class LectureInsights(StrictModel):
    version: Literal[2] = 2
    mock: bool
    transcription: TranscriptionInsights
    concepts: ConceptInsights


def transcription_insights(result: Result, diagnostics: list[Diagnostics]) -> TranscriptionInsights:
    sources = {s.id: s for s in result.transcript}
    # Missing/nonfinite diagnostics are unknown, never counted as reliable.
    checked = {d.segment_id: d for d in diagnostics if d.segment_id in sources and all(
        math.isfinite(value) for value in (d.avg_logprob, d.no_speech_prob, d.compression_ratio)
    )} if not result.mock else {}
    findings = {f.source_segment_ids[0]: f for f in review_diagnostics(list(checked.values()))}
    affected = set()
    passages = []
    for sid, segment in sources.items():
        if sid not in findings:
            continue
        headings = []
        for i, section in enumerate(result.notes.sections):
            if sid in section.source_segment_ids:
                affected.add(i)
                if section.heading not in headings:
                    headings.append(section.heading)
        passages.append(FlaggedPassage(text=segment.text, reason=findings[sid].reason, headings=headings))
    status = "mock" if result.mock else (
        "unavailable" if not checked else "available" if len(checked) == len(sources) else "partial"
    )
    return TranscriptionInsights(status=status, total_passages=len(sources), analyzed_passages=len(checked),
        flagged_passages=len(passages), affected_sections=len(affected), passages=passages)


def section_text(section: Section) -> str:
    parts = [section.heading, section.subheading, section.body]
    for bullet in section.bullets:
        parts.append(bullet_text(bullet))
        if not isinstance(bullet, str):
            parts.extend(bullet.children)
    if section.comparison:
        parts.extend(section.comparison.columns)
        parts.extend(cell for row in section.comparison.rows for cell in row)
    text = " ".join(parts)
    for sid in section.source_segment_ids:
        text = re.sub(r"(?<!\w)" + re.escape(sid) + r"(?!\w)", " ", text)
    # Older generated prose can contain inline Whisper IDs, including uncited IDs.
    return re.sub(r"\bs\d{5,}\b", " ", text, flags=re.IGNORECASE)


def concept_groups(sections: list[Section]) -> ConceptInsights:
    """Learn vocabulary and groups per lecture, without supplied topic labels.

    Average-link cosine distance < 0.7 joins related sections. This is a grouping
    hyperparameter, NOT a correctness score. No fixed cluster count or forced pairs.
    At most 100 sections (the saved-notes limit) bounds the pairwise matrix.
    Singletons remain visible as standalone sections, not claimed discoveries.
    """
    if not sections:
        return ConceptInsights(status="insufficient_text")
    # Unigrams retain shared terminology when sections use different phrasing;
    # unique bigrams previously diluted overlap in short study notes.
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 1),
                                 sublinear_tf=True, max_features=5000)
    try:
        vectors = vectorizer.fit_transform([section_text(s) for s in sections])
    except ValueError as exc:
        if "empty vocabulary" not in str(exc):
            raise
        return ConceptInsights(status="insufficient_text", ungrouped_sections=len(sections))
    indices = np.flatnonzero(np.asarray(vectors.getnnz(axis=1)))
    if len(indices) == 0:
        return ConceptInsights(status="insufficient_text", ungrouped_sections=len(sections))
    selected = vectors[indices]
    with threadpool_limits(limits=1):
        if len(indices) == 1:
            labels = [0]
        else:
            distances = np.clip(1 - (selected @ selected.T).toarray(), 0, 1)
            np.fill_diagonal(distances, 0)
            labels = AgglomerativeClustering(n_clusters=None, distance_threshold=0.7,
                metric="precomputed", linkage="average").fit_predict(distances)
    clusters: dict[int, list[int]] = {}
    for index, label in zip(indices, labels):
        clusters.setdefault(int(label), []).append(int(index))
    names = vectorizer.get_feature_names_out()
    groups = []
    for members in sorted(clusters.values(), key=lambda group: group[0]):
        weights = np.asarray(vectors[members].mean(axis=0)).ravel()
        ranked = sorted(np.flatnonzero(weights), key=lambda i: (-weights[i], names[i]))[:5]
        groups.append(ConceptGroup(terms=[str(names[i]) for i in ranked], section_indices=members,
            headings=[sections[i].heading + (f" · {sections[i].subheading}" if sections[i].subheading else "")
                      for i in members]))
    return ConceptInsights(status="available", groups=groups, ungrouped_sections=len(sections) - len(indices))


def build_insights(result: Result, diagnostics: list[Diagnostics]) -> LectureInsights:
    return LectureInsights(mock=result.mock, transcription=transcription_insights(result, diagnostics),
                           concepts=concept_groups(result.notes.sections))
