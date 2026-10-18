"""The physical-check command also runs against the actual C++ firmware in CI."""
import importlib.util
from pathlib import Path

import pytest

from purrsona import app as api, keys
from purrsona.key_bridge import MemoryBridge
from purrsona.learning import build_event, build_sent_event
from purrsona.queries import rules_for_context
from purrsona.simulated_board import PORT
from test_protocol import native_binary  # noqa: F401
from test_simulator import board, trust, wait_until  # noqa: F401

spec = importlib.util.spec_from_file_location("check_key_parity", Path(__file__).resolve().parents[2] / "scripts/check_key_parity.py")
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


def connect(usb, board):
    board.connect()
    usb.start()
    wait_until(lambda: board.frame()["screen"] == "unlock")
    board.command("!button-b")
    wait_until(lambda: usb.state["key_id"] is not None)


@pytest.mark.parametrize("with_memories", [False, True])
def test_usb_and_virtual_produce_identical_prompts_and_drafts(board, trust, tmp_path, monkeypatch, with_memories):
    usb = MemoryBridge(api.db, usb_enabled=True, trust=trust, ports=board.ports, client_factory=board.client)
    try:
        connect(usb, board)
        if with_memories:
            graph = usb.graph()
            event = build_event("priya-nair", "Priya Nair", "Work", False, "linkedin", "parity-draft",
                "Congratulations on the launch.", "Nice launch, Priya!",
                [{"kind": "length", "text": "Keeps replies to Priya under eight words."}],
                rules_for_context(graph, "priya-nair", "linkedin", ["AI"]), at=1792172000000)
            usb.save_event(graph, event)
            usb.save_event(graph, build_sent_event("new-person", "New Person", "Friends", True,
                "x", "parity-sent", "Well done!", [], at=1792172000001))
            usb.save_event(graph, {"type": "forgot", "at": 1792172000002, "rule_id": "pa1"})
            # Replay through PULL after a process restart, not the already-mutated graph.
            usb.stop(); board.power_cycle()
            connect(usb, board)
        before = usb.client.pull()[1]
        result = parity.compare_usb_and_virtual(api, usb, tmp_path)
        assert result["result"] == "PASS" and result["cases"] == 4
        assert result["model_calls"] >= 4 and result["memories"] == (3 if with_memories else 0)
        assert usb.client.pull()[1] == before  # Draft comparison never appends to the board.
        assert api.db.list_graphs() == ["voice:demo-maulik"]  # Temporary virtual graph was deleted.
        captured = parity.capture_drafts(api, usb)
        assert len({draft["draft"] for draft in captured["drafts"]}) == 4
        if with_memories:
            assert any(rule["text"] == event["rules"][0]["text"] for rule in captured["drafts"][0]["rules"])
            assert all(rule["id"] != "pa1" for rule in captured["drafts"][1]["rules"])
            assert captured["drafts"][2]["person"]["known"]
            # A broken virtual replay must fail; fixed canned mock replies would hide this.
            monkeypatch.setattr(keys, "load_events", lambda key_id: [])
            broken = tmp_path / "missing-replay"
            broken.mkdir()
            with pytest.raises(AssertionError, match="USB and virtual drafts differ"):
                parity.compare_usb_and_virtual(api, usb, broken)
    finally:
        usb.stop()
