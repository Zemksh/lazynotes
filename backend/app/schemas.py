from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Segment(StrictModel):
    id: str
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Segment end precedes start")
        return self


class Section(StrictModel):
    heading: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    source_segment_ids: list[str] = Field(min_length=1)


class ReviewItem(StrictModel):
    reason: str = Field(min_length=1, max_length=800)
    source_segment_ids: list[str] = Field(min_length=1)
    kind: Literal["model", "heuristic"] = "model"


class Notes(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    sections: list[Section] = Field(min_length=1, max_length=100)
    review_items: list[ReviewItem] = Field(default_factory=list)


def validate_sources(notes: Notes, segments: list[Segment], required: set[str] | None = None) -> Notes:
    valid = {s.id for s in segments}
    used = set()
    for item in [*notes.sections, *notes.review_items]:
        if not set(item.source_segment_ids) <= valid:
            raise ValueError("Notes reference nonexistent transcript segments")
        if len(item.source_segment_ids) != len(set(item.source_segment_ids)):
            raise ValueError("Duplicate source references")
        used.update(item.source_segment_ids)
    if required and not required <= used:
        raise ValueError("Consolidation lost source references")
    return notes


class Result(StrictModel):
    job_id: str
    mock: bool
    duration_seconds: float
    notes: Notes
    transcript: list[Segment]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def integrity(self):
        ids = [s.id for s in self.transcript]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate segment IDs")
        validate_sources(self.notes, self.transcript)
        return self
