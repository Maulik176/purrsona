"""The hosted demo: every visitor (browser) gets their own virtual key and graph. No Claude calls here."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("PURRSONA_HOSTED", "1")
    monkeypatch.setenv("PURRSONA_CALLS_PER_HOUR", "2")
    from purrsona import app as bridge, visitors
    importlib.reload(visitors)
    importlib.reload(bridge)
    yield bridge
    monkeypatch.delenv("PURRSONA_HOSTED")
    monkeypatch.delenv("PURRSONA_CALLS_PER_HOUR")
    importlib.reload(visitors)
    importlib.reload(bridge)


def test_two_visitors_get_their_own_keys_and_graphs(hosted):
    judge_a, judge_b = TestClient(hosted.app), TestClient(hosted.app)
    assert judge_a.get("/key").json()["plugged"] is False
    a = judge_a.post("/key/plug", json={"key_id": "demo-maulik"}).json()
    assert a["key_id"].startswith("v-") and a["graph"] == f"voice:{a['key_id']}"
    assert judge_b.get("/key").json()["plugged"] is False  # A plugging in didn't plug anyone else in

    b = judge_b.post("/key/plug", json={"seed": "fresh"}).json()  # a new, empty voice
    assert b["key_id"] != a["key_id"]
    assert judge_b.get("/people").json() == []
    assert {p["id"] for p in judge_a.get("/people").json()} >= {"priya-nair", "neha-kulkarni"}

    judge_a.post("/person/circle", json={"person_id": "neha-kulkarni", "circle": "Friends"})
    assert judge_a.get("/person", params={"id": "neha-kulkarni"}).json()["circle"] == "Friends"
    assert judge_b.get("/person", params={"id": "neha-kulkarni"}).json()["known"] is False
    assert judge_b.get("/dashboard/data").json()["user"] == "You"

    assert judge_a.post("/key/unplug").json()["forgotten"] is True  # A's graph is gone...
    assert judge_b.get("/key").json()["plugged"] is True             # ...and B's isn't
    judge_b.post("/key/unplug")


def test_a_visitor_cannot_pick_someone_elses_key_or_a_strange_seed(hosted):
    judge = TestClient(hosted.app)
    mine = judge.post("/key/plug", json={"key_id": "v-someoneelse1"}).json()["key_id"]
    assert mine != "v-someoneelse1"  # online, the key is always the visitor's own
    assert judge.post("/key/plug", json={"seed": "../../etc/passwd"}).status_code == 400
    judge.post("/key/unplug")


def test_each_visitor_has_an_hourly_limit_on_claude_calls(hosted):
    judge, other = TestClient(hosted.app), TestClient(hosted.app)
    judge.post("/key/plug", json={})
    for _ in range(2):  # "manager" is answered by the graph, but it still counts against the limit
        assert judge.post("/relationship", json={"text": "my manager"}).status_code == 200
    assert judge.post("/relationship", json={"text": "my manager"}).status_code == 429
    assert other.post("/relationship", json={"text": "my manager"}).status_code == 200  # others aren't affected
    judge.post("/key/unplug")
