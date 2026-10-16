"""Q6, "why this draft" (Maulik's query): the rules a draft used, their owners and their evidence."""
from purrsona.queries import record_draft, why_this_draft

from test_queries import graph  # noqa: F401  (the fixture graph from the demo seed)


def test_why_lists_rules_narrowest_first_and_keeps_a_rule_nothing_taught(graph):  # noqa: F811
    # A rule with no LEARNED_FROM evidence: with MATCH instead of OPTIONAL MATCH it would vanish.
    graph.query("MATCH (c:Circle {name: 'Friends'}) CREATE (c)-[:HAS_RULE]->(:StyleRule {id: 'tq6', kind: 'format', text: 'Test rule nobody taught'})")
    record_draft(graph, "d-why1", "Yo bro", 1791400000000, "aarav-mehta", "linkedin", [], ["u1", "f1", "pa1", "tq6"])
    why = why_this_draft(graph, "d-why1")
    assert [w["scope"] for w in why] == ["Person", "Circle", "Circle", "User"]
    assert why[0]["owner"] == "Aarav Mehta" and why[0]["evidence"] == 3       # "Calls Aarav 'bro'", 3 edits
    assert {"rule": "Test rule nobody taught", "scope": "Circle", "owner": "Friends", "evidence": 0} in why


def test_why_for_a_draft_that_does_not_exist_is_empty(graph):  # noqa: F811
    assert why_this_draft(graph, "d-nope") == []
