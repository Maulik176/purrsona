"""What the graph ran: every draft lists its queries (name, ms, rows), and the list survives unplugging."""
from fastapi.testclient import TestClient

from purrsona import queries
from purrsona.app import app

client = TestClient(app)


def test_a_query_is_named_after_its_constant():
    assert queries.query_name(queries.RULES_FOR_CONTEXT) == "RULES_FOR_CONTEXT"
    assert queries.query_name("MATCH (n) RETURN n") == "inline query"


def test_a_draft_lists_the_queries_behind_it_and_the_key_keeps_them(monkeypatch):
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    monkeypatch.setattr("purrsona.app.write_draft", lambda *a, **k: "Hi Neha, great work.")
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    d = client.post("/draft", json={"person_id": "neha-kulkarni", "person_name": "Neha Kulkarni",
                                    "platform": "linkedin", "post_text": "We shipped it!"}).json()
    ran = {q["query"]: q for q in d["queries"]}
    assert ran["RULES_FOR_CONTEXT"]["rows"] > 0 and not ran["RULES_FOR_CONTEXT"]["write"]
    assert ran["PERSON_CONTEXT"]["rows"] == 1 and ran["RECORD_DRAFT"]["write"]
    # The Drafter's step keeps what the graph looked up before Claude wrote (not the writes after).
    looked_up = client.get(f"/trail/{d['draft_id']}").json()["steps"][0]["queries"]
    assert any(line.startswith("RULES_FOR_CONTEXT · ") and line.endswith(" rows") for line in looked_up)
    assert not any("RECORD_DRAFT" in line for line in looked_up)

    client.post("/learn", json={"draft_id": d["draft_id"], "draft_text": d["draft"], "sent_text": d["draft"],
                                "person_id": "neha-kulkarni", "person_name": "Neha Kulkarni", "platform": "linkedin"})
    client.post("/key/unplug")
    client.post("/key/plug", json={"key_id": "demo-maulik"})  # replays the memory, trail included
    assert client.get(f"/trail/{d['draft_id']}").json()["steps"][0]["queries"] == looked_up
    client.post("/key/unplug")
