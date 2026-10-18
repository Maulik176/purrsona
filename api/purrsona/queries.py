"""The Cypher queries Purrsona depends on (listed in the README for the judges)."""
import re
import time

from .db import rows

# Rule kinds that stack (several can apply at once). Every other kind has one winner.
STACKING_KINDS = ["phrase_use", "phrase_avoid"]

# Q2 + Q5: which style rules apply when I write to this person, on this platform, about these topics?
# The person may be unknown (someone new): then their circle (if we know it), the topics, the platform
# and my own global habits still apply. The narrowest scope wins: Person > Circle > Topic > Platform > User.
# $mode is 'reply' or 'post': a rule with applies_to (e.g. "own posts end with a question") only joins its mode.
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
WHERE r.superseded_at IS NULL AND coalesce(r.applies_to, 'any') IN ['any', $mode]
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

# Q1: who is this person to me, and how often have I written to them (drafts I actually sent)?
PERSON_CONTEXT = """
OPTIONAL MATCH (p:Person {id: $person})
OPTIONAL MATCH (p)-[:IN_CIRCLE]->(c:Circle)
OPTIONAL MATCH (p)-[:IS]->(rel:Relationship)
OPTIONAL MATCH (d:Draft)-[:TO]->(p) WHERE d.sent_at IS NOT NULL
RETURN p IS NOT NULL AS known, p.name AS name, c.name AS circle, rel.name AS relationship,
       count(d) AS sent, max(d.sent_at) AS last_sent
"""

# Your words for people: "manager" is part of Work, "customer" of Clients (seeded, then learned).
RELATIONSHIP_WORDS = """
MATCH (r:Relationship)
OPTIONAL MATCH (r)-[:PART_OF]->(c:Circle)
RETURN r.name AS name, c.name AS circle
"""

# A new word joins the graph (its circle is only set the first time).
SAVE_RELATIONSHIP = """
MERGE (r:Relationship {name: $name})
WITH r
OPTIONAL MATCH (r)-[:PART_OF]->(had:Circle)
OPTIONAL MATCH (c:Circle {name: $circle})
FOREACH (_ IN CASE WHEN c IS NULL OR had IS NOT NULL THEN [] ELSE [1] END | MERGE (r)-[:PART_OF]->(c))
"""

# "Priya is my manager."
LINK_RELATIONSHIP = """
MATCH (p:Person {id: $person_id}), (r:Relationship {name: $name})
MERGE (p)-[:IS]->(r)
"""

TOPIC_NAMES = "MATCH (t:Topic) RETURN t.name AS name, t.keywords AS keywords"


def rules_for_context(graph, person: str, platform: str, topics: list[str], circle_hint: str | None = None,
                      mode: str = "reply") -> list[dict]:
    result = graph.ro_query(
        RULES_FOR_CONTEXT,
        {"person": person, "platform": platform, "topics": topics,
         "circle_hint": circle_hint, "stacking": STACKING_KINDS, "mode": mode},
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


# Q6: why does this draft sound like that? Each rule it used, who owns the rule, and how many edits or
# posts taught it, narrowest scope first. Written by Maulik, Oct 16. OPTIONAL MATCH keeps a rule that
# nothing taught (count(ev) is then 0; count(*) would wrongly say 1).
WHY_THIS_DRAFT = """
MATCH (d:Draft {id: $draft})-[:USED]->(r:StyleRule)<-[:HAS_RULE]-(s)
OPTIONAL MATCH (r)-[:LEARNED_FROM]->(ev)
RETURN r.text AS rule, labels(s)[0] AS scope, coalesce(s.name, s.id) AS owner, count(ev) AS evidence
ORDER BY CASE scope WHEN 'Person' THEN 1 WHEN 'Circle' THEN 2 WHEN 'Topic' THEN 3 WHEN 'Platform' THEN 4
                    WHEN 'User' THEN 5 ELSE 6 END, evidence DESC
"""


def why_this_draft(graph, draft_id: str) -> list[dict]:
    return rows(graph.ro_query(WHY_THIS_DRAFT, {"draft": draft_id}))


# ---- The handoff trail (Track 02: agents coordinating through the graph) -------------------------
# Every draft keeps a chain of steps: Drafter wrote -> Critic redo (FLAGGED the rule it broke) ->
# Drafter rewrote -> Critic pass -> Learner learned (TAUGHT the new rules). MERGE on the step number,
# so writing a trail twice (live, then when the key replays it) never duplicates it.
SAVE_STEP = """
MATCH (d:Draft {id: $draft_id})
MERGE (d)-[:HAS_STEP]->(s:Step {n: $n})
  ON CREATE SET s.agent = $agent, s.action = $action, s.detail = $detail, s.at = $at, s.ms = $ms,
                s.queries = $queries
WITH d, s
OPTIONAL MATCH (d)-[:HAS_STEP]->(prev:Step {n: $prev})
FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END | MERGE (prev)-[:NEXT]->(s))
"""
STEP_FLAGGED = """
MATCH (:Draft {id: $draft_id})-[:HAS_STEP]->(s:Step {n: $n})
UNWIND $rule_ids AS rid
MATCH (r:StyleRule {id: rid})
MERGE (s)-[:FLAGGED]->(r)
"""
STEP_TAUGHT = """
MATCH (:Draft {id: $draft_id})-[:HAS_STEP]->(s:Step {n: $n})
UNWIND $rule_ids AS rid
MATCH (r:StyleRule {id: rid})
MERGE (s)-[:TAUGHT]->(r)
"""
# The steps as data (to carry them onto the key with the memory).
TRAIL_STEPS = """
MATCH (:Draft {id: $draft_id})-[:HAS_STEP]->(s:Step)
OPTIONAL MATCH (s)-[:FLAGGED]->(f:StyleRule)
OPTIONAL MATCH (s)-[:TAUGHT]->(t:StyleRule)
RETURN s.n AS n, s.agent AS agent, s.action AS action, s.detail AS detail, s.at AS at, s.ms AS ms,
       s.queries AS queries, collect(DISTINCT f.id) AS flagged, collect(DISTINCT t.id) AS taught
ORDER BY n
"""


def save_trail(graph, draft_id: str, steps: list[dict]) -> None:
    for st in steps:
        graph.query(SAVE_STEP, {"draft_id": draft_id, "n": st["n"], "prev": st["n"] - 1,
                                "agent": st["agent"], "action": st["action"],
                                "detail": st.get("detail", ""), "at": st["at"], "ms": st.get("ms", 0),
                                "queries": st.get("queries") or None})
        if st.get("flagged"):
            graph.query(STEP_FLAGGED, {"draft_id": draft_id, "n": st["n"], "rule_ids": st["flagged"]})
        if st.get("taught"):
            graph.query(STEP_TAUGHT, {"draft_id": draft_id, "n": st["n"], "rule_ids": st["taught"]})


def trail_steps(graph, draft_id: str) -> list[dict]:
    return rows(graph.ro_query(TRAIL_STEPS, {"draft_id": draft_id}))


# The people and circles in the plugged-in voice graph (for the demo console's picker).
PEOPLE = """
MATCH (:User)-[:KNOWS]->(p:Person)-[:IN_CIRCLE]->(c:Circle)
OPTIONAL MATCH (d:Draft)-[:TO]->(p) WHERE d.sent_at IS NOT NULL
RETURN p.id AS id, p.name AS name, p.headline AS headline, c.name AS circle, max(d.sent_at) AS last_sent
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
    found = rows(graph.ro_query(GRAPH_SUMMARY))
    return found[0] if found else {"rules": 0, "circles": []}  # e.g. the graph was deleted under a plugged key


# ---- Remembering an edit (the Learner's save step). Maulik's design, Oct 16: three small queries,
# one idea each: "this person is someone I know" -> "save what I changed" -> "remember the rule
# and where it came from".

# 1. Someone new joins the graph (only run for people the graph doesn't know yet).
#    The circle is optional (Oct 16, found by Maulik on a company post): a company page, or someone you
#    "skip", is still remembered; only the platform, topic and your own rules apply to them.
SAVE_PERSON = """
MATCH (u:User)
MERGE (p:Person {id: $person_id})
SET p.name = $name, p.kind = $kind
MERGE (u)-[:KNOWS]->(p)
WITH p
OPTIONAL MATCH (c:Circle {name: $circle})
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | MERGE (p)-[:IN_CIRCLE]->(c))
"""

# 2. Save what I changed: the edit, linked to the draft it corrected (recreated after a re-plug).
SAVE_EDIT = """
MATCH (p:Person {id: $person_id})
OPTIONAL MATCH (pl:Platform {name: $platform})
MERGE (d:Draft {id: $draft_id})
  ON CREATE SET d.text = $before, d.at = $at
SET d.sent_at = $at, d.edited = $before <> $after
MERGE (d)-[:TO]->(p)
FOREACH (_ IN CASE WHEN pl IS NULL THEN [] ELSE [1] END | MERGE (d)-[:ON]->(pl))
CREATE (:Edit {id: $edit_id, before: $before, after: $after, at: $at})-[:OF]->(d)
"""

# 2b. Sent as it was: still a memory. I wrote to this person, and the draft was right as it was.
SAVE_SENT = """
MATCH (p:Person {id: $person_id})
OPTIONAL MATCH (pl:Platform {name: $platform})
MERGE (d:Draft {id: $draft_id})
  ON CREATE SET d.text = $text, d.at = $at
SET d.sent_at = $at, d.edited = false
MERGE (d)-[:TO]->(p)
FOREACH (_ IN CASE WHEN pl IS NULL THEN [] ELSE [1] END | MERGE (d)-[:ON]->(pl))
"""

# Which rules shaped a draft (saved with each memory, so the links come back after a re-plug).
DRAFT_RULES = "MATCH (:Draft {id: $draft_id})-[:USED]->(r:StyleRule) RETURN r.id AS id"
LINK_USED = """
MATCH (d:Draft {id: $draft_id})
UNWIND $rule_ids AS rid
MATCH (r:StyleRule {id: rid})
MERGE (d)-[:USED]->(r)
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

# You moved someone to another circle (Kunal from Work to Community): the old circle's rules stop
# applying and the new one's apply from the next draft. Their own rules and relationship stay with them.
MOVE_TO_CIRCLE = """
MATCH (p:Person {id: $person_id}), (c:Circle {name: $circle})
OPTIONAL MATCH (p)-[old:IN_CIRCLE]->(:Circle)
DELETE old
WITH DISTINCT p, c
MERGE (p)-[:IN_CIRCLE]->(c)
"""


# ---- The dashboard ------------------------------------------------------------------------------
# Q9: your signature style. PageRank over the USED links from drafts to rules: the rules your drafts
# lean on most. (A FalkorDB algorithm, so the ranking comes from the graph's shape, not a counter.)
SIGNATURE_STYLE = """
CALL algo.pageRank(null, 'USED') YIELD node, score
WITH node, score WHERE node:StyleRule AND node.superseded_at IS NULL
RETURN node.id AS id, node.text AS text, score
ORDER BY score DESC LIMIT $limit
"""

# The voice graph for the map: people, circles, platforms, topics, your words for people, active rules,
# and the drafts you sent.
GRAPH_NODES = """
MATCH (n)
WHERE n:User OR n:Person OR n:Circle OR n:Platform OR n:Topic OR n:Relationship
   OR (n:StyleRule AND n.superseded_at IS NULL) OR (n:Draft AND n.sent_at IS NOT NULL)
RETURN ID(n) AS id, labels(n)[0] AS label, coalesce(n.name, n.text, n.id) AS name, n.id AS key
"""
GRAPH_LINKS = """
MATCH (a)-[r]->(b)
WHERE type(r) IN ['KNOWS', 'IN_CIRCLE', 'HAS_RULE', 'IS', 'PART_OF', 'OVERRIDES', 'INTERESTED_IN', 'USED', 'TO']
RETURN ID(a) AS source, ID(b) AS target, type(r) AS type
"""

# Every rule with its owner and evidence (the Writing rules page), including retired ones.
ALL_RULES = """
MATCH (s)-[:HAS_RULE]->(r:StyleRule)
OPTIONAL MATCH (r)-[:LEARNED_FROM]->(ev)
WITH s, r, count(ev) AS evidence
OPTIONAL MATCH (r)-[:OVERRIDES]->(old:StyleRule)
RETURN r.id AS id, r.kind AS kind, r.text AS text, labels(s)[0] AS scope, coalesce(s.name, s.id) AS owner,
       evidence, r.superseded_at IS NOT NULL AS retired, r.id STARTS WITH 'r-' AS learned, collect(old.text) AS replaces
ORDER BY scope, owner, kind
"""

# The people you write to, most written-to first.
PEOPLE_STATS = """
MATCH (:User)-[:KNOWS]->(p:Person)
OPTIONAL MATCH (p)-[:IN_CIRCLE]->(c:Circle)
OPTIONAL MATCH (p)-[:IS]->(rel:Relationship)
OPTIONAL MATCH (d:Draft)-[:TO]->(p) WHERE d.sent_at IS NOT NULL
WITH p, c, rel, count(DISTINCT d) AS sent, max(d.sent_at) AS last_sent
OPTIONAL MATCH (p)-[:HAS_RULE]->(r:StyleRule) WHERE r.superseded_at IS NULL
RETURN p.id AS id, p.name AS name, p.kind AS kind, c.name AS circle, rel.name AS relationship,
       sent, last_sent, count(r) AS own_rules
ORDER BY sent DESC, name
"""

# "It learns": every draft you sent (and the seed's history), whether you changed it, and Critic redos.
DRAFT_HISTORY = """
MATCH (d:Draft)
OPTIONAL MATCH (e:Edit)-[:OF]->(d)
OPTIONAL MATCH (d)-[:HAS_STEP]->(s:Step {action: 'redo'})
WITH d, count(DISTINCT e) AS edits, count(DISTINCT s) AS redos, min(e.at) AS edited_at
WHERE d.sent_at IS NOT NULL OR edits > 0
RETURN d.id AS id, coalesce(d.sent_at, edited_at, d.at) AS at, coalesce(d.edited, edits > 0) AS edited,
       redos, d.sent_at IS NULL AS seeded
ORDER BY at
"""

# The latest drafts with the agents' handoffs.
RECENT_TRAILS = """
MATCH (d:Draft)-[:HAS_STEP]->(s:Step)
OPTIONAL MATCH (d)-[:TO]->(p:Person)
WITH d, p, s ORDER BY s.n
RETURN d.id AS id, d.text AS text, d.at AS at, d.sent_at AS sent_at, p.name AS person,
       collect({agent: s.agent, action: s.action, detail: s.detail, queries: s.queries}) AS steps
ORDER BY at DESC LIMIT $limit
"""

COUNTS = """
MATCH (n) WITH count(n) AS nodes
OPTIONAL MATCH ()-[r]->() RETURN nodes, count(r) AS edges
"""


def signature_style(graph, limit: int = 5) -> list[dict]:
    return rows(graph.ro_query(SIGNATURE_STYLE, {"limit": limit}))


def graph_view(graph) -> dict:
    nodes = rows(graph.ro_query(GRAPH_NODES))
    ids = {n["id"] for n in nodes}
    links = [l for l in rows(graph.ro_query(GRAPH_LINKS)) if l["source"] in ids and l["target"] in ids]
    return {"nodes": nodes, "links": links}


# ---- What the graph ran (shown under a draft's why, so anyone can check it) -----------------------
class Traced:
    """A voice graph that notes every query run on it: its name in this file, how long it took (round
    trip, milliseconds) and how many rows came back. The Drafter's step keeps the list."""

    def __init__(self, graph):
        self.graph, self.calls = graph, []

    def __getattr__(self, name):
        return getattr(self.graph, name)

    def ro_query(self, q, params=None, **kw):
        return self._run(self.graph.ro_query, q, params, kw, write=False)

    def query(self, q, params=None, **kw):
        return self._run(self.graph.query, q, params, kw, write=True)

    def _run(self, run, q, params, kw, write):
        started = time.perf_counter()
        result = run(q, params, **kw)
        self.calls.append({"query": query_name(q), "ms": round((time.perf_counter() - started) * 1000, 1),
                           "rows": len(result.result_set or []), "write": write})
        return result

    def lines(self) -> list[str]:
        """'RULES_FOR_CONTEXT · 6.2 ms · 7 rows' for each query so far."""
        return [f"{c['query']} · {c['ms']} ms · " + ("saved" if c["write"] else f"{c['rows']} row{'' if c['rows'] == 1 else 's'}")
                for c in self.calls]


def query_name(q: str) -> str:
    """The name a query has in this file (RULES_FOR_CONTEXT), or 'inline query'."""
    return next((k for k, v in globals().items() if k.isupper() and v is q), None) \
        or next((k for k, v in globals().items() if k.isupper() and v == q), "inline query")
