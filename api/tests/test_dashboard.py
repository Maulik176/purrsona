"""The dashboard's queries and endpoints: the map, the rules page, signature style (PageRank), forgetting."""
from fastapi.testclient import TestClient

from purrsona.app import app
from purrsona.db import rows
from purrsona.queries import ALL_RULES, DRAFT_HISTORY, PEOPLE_STATS, graph_view, record_draft, signature_style

from test_queries import graph  # noqa: F401

client = TestClient(app)


def test_graph_view_has_the_voice_graph_and_no_dangling_links(graph):  # noqa: F811
    view = graph_view(graph)
    labels = {n["label"] for n in view["nodes"]}
    assert {"User", "Person", "Circle", "Platform", "Topic", "StyleRule", "Relationship"} <= labels
    ids = {n["id"] for n in view["nodes"]}
    assert all(l["source"] in ids and l["target"] in ids for l in view["links"])
    assert "w0" not in {n["key"] for n in view["nodes"]}  # superseded rules aren't on the map


def test_rules_page_and_people(graph):  # noqa: F811
    rules = {r["id"]: r for r in rows(graph.ro_query(ALL_RULES))}
    assert rules["w0"]["retired"] and not rules["w1"]["retired"] and rules["w1"]["replaces"] == ["Very formal"]
    assert rules["pa1"]["owner"] == "Aarav Mehta" and rules["pa1"]["evidence"] == 3 and not rules["pa1"]["learned"]
    people = rows(graph.ro_query(PEOPLE_STATS))
    assert {p["id"] for p in people} >= {"priya-nair", "aarav-mehta"} and all(p["sent"] == 0 for p in people)
    history = rows(graph.ro_query(DRAFT_HISTORY))
    assert history and all(h["seeded"] and h["edited"] for h in history)  # the seed's made-up history


def test_signature_style_ranks_the_rules_drafts_use_most(graph):  # noqa: F811
    for i in range(3):
        record_draft(graph, f"d-sig{i}", "Yo bro", 1791400000000 + i, "aarav-mehta", "linkedin", [], ["pa1", "u1"])
    record_draft(graph, "d-sig9", "Hi Priya", 1791400000009, "priya-nair", "linkedin", [], ["w3"])
    top = [r["id"] for r in signature_style(graph, 3)]
    assert set(top[:2]) == {"pa1", "u1"}


def test_dashboard_data_and_forgetting_a_rule_survives_unplugging():
    client.post("/key/unplug")
    assert client.get("/dashboard/data").json() == {"plugged": False}
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    data = client.get("/dashboard/data").json()
    assert data["counts"]["rules"] == 33 and data["people"] and data["rules"]
    assert client.get("/dashboard/graph").json()["nodes"]

    out = client.post("/rules/forget", json={"rule_id": "pa1"}).json()
    assert out["forgot"] == "Calls Aarav 'bro'" and out["memories"] == 1
    client.post("/key/unplug")
    client.post("/key/plug", json={"key_id": "demo-maulik"})  # the key replays the forget
    rules = {r["id"]: r for r in client.get("/dashboard/data").json()["rules"]}
    assert rules["pa1"]["retired"]
    assert client.get("/dashboard/data").json()["memories"][0]["type"] == "forgot"
    client.post("/key/unplug")
