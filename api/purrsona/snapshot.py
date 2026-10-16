"""Build a voice graph from a key snapshot (the JSON a memory key hands over on plug-in)."""
import json
from pathlib import Path

# Each scope label and the property that identifies its nodes.
SCOPE_KEYS = {"User": "id", "Person": "id", "Circle": "name", "Platform": "name", "Topic": "name"}

INDEXES = [
    "CREATE INDEX FOR (p:Person) ON (p.id)",
    "CREATE INDEX FOR (c:Circle) ON (c.name)",
    "CREATE INDEX FOR (p:Platform) ON (p.name)",
    "CREATE INDEX FOR (t:Topic) ON (t.name)",
    "CREATE INDEX FOR (r:StyleRule) ON (r.id)",
    "CREATE INDEX FOR (d:Draft) ON (d.id)",
    "CREATE INDEX FOR (e:Edit) ON (e.id)",
    "CREATE INDEX FOR (p:Post) ON (p.id)",
]


def load_snapshot_file(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_graph(graph, snap: dict) -> None:
    """Create every node and relationship in the snapshot. The graph must be empty."""
    for q in INDEXES:
        graph.query(q)

    graph.query("CREATE (:User {id: $id, name: $name})", snap["user"])
    graph.query("UNWIND $names AS n CREATE (:Circle {name: n})", {"names": snap["circles"]})
    graph.query("UNWIND $names AS n CREATE (:Platform {name: n})", {"names": snap["platforms"]})
    keywords = snap.get("topic_keywords", {})
    graph.query(
        "UNWIND $topics AS t CREATE (:Topic {name: t.name, keywords: t.keywords})",
        {"topics": [{"name": n, "keywords": keywords.get(n, [n.lower()])} for n in snap["topics"]]},
    )
    graph.query("UNWIND $texts AS t CREATE (:Phrase {text: t})", {"texts": snap.get("phrases", [])})

    graph.query(
        """
        MATCH (u:User)
        UNWIND $topics AS tn
        MATCH (t:Topic {name: tn})
        CREATE (u)-[:INTERESTED_IN]->(t)
        """,
        {"topics": snap.get("user_topics", [])},
    )
    graph.query(
        """
        MATCH (u:User)
        UNWIND $people AS p
        MATCH (c:Circle {name: p.circle})
        CREATE (u)-[:KNOWS]->(x:Person {id: p.id, name: p.name, headline: p.headline})-[:IN_CIRCLE]->(c)
        """,
        {"people": snap["people"]},
    )
    graph.query(
        """
        UNWIND $people AS p
        UNWIND p.topics AS tn
        MATCH (x:Person {id: p.id}), (t:Topic {name: tn})
        CREATE (x)-[:INTERESTED_IN]->(t)
        """,
        {"people": snap["people"]},
    )

    graph.query(
        "UNWIND $posts AS p CREATE (:Post {id: p.id, text: p.text, platform: p.platform, at: p.at})",
        {"posts": snap.get("posts", [])},
    )
    # Each seeded edit corrects a draft that was written to a person on a platform.
    graph.query(
        """
        UNWIND $edits AS e
        MATCH (x:Person {id: e.person}), (pl:Platform {name: e.platform})
        CREATE (d:Draft {id: 'd-' + e.id, text: e.before, at: e.at})-[:TO]->(x)
        CREATE (d)-[:ON]->(pl)
        CREATE (:Edit {id: e.id, before: e.before, after: e.after, at: e.at})-[:OF]->(d)
        """,
        {"edits": snap.get("edits", [])},
    )
    deleted = [{"edit": e["id"], "phrase": ph} for e in snap.get("edits", []) for ph in e.get("deleted", [])]
    graph.query(
        """
        UNWIND $rows AS r
        MATCH (e:Edit {id: r.edit}), (ph:Phrase {text: r.phrase})
        CREATE (e)-[:DELETED]->(ph)
        """,
        {"rows": deleted},
    )

    rules = snap.get("rules", [])
    for label, key in SCOPE_KEYS.items():
        scoped = [r for r in rules if r["scope"] == label]
        if not scoped:
            continue
        # Labels can't be query parameters, so the scope label is part of the query text.
        graph.query(
            f"""
            UNWIND $rules AS r
            MATCH (s:{label} {{{key}: r.scope_id}})
            CREATE (s)-[:HAS_RULE]->(:StyleRule {{id: r.id, kind: r.kind, text: r.text, applies_to: r.applies_to,
                                                  created_at: r.created_at, superseded_at: r.superseded_at}})
            """,
            {"rules": scoped},
        )

    for label in ("Edit", "Post"):
        links = [
            {"rule": r["id"], "ev": ev}
            for r in rules
            for ev in r.get("learned_from", [])
            if (ev.startswith("e") if label == "Edit" else ev.startswith("p"))
        ]
        graph.query(
            f"""
            UNWIND $links AS l
            MATCH (r:StyleRule {{id: l.rule}}), (ev:{label} {{id: l.ev}})
            CREATE (r)-[:LEARNED_FROM]->(ev)
            """,
            {"links": links},
        )

    overrides = [{"rule": r["id"], "old": old} for r in rules for old in r.get("overrides", [])]
    graph.query(
        """
        UNWIND $rows AS o
        MATCH (a:StyleRule {id: o.rule}), (b:StyleRule {id: o.old})
        CREATE (a)-[:OVERRIDES]->(b)
        """,
        {"rows": overrides},
    )
