"""Bridge API without Claude: key status, people list, and the console page."""
from fastapi.testclient import TestClient

from purrsona.app import app

client = TestClient(app)


def test_people_needs_a_plugged_key_then_lists_people():
    client.post("/key/unplug")
    assert client.get("/people").status_code == 409
    assert client.post("/key/plug", json={"key_id": "demo-maulik"}).status_code == 200
    status = client.get("/key").json()
    assert status["plugged"] is True and status["rules"] == 20 and "Work" in status["circles"]
    people = client.get("/people").json()
    assert {p["id"] for p in people} >= {"priya-nair", "aarav-mehta"}
    assert all(p["circle"] for p in people)
    assert client.post("/key/unplug").json()["forgotten"] is True
    assert client.get("/key").json() == {"plugged": False, "key_id": None}


def test_console_page_is_served():
    page = client.get("/")
    assert page.status_code == 200 and "Purrsona" in page.text
