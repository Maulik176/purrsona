"""The Cypher queries Purrsona depends on (listed in the README for the judges)."""
from .db import rows

# Rule kinds that stack (several can apply at once). Every other kind has one winner.
STACKING_KINDS = ["phrase_use", "phrase_avoid"]

# Q2 + Q5: which style rules apply when I write to this person, on this platform, about these topics?
# The person may be unknown (someone new): then their circle (if we know it), the topics, the platform
# and my own global habits still apply. The narrowest scope wins: Person > Circle > Topic > Platform > User.
RULES_FOR_CONTEXT = """
OPTIONAL MATCH (p:Person {id: $person})
OPTIONAL MATCH (p)-[:IN_CIRCLE]->(c:Circle)
OPTIONAL MATCH (hint:Circle {name: $circle_hint})
OPTIONAL MATCH (pl:Platform {name: $platform})
OPTIONAL MATCH (t:Topic) WHERE t.name IN $topics
OPTIONAL MATCH (u:User)
WITH collect(DISTINCT p) + collect(DISTINCT c) + collect(DISTINCT hint)
     + collect(DISTINCT t) + collect(DISTINCT pl) + collect(DISTINCT u) AS scopes
UNWIND scopes AS s
WITH DISTINCT s
MATCH (s)-[:HAS_RULE]->(r:StyleRule)
WHERE r.superseded_at IS NULL
OPTIONAL MATCH (r)-[:LEARNED_FROM]->(ev)
WITH s, r, count(ev) AS evidence, max(ev.at) AS last_seen
OPTIONAL MATCH (r)-[:OVERRIDES]->(old:StyleRule)
WITH s, r, evidence, last_seen, collect(old.text) AS replaces,
     CASE labels(s)[0] WHEN 'Person' THEN 1 WHEN 'Circle' THEN 2 WHEN 'Topic' THEN 3
                       WHEN 'Platform' THEN 4 ELSE 5 END AS rank
ORDER BY rank, evidence DESC
WITH r.kind AS kind,
     collect({id: r.id, text: r.text, scope: labels(s)[0], scope_name: coalesce(s.name, s.id),
              rank: rank, evidence: evidence, last_seen: last_seen, replaces: replaces}) AS candidates
RETURN kind, CASE WHEN kind IN $stacking THEN candidates ELSE candidates[0..1] END AS chosen
ORDER BY kind
"""

PERSON_CONTEXT = """
OPTIONAL MATCH (p:Person {id: $person})
OPTIONAL MATCH (p)-[:IN_CIRCLE]->(c:Circle)
RETURN p IS NOT NULL AS known, p.name AS name, c.name AS circle
"""

TOPIC_NAMES = "MATCH (t:Topic) RETURN t.name AS name"


def rules_for_context(graph, person: str, platform: str, topics: list[str], circle_hint: str | None = None) -> list[dict]:
    result = graph.ro_query(
        RULES_FOR_CONTEXT,
        {"person": person, "platform": platform, "topics": topics,
         "circle_hint": circle_hint, "stacking": STACKING_KINDS},
    )
    chosen = []
    for row in rows(result):
        for rule in row["chosen"]:
            chosen.append({"kind": row["kind"], **rule})
    return chosen


def person_context(graph, person: str) -> dict:
    return rows(graph.ro_query(PERSON_CONTEXT, {"person": person}))[0]


def topics_in(graph, text: str) -> list[str]:
    """Topics from the graph that the post mentions (simple keyword match for v1)."""
    lowered = text.lower()
    return [r["name"] for r in rows(graph.ro_query(TOPIC_NAMES)) if r["name"].lower() in lowered]


# Saves a draft and which rules shaped it (feeds Q6 "why this draft" and Q9 "signature style").
RECORD_DRAFT = """
CREATE (d:Draft {id: $id, text: $text, at: $at})
WITH d
OPTIONAL MATCH (p:Person {id: $person})
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (d)-[:TO]->(p))
WITH d
OPTIONAL MATCH (pl:Platform {name: $platform})
FOREACH (_ IN CASE WHEN pl IS NULL THEN [] ELSE [1] END | CREATE (d)-[:ON]->(pl))
WITH d
OPTIONAL MATCH (t:Topic) WHERE t.name IN $topics
FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | CREATE (d)-[:ABOUT]->(t))
WITH DISTINCT d
OPTIONAL MATCH (r:StyleRule) WHERE r.id IN $rule_ids
FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END | CREATE (d)-[:USED]->(r))
RETURN d.id AS id
"""


def record_draft(graph, draft_id: str, text: str, at: int, person: str, platform: str,
                 topics: list[str], rule_ids: list[str]) -> None:
    graph.query(RECORD_DRAFT, {"id": draft_id, "text": text, "at": at, "person": person,
                               "platform": platform, "topics": topics, "rule_ids": rule_ids})
