"""Run the firmware display and app together, with only physical I/O and AI mocked."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from purrsona.board_simulator import create_demo
from purrsona.key_protocol import ProtocolError
from purrsona.simulated_board import PORT, SimulatedBoard, TEST_PUBLIC_KEY, screen_png
from purrsona.usb_key import TrustStore
from test_protocol import native_binary  # noqa: F401


def wait_until(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("Simulator did not reach the expected state")


@pytest.fixture
def board(native_binary, tmp_path):
    board = SimulatedBoard(native_binary, tmp_path / "board", realtime=False)
    yield board
    board.close()


def unlock(board, trust):
    board.connect()
    key = board.client(PORT)
    key.hello()
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(key.unlock, trust)
        wait_until(lambda: board.frame()["screen"] == "unlock")
        board.command("!button-b")
        pending.result(timeout=3)
    key.pull()
    return key


@pytest.fixture
def trust(tmp_path):
    trust = TrustStore(tmp_path / "trust")
    trust.save({"key_id": "demo-maulik", "pk": TEST_PUBLIC_KEY, "last_counter": 0})
    return trust


def words(board):
    return [text["text"] for text in board.frame()["texts"]]


def advance(board, key, amount):
    key.ack("STATUS", state="ready", memories=0)
    board.command(f"!tick {amount}")


def test_actual_display_animation_graph_and_both_buttons(board, trust):
    locked = board.frame()
    assert "Waiting for your laptop" in words(board)
    key = unlock(board, trust)
    board.command("!tick 80")
    loading = board.frame()
    assert "Loading your memories" in [t["text"] for t in loading["texts"]]
    assert loading["pixels"] != locked["pixels"]
    nodes = [{"id": f"Person:p{n}", "kind": "Person", "label": f"Person {n}", "detail": f"Details for person {n}"} for n in range(9)]
    key.ack("GRAPH_VIEW", revision=1, nodes=nodes, edges=[], truncated=False)
    advance(board, key, 80)
    assert "Memory connected" in words(board)
    advance(board, key, 1900)
    assert "A Next   B Open   Hold A Back" in words(board)
    for _ in range(7):
        board.command("!button-a")
    advance(board, key, 80)
    assert "2/2" in words(board)
    board.command("!button-b")
    advance(board, key, 80)
    assert "Person 7" in words(board)
    board.command("!button-b")
    advance(board, key, 80)
    assert "Details for person 7" in words(board)
    board.command("!hold-a")
    advance(board, key, 80)
    assert "A Next   B Open   Hold A Back" in words(board)
    board.disconnect(); board.command("!tick 80")
    assert "Waiting for your laptop" in words(board)
    assert board.frame()["nodes"] == 0
    assert screen_png(locked).startswith(b"\x89PNG")


def test_ready_graph_still_opens_if_drafting_starts_during_animation(board, trust):
    key = unlock(board, trust)
    key.ack("GRAPH_VIEW", revision=1, nodes=[{"id": "u", "kind": "User", "label": "Demo"}], edges=[], truncated=False)
    advance(board, key, 80)
    key.ack("STATUS", state="drafting")
    board.command("!tick 1900")
    assert "DRAFTING" in words(board)
    assert "Waiting for your laptop" not in words(board)


def test_lcd_text_stays_in_bounds_for_long_valid_laptop_name(board):
    board.connect()
    board.send(b'{"v":1,"type":"PAIR_BEGIN","id":"p","laptop_id":"' + b'W' * 64 + b'"}\n')
    board.command("!button-b"); board.command("!tick 80")
    # Unlock names can be supplied by any paired host, not only our short laptop name.
    board.disconnect(); board.connect()
    from purrsona.key_protocol import UnlockChallenge, encode
    challenge = UnlockChallenge("demo-maulik", "W" * 64, "long-name")
    board.send(encode(challenge.request())); board.command("!tick 80")
    frame = board.frame()
    assert frame["screen"] == "unlock"
    assert all(0 <= text["x"] <= 320 - text["width"] and 0 <= text["y"] <= 170 - text["height"] for text in frame["texts"])


def test_saved_event_and_counter_survive_native_process_restart(board, trust):
    key = unlock(board, trust)
    event = {"type": "forgot", "at": 12, "rule_id": "pa1"}
    assert key.append(event, "persisted") == 1
    key.close(); board.power_cycle()
    key = unlock(board, trust)
    assert trust.load("demo-maulik")["last_counter"] == 2
    assert key.pull()[1] == [{"event_id": "persisted", "event": event}]
    assert key.append(event, "persisted") == 1
    key.close()


@pytest.fixture
def demo(native_binary, tmp_path):
    app = create_demo(native_binary, tmp_path)
    with TestClient(app) as client:
        yield client, app.state.board, app.state.bridge


def connect_demo(client):
    client.post("/sim/command", json={"command": "connect"}).raise_for_status()
    wait_until(lambda: client.get("/sim/state").json()["screen"] == "unlock")
    client.post("/sim/command", json={"command": "b"}).raise_for_status()
    wait_until(lambda: client.get("/key").json()["plugged"])


def test_full_app_draft_learn_and_power_cycle_with_cpp_board(demo):
    client, board, bridge = demo
    assert client.get("/sim/").status_code == 200
    assert client.get("/practice/").status_code == 200
    connect_demo(client)
    draft = client.post("/draft", json={"person_id": "priya-nair", "person_name": "Priya Nair", "platform": "linkedin", "post_text": "We launched"})
    assert draft.status_code == 200 and draft.json()["mode"] == "voice"
    assert any(query["query"] == "RULES_FOR_CONTEXT" and query["rows"] > 0 for query in draft.json()["queries"])
    traced_queries = draft.json()["trail"][0]["queries"]
    assert any(line.startswith("RULES_FOR_CONTEXT · ") for line in traced_queries)
    saved = client.post("/learn", json={"draft_id": draft.json()["draft_id"], "draft_text": draft.json()["draft"],
                       "sent_text": "Congrats Priya!", "person_id": "priya-nair", "person_name": "Priya Nair", "platform": "linkedin"})
    assert saved.status_code == 200 and saved.json()["saved"]
    assert saved.json()["learned"][0]["text"] == "Keeps replies to Priya short."
    assert board.frame()["events"] == 1
    client.post("/sim/command", json={"command": "restart"}).raise_for_status()
    assert not client.get("/key").json()["plugged"]
    assert not bridge.db.list_graphs()
    connect_demo(client)
    data = client.get("/dashboard/data").json()
    assert data["counts"]["memories"] == 1 and data["counts"]["learned"] == 1
    assert data["memories"][0]["rules"] == ["Keeps replies to Priya short."]
    restored_trail = client.get(f"/trail/{draft.json()['draft_id']}").json()["steps"]
    assert restored_trail[0]["queries"] == traced_queries


def test_lost_save_ack_returns_failure_but_reconnect_recovers_one_event(demo):
    client, board, bridge = demo
    connect_demo(client)
    client.post("/sim/command", json={"command": "lose_ack"}).raise_for_status()
    result = client.post("/rules/forget", json={"rule_id": "pa1"})
    assert result.status_code == 409 and "Save interrupted" in result.json()["detail"]
    client.post("/sim/command", json={"command": "restart"}).raise_for_status()
    connect_demo(client)
    assert client.get("/key").json()["memories"] == 1
    assert bridge.graph().ro_query("MATCH (r:StyleRule {id:'pa1'}) RETURN r.superseded_at").result_set[0][0]


def test_failed_flash_write_leaves_no_event_after_restart(demo):
    client, board, bridge = demo
    connect_demo(client)
    assert client.get("/key").json()["usb"] == "connected"
    client.post("/sim/command", json={"command": "fail_storage"}).raise_for_status()
    assert client.post("/rules/forget", json={"rule_id": "pa1"}).status_code == 409
    client.post("/sim/command", json={"command": "restart"}).raise_for_status()
    connect_demo(client)
    assert client.get("/key").json()["memories"] == 0
    assert bridge.graph().ro_query("MATCH (r:StyleRule {id:'pa1'}) RETURN r.superseded_at").result_set == [[None]]


def test_concurrent_saves_keep_board_and_graph_counts_in_order(demo, monkeypatch):
    client, board, bridge = demo
    connect_demo(client)
    append = bridge.client.append
    first = threading.Event()

    def delayed_commit(event, event_id):
        count = append(event, event_id)
        if count == 1:
            first.set()
            time.sleep(0.1)
        return count

    monkeypatch.setattr(bridge.client, "append", delayed_commit)
    with ThreadPoolExecutor() as pool:
        one = pool.submit(bridge.save_event, bridge.graph(), {"type": "forgot", "at": 1, "rule_id": "pa1"})
        assert first.wait(timeout=3)
        two = pool.submit(bridge.save_event, bridge.graph(), {"type": "forgot", "at": 2, "rule_id": "pa1"})
        assert [one.result(timeout=5), two.result(timeout=5)] == [1, 2]
    assert client.get("/key").json()["memories"] == 2
