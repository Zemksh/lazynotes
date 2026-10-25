"""Conservative, model-free organization. Similarity is diagnostic only."""
import re
from difflib import SequenceMatcher

from .schemas import BulletGroup, Notes, Section, bullet_text


def key(text: str) -> str:
    return " ".join(text.split())


def topic(section: Section) -> tuple[str, str]:
    # Only explicit structural headings, never inferred semantic equivalence.
    match = re.fullmatch(r"(Introduction to|Examples of|Applications of|Discussion of)\s+(.+)",
                         section.heading, re.IGNORECASE)
    if match:
        roles = {"introduction to": "Introduction", "examples of": "Examples",
                 "applications of": "Applications", "discussion of": "Discussion"}
        return match[2], section.subheading or roles[match[1].lower()]
    return section.heading, section.subheading


def sentences(text: str) -> list[str]:
    # Split only clear sentence ends before uppercase letters; leave decimals,
    # equations and ambiguous abbreviations intact rather than risk losing text.
    return re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)


def context_dependent(text: str) -> bool:
    return bool(re.match(
        r"^(?:\d+[.)]|it\b|this\b|that\b|these\b|those\b|they\b|then\b|otherwise\b|repeat\b)",
        text.strip(), re.IGNORECASE,
    ))


def reusable(text: str) -> bool:
    # Repeated short steps or context-dependent pronouns may mean different things.
    return len(text.split()) >= 4 and not context_dependent(text)


def unique_children(children: list[str]) -> list[str]:
    result, seen = [], set()
    for child in children:
        if context_dependent(child) or key(child) not in seen:
            result.append(child)
            seen.add(key(child))
    return result


def organize(sections: list[Section]) -> list[Section]:
    result, seen, exact_sections, bullet_owners = [], {}, {}, {}
    for original in sections:
        section = original.model_copy(deep=True)
        section.heading, section.subheading = topic(section)
        scope = (key(section.heading).casefold(), key(section.subheading).casefold())
        whole = (scope, section.model_dump_json(exclude={"source_segment_ids"}))
        if whole in exact_sections:
            target = exact_sections[whole]
            target.source_segment_ids = list(dict.fromkeys(target.source_segment_ids + section.source_segment_ids))
            continue
        exact_sections[whole] = section

        def retain(text: str, kind: str) -> bool:
            if not reusable(text):
                return True
            identity = (scope, kind, key(text))  # Do not fold case, numbers, negation or punctuation.
            if identity in seen:
                target = seen[identity]
                target.source_segment_ids = list(dict.fromkeys(target.source_segment_ids + section.source_segment_ids))
                return False
            seen[identity] = section
            return True

        paragraphs = []
        for paragraph in section.body.split('\n\n'):
            # Preserve line-based lists/equations as a unit; no flattening of formatting.
            parts = [paragraph] if '\n' in paragraph else sentences(paragraph)
            kept = [part for part in parts if retain(part, "body")]
            if kept:
                paragraphs.append(' '.join(kept))
        section.body = '\n\n'.join(paragraphs)
        bullets = []
        for bullet in section.bullets:
            text = bullet_text(bullet)
            identity = (scope, key(text))
            # Numbered/referential steps may need repetition even when identical.
            repeatable_step = context_dependent(text)
            if not isinstance(bullet, str):
                bullet.children = unique_children(bullet.children)
            if not repeatable_step and identity in bullet_owners:
                owner, owner_bullets, position = bullet_owners[identity]
                existing = owner_bullets[position]
                if not isinstance(bullet, str):
                    if isinstance(existing, str):
                        existing = BulletGroup(text=existing, children=list(bullet.children))
                        owner_bullets[position] = existing
                    else:
                        existing.children = unique_children(existing.children + bullet.children)
                owner.source_segment_ids = list(dict.fromkeys(owner.source_segment_ids + section.source_segment_ids))
                continue
            if not repeatable_step:
                bullet_owners[identity] = (section, bullets, len(bullets))
            bullets.append(bullet)
        section.bullets = bullets
        if section.body or section.bullets or section.comparison:
            result.append(section)
        else:
            # This object will not be emitted; future whole-section duplicates must
            # flow through the surviving sentence owners instead.
            exact_sections.pop(whole, None)
    return result


def near_duplicate_introductions(notes: Notes) -> list[dict]:
    findings, prior = [], {}
    for index, section in enumerate(notes.sections):
        heading, subheading = topic(section)
        if subheading and subheading.casefold() not in {"introduction", "overview"}:
            continue
        opening = sentences(section.body.strip() or (bullet_text(section.bullets[0]) if section.bullets else ""))[0]
        if len(opening.split()) < 6:
            continue
        scope = key(heading).casefold()
        for other_index, other in prior.get(scope, []):
            if key(opening) == key(other):
                continue
            # Bounded word-level comparison keeps malformed long openings cheap.
            similarity = SequenceMatcher(None, opening.split()[:100], other.split()[:100], autojunk=False).ratio()
            if similarity >= 0.8:
                findings.append({"section_indices": [other_index, index],
                                 "reason": "similar_opening_requires_editorial_review",
                                 "lexical_similarity": round(similarity, 3),
                                 "source_segment_ids": list(dict.fromkeys(
                                     notes.sections[other_index].source_segment_ids + section.source_segment_ids))})
        prior.setdefault(scope, []).append((index, opening))
    return findings
