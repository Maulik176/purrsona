"""Virtual key (plug = build, unplug = forget) and draft recording. No Claude calls here."""
from purrsona import keys
from purrsona.db import connect, graph_name, rows
from purrsona.queries import record_draft, rules_for_context

from test_queries import graph  # noqa: F401  (reuse the fixture graph)


def test_plug_builds_graph_and_unplug_forgets():
    db = connect()
    info = keys.plug(db, "demo-maulik")
    assert info["nodes"] > 40 and info["edges"] > 60
    assert graph_name("demo-maulik") in db.list_graphs()
    gone = keys.unplug(db, "demo-maulik")
    assert gone["forgotten"] is True
    assert graph_name("demo-maulik") not in db.list_graphs()


def test_record_draft_links_used_rules(graph):  # noqa: F811
    rules = rules_for_context(graph, "priya-nair", "linkedin", ["ServiceNow"])
    ids = [r["id"] for r in rules]
    record_draft(graph, "d-test", "Hi Priya, congrats 🎉", 1791400000000, "priya-nair", "linkedin",
                 ["ServiceNow"], ids)
    used = rows(graph.ro_query(
        "MATCH (d:Draft {id: 'd-test'})-[:USED]->(r:StyleRule) RETURN collect(r.id) AS ids"))[0]["ids"]
    assert sorted(used) == sorted(ids)
    to = rows(graph.ro_query("MATCH (d:Draft {id: 'd-test'})-[:TO]->(p:Person) RETURN p.id AS id"))
    assert to == [{"id": "priya-nair"}]


def test_record_draft_for_unknown_person_has_no_to_edge(graph):  # noqa: F811
    record_draft(graph, "d-new", "Hello", 1791400000000, "kabir-shah", "linkedin", [], [])
    to = rows(graph.ro_query("MATCH (d:Draft {id: 'd-new'})-[:TO]->(p) RETURN count(p) AS n"))
    assert to[0]["n"] == 0
