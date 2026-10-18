"""Compare the USB key with a temporary virtual copy, using deterministic mock AI.

Stop the hardware demo first. This reads the paired board; it never appends,
resets, or exports its memories. Only the normal signed-unlock counter advances.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

CASES = [
    {"person_id": "priya-nair", "person_name": "Priya Nair", "platform": "linkedin",
     "post_text": "We launched our ServiceNow AI project."},
    {"person_id": "aarav-mehta", "person_name": "Aarav Mehta", "platform": "slack",
     "mode": "note", "user_note": "See you at cricket tomorrow, Aarav."},
    {"person_id": "new-person", "person_name": "New Person", "platform": "x",
     "circle_hint": "Friends", "post_text": "My first hackathon is done!"},
    {"platform": "linkedin", "mode": "post", "user_note": "Our AI project is ready for the hackathon."},
]


class PromptRecorder:
    """Mock only the model response; exercise the real prompt builder and Critic."""
    def __init__(self, delay=0):
        self.calls = []
        self.delay = delay
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(deepcopy(request))
        time.sleep(self.delay)
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:20]
        # Different context must produce different output, unlike a constant demo mock.
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="text", text=f"Mock draft {digest}.")])


def capture_drafts(api, bridge, *, delay=0):
    from fastapi.testclient import TestClient

    recorder, results = PromptRecorder(delay), []
    # The caller owns bridge startup/shutdown; do not enter the API lifespan here.
    with patch.object(api, "memory", bridge), patch.object(api, "state", bridge.state), \
            patch.object(api, "claude", lambda: recorder), closing(TestClient(api.app)) as client:
        for case in CASES:
            response = client.post("/draft", json=case)
            if response.status_code != 200:
                raise AssertionError(f"Draft case {len(results) + 1} returned HTTP {response.status_code}")
            data = response.json()
            results.append({field: data[field] for field in ("mode", "draft", "rules", "person", "topics", "critic")})
    return {"drafts": results, "prompts": recorder.calls}


def compare_usb_and_virtual(api, usb, directory: Path, *, delay=0):
    from purrsona import keys
    from purrsona.key_bridge import MemoryBridge

    snapshot, records = usb.client.pull()
    assert [item["event"] for item in records] == usb.events(snapshot["key_id"])
    virtual_id = f"parity-{uuid.uuid4().hex[:12]}"
    seeds = directory / "seeds"
    seeds.mkdir()
    (seeds / f"{virtual_id}.json").write_text(json.dumps(snapshot), encoding="utf-8")
    virtual = MemoryBridge(usb.db, usb_enabled=False)
    with patch.object(keys, "SEED_DIR", seeds), patch.dict(os.environ, PURRSONA_KEYS_DIR=str(directory / "events")):
        for record in records:
            keys.append_event(virtual_id, record["event"])
        try:
            virtual.plug_virtual(virtual_id)
            actual = capture_drafts(api, usb, delay=delay)
            expected = capture_drafts(api, virtual)
            for field in ("drafts", "prompts"):
                assert actual[field] == expected[field], f"USB and virtual {field} differ"
            assert all(draft["mode"] == "voice" and draft["rules"] for draft in actual["drafts"])
            return {"cases": len(CASES), "model_calls": len(actual["prompts"]),
                    "memories": len(records), "result": "PASS", "ai": "deterministic mock"}
        finally:
            virtual.unplug()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="USB port when more than one ESP32 is connected")
    args = parser.parse_args()
    from redislite.falkordb_client import FalkorDB

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="purrsona-parity-") as directory:
        db = FalkorDB(str(Path(directory) / "graph.rdb"),
                     serverconfig={"port": str(port), "bind": "127.0.0.1"})
        usb = None
        try:
            os.environ.update(FALKORDB_HOST="127.0.0.1", FALKORDB_PORT=str(port), PURRSONA_USB="0")
            if args.port:
                os.environ["PURRSONA_USB_PORT"] = args.port
            from purrsona import app as api
            from purrsona.key_bridge import MemoryBridge

            usb = MemoryBridge(api.db, usb_enabled=True)
            print("Press B when the LilyGO asks to unlock (within 2 minutes).", flush=True)
            usb.start()
            deadline = time.monotonic() + 135
            while usb.state["key_id"] is None:
                if usb.usb_status in ("needs_pairing", "choose_port", "connection_failed"):
                    raise RuntimeError(f"USB state: {usb.usb_status}. Check pairing/port and retry.")
                if time.monotonic() >= deadline:
                    raise RuntimeError("No approved USB key. Reconnect and run the check again.")
                time.sleep(0.05)
            print("Comparing the board snapshot and saved events with a temporary virtual key.", flush=True)
            print(json.dumps(compare_usb_and_virtual(api, usb, Path(directory), delay=0.8)), flush=True)
        finally:
            if usb:
                usb.stop()
            db.client.shutdown()


if __name__ == "__main__":
    main()
