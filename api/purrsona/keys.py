"""The virtual key: a software stand-in for the ESP32 memory key.

Plug in = build the key's graph from its snapshot. Unplug = GRAPH.DELETE (the AI forgets).
The real key will hand over the same snapshot shape through the bridge.
"""
import json
import os
import time
from pathlib import Path

from .db import graph_name, rows
from .learning import apply_event
from .snapshot import build_graph, load_snapshot_file

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seed"


def events_path(key_id: str) -> Path:
    """The key's memory log: one JSON line per lesson. Kept out of git (it can hold real names)."""
    base = Path(os.getenv("PURRSONA_KEYS_DIR", ROOT / "private" / "keys"))
    return base / f"{key_id}.events.jsonl"


def load_events(key_id: str) -> list[dict]:
    path = events_path(key_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_event(key_id: str, event: dict) -> int:
    """Write a lesson to the key (the virtual key's equivalent of APPEND). Returns the memory count."""
    path = events_path(key_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return len(load_events(key_id))


def reset_memories(key_id: str) -> None:
    """Demo helper: forget everything the key learned (back to the seed)."""
    path = events_path(key_id)
    if path.exists():
        path.write_text("", encoding="utf-8")

COUNTS = "MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r]->() RETURN nodes, count(r) AS edges"


def plug(db, key_id: str, seed: str | None = None) -> dict:
    """Build the key's graph: its snapshot first, then replay every lesson in its memory log."""
    started = time.perf_counter()
    snap = load_snapshot_file(SEED_DIR / f"{seed or key_id}.json")
    name = graph_name(key_id)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    graph = db.select_graph(name)
    build_graph(graph, snap)
    events = load_events(key_id)
    for event in events:
        apply_event(graph, event)
    counts = rows(graph.ro_query(COUNTS))[0]
    return {"key_id": key_id, "graph": name, **counts, "memories": len(events),
            "load_ms": round((time.perf_counter() - started) * 1000, 1)}


def unplug(db, key_id: str) -> dict:
    name = graph_name(key_id)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    return {"key_id": key_id, "graph": name, "forgotten": name not in db.list_graphs()}
