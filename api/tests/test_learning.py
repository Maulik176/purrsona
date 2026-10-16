"""The learning loop without Claude: apply a lesson, outrank rules, survive unplug/re-plug."""
import pytest

from purrsona import keys, learning
from purrsona.db import connect, graph_name
from purrsona.queries import person_context, rules_for_context

from test_queries import graph  # noqa: F401


def lesson_for_kabir(current):
    return learning.build_event(
        "kabir-shah", "Kabir Shah", "Friends", True, "x", "d-test1",
        "Yo that last over was unreal 🔥", "Bhaila that last over was unreal 🔥🏏",
        [{"kind": "phrase_use", "text": "Calls Kabir 'bhaila'"}], current, at=1791500000000)


def test_new_person_learns_a_rule(graph):  # noqa: F811
    assert person_context(graph, "kabir-shah")["known"] is False
    current = rules_for_context(graph, "kabir-shah", "x", [], circle_hint="Friends")
    learning.apply_event(graph, lesson_for_kabir(current))
    assert person_context(graph, "kabir-shah") == {"known": True, "name": "Kabir Shah", "circle": "Friends"}
    own = [r for r in rules_for_context(graph, "kabir-shah", "x", []) if r["scope"] == "Person"]
    assert [r["text"] for r in own] == ["Calls Kabir 'bhaila'"]
    assert own[0]["evidence"] == 1


def test_person_rule_outranks_circle_rule_of_same_kind(graph):  # noqa: F811
    current = rules_for_context(graph, "neha-kulkarni", "linkedin", [])
    event = learning.build_event("neha-kulkarni", "Neha Kulkarni", "Work", False, "linkedin", "d-test2",
                                 "Hi Neha, nice work.", "Hi Neha, nice work 👏",
                                 [{"kind": "emoji", "text": "One 👏 is fine with Neha"}], current)
    assert event["rules"][0]["overrides"] == ["w2"] and event["rules"][0]["supersedes"] == []
    learning.apply_event(graph, event)
    emoji = [r for r in rules_for_context(graph, "neha-kulkarni", "linkedin", []) if r["kind"] == "emoji"][0]
    assert emoji["text"] == "One 👏 is fine with Neha" and emoji["replaces"] == ["No emojis"]


def test_memory_survives_unplug_and_replug(tmp_path, monkeypatch):
    monkeypatch.setenv("PURRSONA_KEYS_DIR", str(tmp_path))
    db = connect()
    keys.plug(db, "test-learn", seed="demo-maulik")
    g = db.select_graph(graph_name("test-learn"))
    event = lesson_for_kabir(rules_for_context(g, "kabir-shah", "x", [], circle_hint="Friends"))
    learning.apply_event(g, event)
    assert keys.append_event("test-learn", event) == 1

    keys.unplug(db, "test-learn")
    assert graph_name("test-learn") not in db.list_graphs()
    info = keys.plug(db, "test-learn", seed="demo-maulik")
    assert info["memories"] == 1
    g = db.select_graph(graph_name("test-learn"))
    assert "Calls Kabir 'bhaila'" in {r["text"] for r in rules_for_context(g, "kabir-shah", "x", [])}

    keys.reset_memories("test-learn")
    keys.plug(db, "test-learn", seed="demo-maulik")
    assert person_context(db.select_graph(graph_name("test-learn")), "kabir-shah")["known"] is False
    keys.unplug(db, "test-learn")
