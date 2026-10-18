"""Physical-key app path against FalkorDB, with a byte-accurate USB stand-in."""
import pytest
import time
from fastapi.testclient import TestClient

from purrsona import app as app_module
from purrsona.db import connect, graph_name
from purrsona.key_bridge import MemoryBridge, display_graph
from purrsona.key_protocol import ProtocolError

from test_usb_key import usb_fixture  # noqa: F401


@pytest.fixture
def bridge(usb_fixture):
    storage, trust, factory = usb_fixture
    bridge = MemoryBridge(connect(), usb_enabled=True, trust=trust, ports=lambda: ["test-port"], client_factory=factory)
    yield bridge
    bridge.disconnect("test_finished")
    bridge.cleanup()


def test_usb_attach_save_unplug_and_replug_restore_real_graph(bridge):
    bridge.attach("test-port")
    assert bridge.state["key_id"] == "demo-maulik"
    graph = bridge.graph()
    assert bridge.save_event(graph, {"type": "forgot", "at": 123, "rule_id": "pa1"}) == 1
    assert bridge.events("demo-maulik")[0]["rule_id"] == "pa1"
    assert bridge.unplug()["forgotten"]
    assert graph_name("demo-maulik") not in bridge.db.list_graphs()
    bridge.attach("test-port")
    result = bridge.graph().ro_query("MATCH (r:StyleRule {id:'pa1'}) RETURN r.superseded_at")
    assert result.result_set == [[123]]
    assert len(bridge.events("demo-maulik")) == 1


def test_old_graph_lease_cannot_recreate_graph_after_disconnect(bridge):
    bridge.attach("test-port")
    old = bridge.graph()
    bridge.disconnect("cable_removed")
    bridge.cleanup()
    with pytest.raises(ProtocolError, match="discarded"):
        old.query("CREATE (:ShouldNeverExist)")
    bridge.attach("test-port")
    with pytest.raises(ProtocolError):
        old.query("CREATE (:ShouldNeverExist)")
    assert bridge.graph().ro_query("MATCH (n:ShouldNeverExist) RETURN count(n)").result_set == [[0]]


def test_late_draft_is_discarded_after_unplug(bridge, monkeypatch):
    bridge.attach("test-port")
    monkeypatch.setattr(app_module, "memory", bridge)
    monkeypatch.setattr(app_module, "state", bridge.state)
    monkeypatch.setattr(app_module, "claude", lambda: None)

    def unplug_during_draft(*args, **kwargs):
        bridge.disconnect("cable_removed")
        bridge.cleanup()
        return "This personalized answer must not be returned."

    monkeypatch.setattr(app_module, "write_draft", unplug_during_draft)
    result = TestClient(app_module.app).post("/draft", json={"person_id": "priya-nair", "person_name": "Priya",
                                                            "platform": "linkedin", "post_text": "We launched"})
    assert result.status_code == 409 and "personalized answer" not in result.text
    assert graph_name("demo-maulik") not in bridge.db.list_graphs()


def test_save_failure_never_changes_live_graph_or_claims_saved(bridge, monkeypatch):
    bridge.attach("test-port")
    graph = bridge.graph()

    def cannot_save(*args):
        raise OSError("USB disconnected")

    monkeypatch.setattr(bridge.client, "append", cannot_save)
    with pytest.raises(ProtocolError, match="Save interrupted"):
        bridge.save_event(graph, {"type": "forgot", "at": 123, "rule_id": "pa1"})
    assert bridge.state["key_id"] is None
    bridge.cleanup()
    bridge.attach("test-port")
    assert bridge.events("demo-maulik") == []
    assert bridge.graph().ro_query("MATCH (r:StyleRule {id:'pa1'}) RETURN r.superseded_at").result_set == [[None]]


def test_display_payload_has_stable_ids_caps_and_no_dangling_edges():
    nodes = [{"id": n, "label": "Person", "key": f"p-{n}", "name": f"Person {n}"} for n in range(80)]
    links = [{"source": n, "target": m, "type": "KNOWS"} for n in range(80) for m in range(n + 1, 80)]
    packet = display_graph({"nodes": nodes, "links": links}, 1)
    assert packet["truncated"] and len(packet["nodes"]) == 64 and len(packet["edges"]) == 128
    ids = {node["id"] for node in packet["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in packet["edges"])
    for node in nodes:
        node["id"] += 1000
    for edge in links:
        edge["source"] += 1000; edge["target"] += 1000
    assert display_graph({"nodes": nodes, "links": links}, 1) == packet


def test_overlapping_activities_keep_learning_state_until_save_finishes(bridge):
    bridge.attach("test-port")
    graph = bridge.graph()
    with bridge.activity(graph, "learning"):
        with bridge.activity(graph, "drafting"):
            assert bridge.screen == "learning"
        assert bridge.screen == "learning"
    assert bridge.screen == "ready"


def test_old_connection_error_does_not_disconnect_a_new_key(bridge):
    bridge.attach("test-port")
    old_client = bridge.client
    bridge.unplug()
    bridge.attach("test-port")
    bridge.disconnect("stale_error", expected_client=old_client)
    assert bridge.state["key_id"] == "demo-maulik"


def test_monitor_detects_cable_loss_and_cleans_graph(bridge, monkeypatch):
    connected = ["test-port"]
    bridge.ports = lambda: list(connected)
    bridge.start()
    try:
        deadline = time.monotonic() + 5
        while bridge.state["key_id"] is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert bridge.state["key_id"] == "demo-maulik"
        started = time.monotonic()
        connected.clear()
        while (bridge.state["key_id"] is not None or bridge.cleanup_keys) and time.monotonic() - started < 2:
            time.sleep(0.02)
        assert bridge.state["key_id"] is None
        assert graph_name("demo-maulik") not in bridge.db.list_graphs()
        assert time.monotonic() - started < 1.5
        assert bridge.live.current["phase"] == "forgotten"
        monkeypatch.setattr(app_module, "memory", bridge)
        monkeypatch.setattr(app_module, "state", bridge.state)
        monkeypatch.setattr(app_module, "claude", lambda: None)
        seen_rules = []

        def generic_draft(*args, **kwargs):
            seen_rules.append(kwargs["rules"])
            return "Congratulations on the launch."

        monkeypatch.setattr(app_module, "write_draft", generic_draft)
        response = TestClient(app_module.app).post("/draft", json={"person_id": "priya-nair",
            "person_name": "Priya", "platform": "linkedin", "post_text": "We launched"})
        assert response.status_code == 200
        assert response.json()["mode"] == "generic" and response.json()["rules"] == []
        assert seen_rules == [None]
        assert graph_name("demo-maulik") not in bridge.db.list_graphs()
    finally:
        bridge.stop()


def test_unpaired_key_is_not_automatically_trusted(bridge, usb_fixture, tmp_path):
    from purrsona.usb_key import TrustStore
    bridge.trust = TrustStore(tmp_path / "empty-trust")
    bridge.attach("test-port")
    assert bridge.usb_status == "needs_pairing"
    assert bridge.client is None and bridge.state["key_id"] is None
    assert bridge.trust.load("demo-maulik") is None
