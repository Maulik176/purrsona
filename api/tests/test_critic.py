"""The Critic and the handoff trail: Drafter -> Critic -> (one redo) -> Critic -> Learner, saved in the graph.

Maulik's three checks are swapped for tiny stand-ins here, so these tests don't depend on them.
"""
import pytest
from fastapi.testclient import TestClient

from purrsona import critic, critic_checks
from purrsona.app import app

client = TestClient(app)
WORK = [
    {"id": "w2", "kind": "emoji", "text": "No emojis", "scope": "Circle", "scope_name": "Work"},
    {"id": "w3", "kind": "greeting", "text": "Opens with 'Hi <first name>'", "scope": "Circle", "scope_name": "Work"},
    {"id": "w1", "kind": "tone", "text": "Warm but professional", "scope": "Circle", "scope_name": "Work"},
    {"id": "x2", "kind": "format", "text": "No hashtags", "scope": "Platform", "scope_name": "x"},
]


@pytest.fixture(autouse=True)
def stand_in_checks(monkeypatch):
    monkeypatch.setattr(critic_checks, "count_emojis", lambda t: t.count("🎉"))
    monkeypatch.setattr(critic_checks, "count_sentences", lambda t: len([s for s in t.split(".") if s.strip()]))
    monkeypatch.setattr(critic_checks, "banned_phrase_used", lambda d, r: None)


def test_review_names_each_broken_rule_and_its_owner():
    out = critic.review("Hello Neha 🎉 #win", WORK, "Neha Kulkarni")
    assert {p["id"] for p in out["problems"]} == {"w2", "w3", "x2"}
    assert out["checked"] == 3  # the tone rule isn't measured in code
    assert "broke Work's 'No emojis' (it has 1 emoji, the rule allows 0)" in critic.describe(out["problems"])
    assert critic.review("Hi Neha, great work.", WORK, "Neha Kulkarni")["problems"] == []


def test_your_own_words_win_over_the_rules():
    out = critic.review("Hi Neha, congrats 🎉", WORK, "Neha Kulkarni", note="congrats neha 🎉")
    assert out["problems"] == []


def plug_with_fakes(monkeypatch, rewrite):
    calls = []
    monkeypatch.setattr("purrsona.app.claude", lambda: None)
    monkeypatch.setattr("purrsona.app.write_draft", lambda *a, **k: "Hi Neha, great work 🎉")

    def fake_refine(client_, current, instruction, *a, **k):
        calls.append(instruction)
        return rewrite
    monkeypatch.setattr("purrsona.app.refine_draft", fake_refine)
    client.post("/key/plug", json={"key_id": "demo-maulik"})
    return calls


def test_critic_sends_it_back_once_and_the_trail_survives_unplugging(monkeypatch):
    calls = plug_with_fakes(monkeypatch, "Hi Neha, great work.")
    d = client.post("/draft", json={"person_id": "neha-kulkarni", "person_name": "Neha Kulkarni",
                                    "platform": "linkedin", "post_text": "We shipped it!"}).json()
    assert d["draft"] == "Hi Neha, great work." and len(calls) == 1 and "No emojis" in calls[0]
    assert [(s["agent"], s["action"]) for s in d["trail"]] == [
        ("Drafter", "wrote"), ("Critic", "redo"), ("Drafter", "rewrote"), ("Critic", "pass")]
    assert [p["id"] for p in d["critic"]["fixed"]] == ["w2"]
    trail = client.get(f"/trail/{d['draft_id']}").json()["steps"]
    assert trail[1]["flagged"] == ["w2"]

    client.post("/learn", json={"draft_id": d["draft_id"], "draft_text": d["draft"], "sent_text": d["draft"],
                                "person_id": "neha-kulkarni", "person_name": "Neha Kulkarni", "platform": "linkedin"})
    client.post("/key/unplug")
    client.post("/key/plug", json={"key_id": "demo-maulik"})  # replays the memory, trail included
    replayed = client.get(f"/trail/{d['draft_id']}").json()["steps"]
    assert [(s["agent"], s["action"]) for s in replayed][-1] == ("Learner", "sent as-is")
    assert len(replayed) == 5 and replayed[1]["flagged"] == ["w2"]
    client.post("/key/unplug")


def test_never_more_than_one_redo(monkeypatch):
    calls = plug_with_fakes(monkeypatch, "Hi Neha, still 🎉")
    d = client.post("/draft", json={"person_id": "neha-kulkarni", "person_name": "Neha Kulkarni",
                                    "platform": "linkedin", "post_text": "We shipped it!"}).json()
    assert len(calls) == 1
    assert d["trail"][-1]["action"] == "still breaks" and [p["id"] for p in d["critic"]["still"]] == ["w2"]
    client.post("/key/unplug")
