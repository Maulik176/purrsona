"""Core query tests against a fixture graph built from the demo snapshot.

Needs the project FalkorDB running: `docker compose up -d` in the repo root (port 6380).
"""
from pathlib import Path

import pytest

from purrsona.db import connect
from purrsona.queries import person_context, rules_for_context, topics_in
from purrsona.snapshot import build_graph, load_snapshot_file

SEED = Path(__file__).resolve().parents[2] / "data" / "seed" / "demo-maulik.json"


@pytest.fixture(scope="module")
def graph():
    db = connect()
    g = db.select_graph("test:voice")
    try:
        g.delete()
    except Exception:
        pass
    build_graph(g, load_snapshot_file(SEED))
    yield g
    g.delete()


def by_kind(rules):
    out = {}
    for r in rules:
        out.setdefault(r["kind"], []).append(r)
    return out


def test_friend_on_x_about_cricket(graph):
    rules = by_kind(rules_for_context(graph, "aarav-mehta", "x", ["cricket"]))
    assert rules["tone"][0]["text"] == "Casual and playful"
    assert rules["tone"][0]["scope"] == "Circle"
    # X's length rule (platform) beats my global one (user).
    assert rules["length"][0]["id"] == "x1"
    phrase_ids = {r["id"] for r in rules["phrase_use"]}
    assert {"pa1", "t2"} <= phrase_ids          # "bro" for Aarav + a cricket reference
    assert rules["emoji"][0]["evidence"] == 2   # learned from two edits


def test_person_rule_beats_circle_rule(graph):
    rules = by_kind(rules_for_context(graph, "priya-nair", "linkedin", []))
    emoji = rules["emoji"][0]
    assert emoji["id"] == "pp1" and emoji["scope"] == "Person"
    assert "No emojis" in emoji["replaces"]


def test_new_person_inherits_from_circle(graph):
    # Q5: someone not in the graph yet, placed in Work.
    rules = by_kind(rules_for_context(graph, "kabir-shah", "linkedin", ["ServiceNow"], circle_hint="Work"))
    assert rules["tone"][0]["id"] == "w1"
    assert rules["emoji"][0]["text"] == "No emojis"
    assert "t1" in {r["id"] for r in rules["phrase_use"]}
    assert all(r["scope"] != "Person" for kind in rules.values() for r in kind)


def test_nothing_known_still_returns_my_global_habits(graph):
    # Nothing-found case: unknown person, no circle, a platform the graph doesn't know.
    rules = rules_for_context(graph, "nobody", "reddit", [])
    assert {r["id"] for r in rules} == {"u1", "u2"}


def test_superseded_rule_is_never_applied(graph):
    rules = rules_for_context(graph, "priya-nair", "linkedin", [])
    assert "w0" not in {r["id"] for r in rules}


def test_person_context_and_topics(graph):
    assert person_context(graph, "neha-kulkarni") == {"known": True, "name": "Neha Kulkarni", "circle": "Work"}
    assert person_context(graph, "kabir-shah")["known"] is False
    assert set(topics_in(graph, "Big ServiceNow go-live today, AI everywhere")) == {"ServiceNow", "AI"}


def test_topics_match_whole_words_and_keywords(graph):
    # Found in Maulik's Kabir test: "bhaila" contains "ai" but isn't about AI.
    assert topics_in(graph, "Hey kabir bhaila!!! What a last over yesterday!") == ["cricket"]
    # Found in the Neha test: Flow Designer is a ServiceNow topic even without the word ServiceNow.
    assert topics_in(graph, "Just automated our approval flow with Flow Designer.") == ["ServiceNow"]
    assert topics_in(graph, "Lunch was great today") == []


def test_circle_hint_matching_own_circle_does_not_double_count(graph):
    # Regression: Work reached twice (own circle + hint) doubled the evidence counts.
    rules = by_kind(rules_for_context(graph, "priya-nair", "linkedin", [], circle_hint="Work"))
    assert rules["greeting"][0]["id"] == "w3"
    assert rules["greeting"][0]["evidence"] == 2


def test_own_post_rules_only_join_own_posts(graph):
    # Gap found Oct 15: "on own posts, end with a question" was also shaping replies.
    reply = {r["id"] for r in rules_for_context(graph, "priya-nair", "linkedin", [])}
    post = {r["id"] for r in rules_for_context(graph, "", "linkedin", [], mode="post")}
    assert "l2" not in reply and "l2" in post
    assert "w2" not in post  # nobody to reply to: no person or circle rules on your own post
