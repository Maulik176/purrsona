"""Bridge API without Claude: key status, people list, and the console page."""
from fastapi.testclient import TestClient

from purrsona.app import app

client = TestClient(app)


def test_people_needs_a_plugged_key_then_lists_people():
    client.post("/key/unplug")
    assert client.get("/people").status_code == 409
    assert client.post("/key/plug", json={"key_id": "demo-maulik"}).status_code == 200
    status = client.get("/key").json()
    assert status["plugged"] is True and status["rules"] == 33 and "Work" in status["circles"]
    people = client.get("/people").json()
    assert {p["id"] for p in people} >= {"priya-nair", "aarav-mehta"}
    assert all(p["circle"] for p in people)
    assert client.post("/key/unplug").json()["forgotten"] is True
    assert client.get("/key").json() == {"plugged": False, "key_id": None}


def test_console_page_is_served():
    page = client.get("/")
    assert page.status_code == 200 and "Purrsona" in page.text


def test_person_lookup_and_practice_page():
    client.post("/key/unplug")
    assert client.get("/person", params={"id": "priya-nair"}).json() == {"plugged": False, "known": False}
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    who = client.get("/person", params={"id": "priya-nair"}).json()
    assert who["known"] is True and who["circle"] == "Work" and "Friends" in who["circles"]
    assert client.get("/person", params={"id": "nobody-new"}).json()["known"] is False
    client.post("/key/unplug")
    assert "data-author-id" in client.get("/practice/").text
    assert "PurrsonaCat" in client.get("/ext/cat.js").text


def test_learn_passes_your_own_note_to_the_learner(monkeypatch):
    from purrsona import learning
    seen = {}

    def fake_learn(client, draft, sent, name, platform, current, note=None, asks=None):
        seen["note"] = note
        return [{"kind": "phrase_use", "text": "Calls Kabir 'the legend'"}]

    monkeypatch.setattr(learning, "learn_rules", fake_learn)
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    note = "kabir (the legend) starts a new role"
    out = client.post("/learn", json={"draft_id": "d-n1", "draft_text": "Kabir the legend, congrats on the new role!",
                                      "sent_text": "Kabir the legend, congrats on the new role!", "person_id": "kabir-shah",
                                      "person_name": "Kabir Shah", "platform": "linkedin", "circle": "Friends",
                                      "note_text": note}).json()
    assert seen["note"] == note                      # sent unchanged, but your own note still teaches
    assert out["learned"][0]["text"] == "Calls Kabir 'the legend'" and out["person_added"] is True
    client.post("/key/unplug")


def test_new_post_mode_needs_a_note_and_uses_post_rules(monkeypatch):
    seen = {}

    def fake_write(client, post_text, author_name, platform, rules, note=None, mode="reply"):
        seen.update(mode=mode, note=note, rules={r["id"] for r in rules or []})
        return "A post."

    monkeypatch.setattr("purrsona.app.write_draft", fake_write)
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    assert client.post("/draft", json={"platform": "linkedin", "mode": "post"}).status_code == 400
    out = client.post("/draft", json={"platform": "linkedin", "mode": "post",
                                      "user_note": "we went live with ServiceNow today"}).json()
    assert out["mode"] == "voice" and seen["mode"] == "post" and "l2" in seen["rules"] and "t1" in seen["rules"]
    client.post("/key/unplug")


def test_sending_unchanged_still_saves_who_you_wrote_to(monkeypatch):
    monkeypatch.setattr("purrsona.app.claude", lambda: None)  # no Claude call on this path
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    out = client.post("/learn", json={"draft_id": "d-same1", "draft_text": "Nice one!", "sent_text": "Nice one!",
                                      "person_id": "avery-new", "person_name": "Avery New",
                                      "platform": "linkedin", "circle": "Work"}).json()
    assert out["saved"] and out["person_added"] and out["learned"] == [] and "Work" in out["note"]
    who = client.get("/person", params={"id": "avery-new"}).json()
    assert who["known"] and who["circle"] == "Work" and who["sent"] == 1
    client.post("/key/unplug")


def test_refine_brings_the_graph_rules_and_asks_reach_the_learner(monkeypatch):
    from purrsona import learning
    seen = {}

    def fake_refine(client, current, instruction, post_text, author_name, platform, rules, mode="reply"):
        seen.update(instruction=instruction, rules={r["id"] for r in rules or []})
        return "Shorter."

    def fake_learn(client, draft, sent, name, platform, current, note=None, asks=None):
        seen["asks"] = asks
        return []

    monkeypatch.setattr("purrsona.app.refine_draft", fake_refine)
    monkeypatch.setattr(learning, "learn_rules", fake_learn)
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    out = client.post("/refine", json={"current_text": "A long reply.", "instruction": "make it shorter",
                                       "person_id": "priya-nair", "person_name": "Priya Nair", "platform": "linkedin"}).json()
    assert out["draft"] == "Shorter." and out["mode"] == "voice" and "pp1" in seen["rules"]
    client.post("/learn", json={"draft_id": "d-r1", "draft_text": "A long reply.", "sent_text": "Shorter.",
                                "person_id": "priya-nair", "person_name": "Priya Nair", "platform": "linkedin",
                                "asks": ["make it shorter"]})
    assert seen["asks"] == ["make it shorter"]
    client.post("/key/unplug")


def test_a_company_or_skipped_person_is_remembered_without_a_circle(monkeypatch):
    # Found by Maulik on a company's post, Oct 16: "skip" meant the send left no memory at all.
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    out = client.post("/learn", json={"draft_id": "d-co1", "draft_text": "Looks useful!", "sent_text": "Looks useful!",
                                      "person_id": "company-acme-learning", "person_name": "Acme Learning",
                                      "platform": "linkedin"}).json()
    assert out["saved"] and out["person_added"] and out["note"].endswith("I'll remember Acme, no circle.")
    who = client.get("/person", params={"id": "company-acme-learning"}).json()
    assert who["known"] and who["circle"] is None and who["sent"] == 1
    client.post("/key/unplug")


def test_someone_new_in_your_own_words(monkeypatch):
    seen = {}

    def fake_write(client, post_text, author_name, platform, rules, note=None, mode="reply"):
        seen["to"] = author_name
        return "Hi Avery, congrats on the launch."

    monkeypatch.setattr("purrsona.app.write_draft", fake_write)
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    rel = client.post("/relationship", json={"text": "My skip-level"}).json()
    assert rel == {"name": "skip-level", "circle": "Work", "source": "graph"}
    d = client.post("/draft", json={"person_id": "avery-boss", "person_name": "Avery Boss", "platform": "linkedin",
                                    "post_text": "We launched!", "circle_hint": "Work", "relationship": "skip-level"}).json()
    assert seen["to"] == "Avery Boss (your skip-level)" and d["person"]["circle"] == "Work"
    out = client.post("/learn", json={"draft_id": d["draft_id"], "draft_text": d["draft"], "sent_text": d["draft"],
                                      "person_id": "avery-boss", "person_name": "Avery Boss", "platform": "linkedin",
                                      "circle": "Work", "relationship": "skip-level"}).json()
    assert out["note"].endswith("Avery (skip-level) is now in your Work circle.")
    who = client.get("/person", params={"id": "avery-boss"}).json()
    assert (who["relationship"], who["circle"]) == ("skip-level", "Work")
    client.post("/key/unplug")
