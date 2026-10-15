"""The virtual key: a software stand-in for the ESP32 memory key.

Plug in = build the key's graph from its snapshot. Unplug = GRAPH.DELETE (the AI forgets).
The real key will hand over the same snapshot shape through the bridge.
"""
import time
from pathlib import Path

from .db import graph_name, rows
from .snapshot import build_graph, load_snapshot_file

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

COUNTS = "MATCH (n) WITH count(n) AS nodes OPTIONAL MATCH ()-[r]->() RETURN nodes, count(r) AS edges"


def plug(db, key_id: str) -> dict:
    started = time.perf_counter()
    snap = load_snapshot_file(SEED_DIR / f"{key_id}.json")
    name = graph_name(key_id)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    graph = db.select_graph(name)
    build_graph(graph, snap)
    counts = rows(graph.ro_query(COUNTS))[0]
    return {"key_id": key_id, "graph": name, **counts,
            "load_ms": round((time.perf_counter() - started) * 1000, 1)}


def unplug(db, key_id: str) -> dict:
    name = graph_name(key_id)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    return {"key_id": key_id, "graph": name, "forgotten": name not in db.list_graphs()}
