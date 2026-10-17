"""The learning loop without Claude: apply a lesson, outrank rules, survive unplug/re-plug."""
import pytest

from purrsona import keys, learning
from purrsona.db import connect, graph_name, rows
from purrsona.queries import DRAFT_RULES, person_context, record_draft, rules_for_context

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
    who = person_context(graph, "kabir-shah")
    assert (who["known"], who["name"], who["circle"], who["sent"]) == (True, "Kabir Shah", "Friends", 1)
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


def test_sent_as_is_still_remembers_a_new_person(tmp_path, monkeypatch):
    # Found by Maulik on LinkedIn, Oct 16: a new person's draft sent unchanged left no memory at all.
    monkeypatch.setenv("PURRSONA_KEYS_DIR", str(tmp_path))
    db = connect()
    keys.plug(db, "test-sent", seed="demo-maulik")
    g = db.select_graph(graph_name("test-sent"))
    used = [r["id"] for r in rules_for_context(g, "avery-new", "linkedin", [], circle_hint="Work")]
    record_draft(g, "d-sent1", "Great point, Avery.", 1, "avery-new", "linkedin", [], used)
    event = learning.build_sent_event("avery-new", "Avery New", "Work", True, "linkedin",
                                      "d-sent1", "Great point, Avery.", used)
    event["person"]["relationship"] = "mentor"
    learning.apply_event(g, event)
    keys.append_event("test-sent", event)

    keys.unplug(db, "test-sent")
    keys.plug(db, "test-sent", seed="demo-maulik")
    g = db.select_graph(graph_name("test-sent"))
    who = person_context(g, "avery-new")
    assert (who["known"], who["circle"], who["relationship"], who["sent"]) == (True, "Work", "mentor", 1)
    assert {r["id"] for r in rows(g.ro_query(DRAFT_RULES, {"draft_id": "d-sent1"}))} == set(used)
    keys.unplug(db, "test-sent")


def test_a_habit_about_how_a_message_opens_is_a_greeting():
    # Found Oct 17: "Addresses Kunal by his X handle instead of his first name" came back as phrase_use,
    # so Work's "Hi <first name>" still applied and the Critic sent the habit back.
    from purrsona.learning import kind_for
    assert kind_for("phrase_use", "Addresses Kunal by his X handle '@kunalstwt' instead of his first name") == "greeting"
    assert kind_for("phrase_use", "Opens with 'Bhaila'") == "greeting"
    assert kind_for("phrase_use", "Calls Aarav 'bro'") == "phrase_use"   # a nickname inside the message stacks
    assert kind_for("tone", "Opens with a joke") == "tone"                # only the stacking kinds are re-sorted
