"""Benchmark Ollama notes generation on representative transcript chunks.

Runs the real /api/chat path (structured output + same options) for one model and
chunk, and reports timing, token, JSON-validity and source-reference metrics.
"""

import json
import sys
import time

import httpx

from app.config import settings
from app.generation import SYSTEM, generation_payload
from app.schemas import GeneratedNotes, Notes, Segment, generation_schema

TECH = [
    Segment(id="s00001", start=0, end=42.5, text="The Raft consensus algorithm elects leaders through terms and heartbeat timeouts."),
    Segment(id="s00002", start=42.5, end=85.0, text="A follower only advances its log when it receives a heartbeat from the leader."),
    Segment(id="s00003", start=85.0, end=130.2, text="The commit index tracks the highest contiguous applied entry in the replicated log."),
]

UNCLEAR = [
    Segment(id="s00001", start=0, end=31.4, text="So, uh, the, um, we were, like, talking about the uh, replication uh earlier today"),
    Segment(id="s00002", start=31.4, end=58.9, text="and the, uh, failover thing, you know, when the, when the primary, uh, goes down"),
]

CHUNKS = {"technical": TECH, "unclear": UNCLEAR}
TOPICS = "distributed systems replication consistency consensus"
SYLLABUS = "Replication and consensus.\nCommit protocols ensure durability."


def build_payload(chunk: list[Segment]) -> dict:
    return generation_payload(chunk, TOPICS, SYLLABUS, {s.id for s in chunk})


def run_model(model: str, chunk_name: str, chunk: list[Segment]) -> dict:
    cfg = settings()
    payload = build_payload(chunk)
    t0 = time.monotonic()
    response = httpx.post(
        f"{cfg.ollama_url.rstrip('/')}/api/chat",
        timeout=cfg.ollama_timeout_seconds,
        json={
            "model": model,
            "stream": False,
            "keep_alive": cfg.ollama_keep_alive,
            "format": generation_schema(),
            "options": {"temperature": 0, "num_ctx": 16384, "num_predict": cfg.ollama_num_predict},
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
        },
    )
    elapsed = time.monotonic() - t0
    response.raise_for_status()
    data = response.json()
    content = data.get("message", {}).get("content") or ""
    metrics = {
        "model": model,
        "chunk": chunk_name,
        "status": "ok",
        "total_duration": data.get("total_duration"),
        "load_duration": data.get("load_duration"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "prompt_eval_cached_count": data.get("prompt_eval_cached_count"),
        "prompt_eval_duration": data.get("prompt_eval_duration"),
        "eval_count": data.get("eval_count"),
        "eval_duration": data.get("eval_duration"),
        "wall_elapsed": round(elapsed, 2),
        "output_chars": len(content),
    }
    try:
        generated = GeneratedNotes.model_validate_json(content)
        notes = Notes(title="Lecture notes", sections=generated.sections)
        metrics["json_valid"] = True
        metrics["sections"] = len(notes.sections)
        metrics["section_bodies"] = [s.body for s in notes.sections]
        metrics["source_refs_valid"] = all(
            set(s.source_segment_ids) <= {seg.id for seg in chunk} for s in notes.sections
        )
        metrics["bullet_points"] = sum(len(s.bullets) for s in notes.sections)
    except Exception as exc:  # noqa: BLE001
        metrics["json_valid"] = False
        metrics["error"] = f"{type(exc).__name__}: {exc}"
    return metrics


if __name__ == "__main__":
    model = sys.argv[1]
    chunk_name = sys.argv[2]
    result = run_model(model, chunk_name, CHUNKS[chunk_name])
    print(json.dumps(result, indent=2))
