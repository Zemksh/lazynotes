from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


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


class Comparison(StrictModel):
    columns: list[str] = Field(min_length=2, max_length=5)
    rows: list[list[str]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def rectangular(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Comparison rows must match columns")
        if any(not cell.strip() or len(cell) > 1000 for row in [self.columns, *self.rows] for cell in row):
            raise ValueError("Comparison cells must be nonempty and bounded")
        return self


class BulletGroup(StrictModel):
    text: str = Field(min_length=1)
    children: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def nonempty_children(self):
        if any(not child.strip() for child in self.children):
            raise ValueError("Nested bullets must be nonempty")
        return self


Bullet = str | BulletGroup


def bullet_text(bullet: Bullet) -> str:
    return bullet if isinstance(bullet, str) else bullet.text


class Section(StrictModel):
    heading: str = Field(min_length=1, max_length=200)
    subheading: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=8000)
    bullets: list[Bullet] = Field(default_factory=list)
    comparison: Comparison | None = None
    source_segment_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def substantive(self):
        if not (self.body or self.bullets or self.comparison):
            raise PydanticCustomError("section_content_missing", "Section needs study content")
        if any(not bullet_text(bullet).strip() for bullet in self.bullets):
            raise PydanticCustomError("blank_bullet", "Bullets must be nonempty")
        return self


class ReviewItem(StrictModel):
    reason: str = Field(min_length=1, max_length=800)
    source_segment_ids: list[str] = Field(min_length=1)
    kind: Literal["model", "heuristic"] = "model"


class GeneratedNotes(StrictModel):
    """Model output only; Python supplies saved metadata."""
    sections: list[Section] = Field(min_length=1, max_length=100)


class Notes(GeneratedNotes):
    title: str = Field(min_length=1, max_length=160)


def generation_schema() -> dict:
    """Constrain new bullet-first output without changing legacy saved notes."""
    schema = GeneratedNotes.model_json_schema()
    section = schema["$defs"]["Section"]
    section["required"].append("bullets")
    bullets = section["properties"]["bullets"]
    bullets["minItems"] = 1
    # Mirror the nonblank Python checks in constrained decoding too.
    for branch in bullets["items"]["anyOf"]:
        if branch.get("type") == "string":
            branch.update(minLength=1, pattern=r"[\s\S]*\S[\s\S]*")
    group = schema["$defs"]["BulletGroup"]["properties"]
    group["text"]["pattern"] = r"[\s\S]*\S[\s\S]*"
    group["children"]["items"].update(minLength=1, pattern=r"[\s\S]*\S[\s\S]*")
    return schema


def validate_sources(notes: Notes, segments: list[Segment], required: set[str] | None = None) -> Notes:
    valid = {s.id for s in segments}
    used = set()
    for item in notes.sections:
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

    @model_validator(mode="before")
    @classmethod
    def legacy_notes(cls, value):
        # Read older saved results without exposing their former reliability UI.
        if isinstance(value, dict) and isinstance(value.get("notes"), dict):
            value = {**value, "notes": dict(value["notes"])}
            value["notes"].pop("review_items", None)
            value["warnings"] = []
        return value

    @model_validator(mode="after")
    def integrity(self):
        ids = [s.id for s in self.transcript]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate segment IDs")
        validate_sources(self.notes, self.transcript)
        return self
