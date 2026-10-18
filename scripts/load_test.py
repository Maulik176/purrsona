"""Load test (#11): many keys on one FalkorDB, one graph per key.

1. Plug in N made-up users: each gets its own graph `voice:load-<n>` built from the demo seed, plus one
   rule only that user has (a marker), so we can tell the graphs apart.
2. Draft lookups from many threads at once: for a random user, run what the bridge runs before Claude
   writes (who is this person, which topics, which rules win). Reports p50 / p95 / p99.
3. Isolation: every user's rules must include their own marker and no one else's.
4. Unplug everyone: GRAPH.DELETE each graph, and check none is left.

It never touches voice:demo-maulik. Run it against a database you can throw away, e.g.
  docker run -d --rm --name purrsona-load-db -p 6390:6379 falkordb/falkordb:v4.22.0
  FALKORDB_PORT=6390 uv run --project api python scripts/load_test.py --users 1000 --workers 32 --lookups 5000
"""
import argparse
import json
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from purrsona.db import connect  # noqa: E402
from purrsona.queries import person_context, rules_for_context, topics_in  # noqa: E402
from purrsona.snapshot import build_graph  # noqa: E402

SEED = json.loads((ROOT / "data/seed/demo-maulik.json").read_text())
POSTS = [  # made-up posts the lookups "reply" to
    ("priya-nair", "linkedin", "We just shipped the new Flow Designer approvals to production"),
    ("aarav-mehta", "x", "What a finish in the cricket last night"),
    ("rohan-desai", "linkedin", "Quarterly review is next Tuesday, agenda attached"),
    ("kabir-shah", "linkedin", "Excited to start my new role in AI research"),  # someone new: not in the graph
]


def name(n: int) -> str:
    return f"voice:load-{n:04d}"


def snapshot_for(n: int) -> dict:
    snap = deepcopy(SEED)
    snap["key_id"] = f"load-{n:04d}"
    snap["rules"].append({"id": f"marker-{n:04d}", "scope": "Person", "scope_id": "priya-nair", "kind": "phrase_use",
                          "text": f"Marker for load-{n:04d}", "created_at": 1790899200000, "learned_from": []})
    return snap


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def summary(label: str, values: list[float]) -> str:
    return (f"| {label} | {len(values)} | {statistics.median(values):.1f} | {pct(values, 95):.1f} | "
            f"{pct(values, 99):.1f} | {max(values):.1f} |")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=32, help="lookups running at the same time")
    parser.add_argument("--lookups", type=int, default=5000)
    parser.add_argument("--keep", action="store_true", help="leave the graphs in place afterwards")
    args = parser.parse_args()

    db = connect()
    leftover = [g for g in db.list_graphs() if g.startswith("voice:load-")]
    if leftover:
        sys.exit(f"{len(leftover)} voice:load-* graphs already exist; use a fresh database")
    memory_before = db.connection.info("memory")["used_memory"]

    plug_ms = []
    for n in range(args.users):
        started = time.perf_counter()
        build_graph(db.select_graph(name(n)), snapshot_for(n))
        plug_ms.append((time.perf_counter() - started) * 1000)
    memory_after = db.connection.info("memory")["used_memory"]

    def lookup(i: int):
        rng = random.Random(i)
        n = rng.randrange(args.users)
        person, platform, text = rng.choice(POSTS)
        graph = db.select_graph(name(n))
        started = time.perf_counter()
        who = person_context(graph, person)
        topics = topics_in(graph, text)
        rules = rules_for_context(graph, person, platform, topics, circle_hint=who["circle"] or "Friends")
        ms = (time.perf_counter() - started) * 1000
        markers = {r["text"] for r in rules if r["text"].startswith("Marker for")}
        expected = {f"Marker for load-{n:04d}"} if person == "priya-nair" else set()
        return ms, markers == expected

    started = time.perf_counter()
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lookup, range(args.lookups)))
    wall = time.perf_counter() - started
    lookup_ms = [ms for ms, _ in results]
    leaks = sum(not ok for _, ok in results)

    # Isolation, exhaustively: each user sees exactly their own marker.
    isolated = 0
    for n in range(args.users):
        rules = rules_for_context(db.select_graph(name(n)), "priya-nair", "linkedin", [])
        isolated += {r["text"] for r in rules if r["text"].startswith("Marker for")} == {f"Marker for load-{n:04d}"}

    forget_ms = []
    if not args.keep:
        for n in range(args.users):
            started = time.perf_counter()
            db.select_graph(name(n)).delete()
            forget_ms.append((time.perf_counter() - started) * 1000)
    left = [g for g in db.list_graphs() if g.startswith("voice:load-")]

    print(f"\n{args.users} users, one graph each, on one FalkorDB · {args.workers} lookups at once\n")
    print("| step | count | p50 ms | p95 ms | p99 ms | max ms |\n|---|---|---|---|---|---|")
    print(summary("plug in (build the graph from the seed)", plug_ms))
    print(summary("draft lookups (person, topics, rules)", lookup_ms))
    if forget_ms:
        print(summary("unplug (GRAPH.DELETE)", forget_ms))
    print(f"\nthroughput: {args.lookups / wall:.0f} draft lookups per second")
    print(f"memory: {(memory_after - memory_before) / args.users / 1024:.0f} KB per user graph")
    print(f"isolation: {isolated}/{args.users} users see only their own marker; "
          f"{leaks} of {args.lookups} concurrent lookups saw the wrong marker")
    print(f"after unplugging: {len(left)} graphs left" if not args.keep else f"kept {len(left)} graphs")
    if leaks or isolated != args.users or (not args.keep and left):
        sys.exit(1)


if __name__ == "__main__":
    main()
