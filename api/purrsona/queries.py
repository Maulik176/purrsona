"""The Cypher queries Purrsona depends on (listed in the README for the judges)."""
import re

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

TOPIC_NAMES = "MATCH (t:Topic) RETURN t.name AS name, t.keywords AS keywords"


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
    """Topics from the graph that the post mentions, by whole-word keyword match.

    Whole words matter: "bhaila" must not count as a mention of "AI".
    """
    found = []
    for r in rows(graph.ro_query(TOPIC_NAMES)):
        words = r["keywords"] or [r["name"].lower()]
        if any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in words):
            found.append(r["name"])
    return found


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


# The people and circles in the plugged-in voice graph (for the demo console's picker).
PEOPLE = """
MATCH (:User)-[:KNOWS]->(p:Person)-[:IN_CIRCLE]->(c:Circle)
RETURN p.id AS id, p.name AS name, p.headline AS headline, c.name AS circle
ORDER BY circle, name
"""

GRAPH_SUMMARY = """
MATCH (r:StyleRule) WHERE r.superseded_at IS NULL
WITH count(r) AS rules
MATCH (c:Circle)
RETURN rules, collect(c.name) AS circles
"""


def people(graph) -> list[dict]:
    return rows(graph.ro_query(PEOPLE))


def graph_summary(graph) -> dict:
    return rows(graph.ro_query(GRAPH_SUMMARY))[0]


# ---- Remembering an edit (the Learner's save step). Maulik's design, Oct 16: three small queries,
# one idea each: "this person is someone I know" -> "save what I changed" -> "remember the rule
# and where it came from".

# 1. Someone new joins the graph (only run for people the graph doesn't know yet).
SAVE_PERSON = """
MATCH (u:User), (c:Circle {name: $circle})
MERGE (p:Person {id: $person_id})
SET p.name = $name
MERGE (u)-[:KNOWS]->(p)
MERGE (p)-[:IN_CIRCLE]->(c)
"""

# 2. Save what I changed: the edit, linked to the draft it corrected (recreated after a re-plug).
SAVE_EDIT = """
MATCH (p:Person {id: $person_id})
OPTIONAL MATCH (pl:Platform {name: $platform})
MERGE (d:Draft {id: $draft_id})
  ON CREATE SET d.text = $before, d.at = $at
MERGE (d)-[:TO]->(p)
FOREACH (_ IN CASE WHEN pl IS NULL THEN [] ELSE [1] END | MERGE (d)-[:ON]->(pl))
CREATE (:Edit {id: $edit_id, before: $before, after: $after, at: $at})-[:OF]->(d)
"""

# 3. Remember the rule and the edit that taught it.
SAVE_LEARNED_RULE = """
MATCH (p:Person {id: $person_id}), (e:Edit {id: $edit_id})
CREATE (p)-[:HAS_RULE]->(r:StyleRule {id: $rule_id, kind: $kind, text: $text, created_at: $at})-[:LEARNED_FROM]->(e)
RETURN r.id AS id
"""

# A new narrower rule beats a broader one of the same kind (never deleted, only outranked).
LINK_OVERRIDES = """
MATCH (r:StyleRule {id: $rule_id})
UNWIND $old_ids AS oid
MATCH (old:StyleRule {id: oid})
CREATE (r)-[:OVERRIDES]->(old)
"""

# A person's old rule replaced by a newer one for the same person stays as history.
SUPERSEDE = "MATCH (old:StyleRule) WHERE old.id IN $old_ids SET old.superseded_at = $at"
