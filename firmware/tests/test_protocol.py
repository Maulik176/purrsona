"""Python client against the compiled firmware core, including the board's real Ed25519 library."""
import importlib.util
import json
import os
import select
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from purrsona.key_protocol import (CONSENT_TIMEOUT, ProtocolError, UnlockChallenge,
                                  encode, fingerprint, from_base64, to_base64)
from purrsona.usb_key import TrustStore, UsbKey

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def native_binary(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("build_native", ROOT / "firmware/scripts/build_native.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(tmp_path_factory.mktemp("native-firmware") / "protocol")


class NativeTransport:
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary), str(ROOT / "data/seed/demo-maulik.json")],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self.auto_approve = True

    def write(self, data):
        self.process.stdin.write(data)
        if self.auto_approve and b'"type":"CHALLENGE"' in data:
            self.process.stdin.write(b"!approve\n")
        return len(data)

    def read(self, size):
        if select.select([self.process.stdout], [], [], 0.1)[0]:
            return os.read(self.process.stdout.fileno(), size)
        return b""

    def control(self, value):
        self.process.stdin.write(value.encode() + b"\n")

    def inspect(self):
        self.control("!inspect")
        assert select.select([self.process.stdout], [], [], 2)[0], "Native firmware did not respond"
        return json.loads(self.process.stdout.readline())

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=3)
        self.process.stdout.close()


@pytest.fixture
def link(native_binary, tmp_path):
    transport = NativeTransport(native_binary)
    key = UsbKey("native-firmware", transport=transport)
    trust = TrustStore(tmp_path)
    public_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32))).public_key().public_bytes_raw()
    trust.save({"key_id": "demo-maulik", "pk": to_base64(public_key), "last_counter": 0})
    yield key, transport, trust
    key.close()


def test_firmware_signature_and_snapshot_match_python(link):
    key, transport, trust = link
    assert key.hello() == "demo-maulik"
    key.unlock(trust)
    assert trust.load(key.key_id)["last_counter"] == 1
    snapshot, events = key.pull()
    assert snapshot == json.loads((ROOT / "data/seed/demo-maulik.json").read_text())
    assert events == []


def test_firmware_stores_a_circle_move(link):
    key, _, trust = link
    key.hello(); key.unlock(trust); key.pull()
    moved = {"type": "moved", "at": 124, "person": {"id": "neha-kulkarni", "name": "Neha Kulkarni"},
             "from": "Work", "circle": "Community"}
    assert key.append(moved, "event-move") == 1
    assert key.pull()[1] == [{"event_id": "event-move", "event": moved}]


def test_firmware_append_retry_and_graph_navigation(link):
    key, transport, trust = link
    key.hello(); key.unlock(trust); key.pull()
    event = {"type": "forgot", "at": 123, "rule_id": "pa1", "rule_text": "Unicode 🧠"}
    assert key.append(event, "event-1") == 1
    assert key.append(dict(reversed(list(event.items()))), "event-1") == 1
    _, events = key.pull()
    assert events == [{"event_id": "event-1", "event": event}]
    nodes = [{"id": f"Person:p{n}", "kind": "Person", "label": f"Person {n}"} for n in range(9)]
    key.ack("GRAPH_VIEW", revision=1, nodes=nodes, edges=[{"source": "Person:p7", "target": "Person:p8", "type": "KNOWS"}], truncated=False)
    key.ack("STATUS", state="ready", memories=1)
    for _ in range(7):
        transport.control("!next")
    assert transport.inspect()["selected"] == 7
    transport.control("!open")
    assert transport.inspect()["center"] == 7
    transport.control("!next"); transport.control("!open")
    assert transport.inspect()["center"] == 8
    transport.control("!back")
    assert transport.inspect()["center"] == 7
    transport.control("!open")
    assert transport.inspect()["details"]
    transport.control("!back"); transport.control("!back")
    assert transport.inspect()["center"] == -1
    key.ack("BYE", reason="eject")
    state = transport.inspect()
    assert state["session"] == "" and state["nodes"] == 0 and state["events"] == 1


@pytest.mark.parametrize("wire", [
    b'{"v":1,"v":1,"type":"HELLO","id":"a"}\n',
    b'{"v":1,"type":"HELLO","id":"a","x":NaN}\n',
    b'{"v":true,"type":"HELLO","id":"a"}\n',
    b'{"v":1,"type":"HELLO","id":"a"}garbage\n',
    b'{"v":1,"type":"HELLO","id":"a","x":"\\ud800"}\n',
    b'{"v":1,"type":"HELLO","id":"a","x":"\\u0000"}\n',
    b'{"v":1,"type":"HELLO","id":"a","x":01}\n',
    b'{"v":1,"type":"HELLO","id":"a","x":"\xc0\xaf"}\n',
])
def test_firmware_rejects_malformed_json(link, wire):
    _, transport, _ = link
    transport.write(wire)
    assert json.loads(transport.read(4096))["type"] == "ERROR"


def test_firmware_fingerprint_and_consent(link):
    key, transport, _ = link
    transport.control("!approve")  # A previous press cannot consent to a later prompt.
    transport.write(b'{"v":1,"type":"PAIR_BEGIN","id":"pair","laptop_id":"test"}\n')
    assert transport.read(4096) == b""
    transport.control("!approve")
    reply = json.loads(transport.read(4096))
    public_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32))).public_key().public_bytes_raw()
    assert reply["pk"] == to_base64(public_key) and reply["fingerprint"] == fingerprint(public_key)
    assert transport.inspect()["session"] == ""


@pytest.mark.parametrize("kind", ["PAIR_BEGIN", "CHALLENGE"])
@pytest.mark.parametrize("expired", [False, True])
def test_firmware_two_minute_consent_window(link, kind, expired):
    key, transport, trust = link
    key.hello()
    transport.auto_approve = False
    challenge = UnlockChallenge(key.key_id, "test-laptop", "consent-test", issued_at=0)
    request = challenge.request() if kind == "CHALLENGE" else {
        "v": 1, "type": kind, "id": "pair-test", "laptop_id": "test-laptop"}
    transport.write(encode(request))
    delay = CONSENT_TIMEOUT if expired else CONSENT_TIMEOUT - 1
    transport.control(f"!tick {delay * 1000}")
    transport.control("!approve")
    reply = json.loads(transport.read(4096))
    if expired:
        assert reply["type"] == "DENIED" and reply["reason"] == "timeout"
        state = transport.inspect()
        assert state["session"] == "" and state["counter"] == 0
    elif kind == "PAIR_BEGIN":
        assert reply["type"] == "PAIR_OK"
    else:
        challenge.verify(reply, from_base64(trust.load(key.key_id)["pk"], 32), 0, now=delay)
        key.session = challenge.session
        key.ack("UNLOCKED")
        assert transport.inspect()["session"] == challenge.session


def test_firmware_timeout_clears_screen_but_keeps_memory(link):
    key, transport, trust = link
    key.hello(); key.unlock(trust)
    key.append({"type": "forgot", "at": 123, "rule_id": "pa1"}, "saved")
    key.ack("GRAPH_VIEW", revision=1, nodes=[{"id": "User:demo", "kind": "User", "label": "Demo"}], edges=[], truncated=False)
    transport.control("!tick 3000")
    state = transport.inspect()
    assert state["session"] == "" and state["nodes"] == 0 and state["events"] == 1


def test_firmware_storage_failure_never_sends_ack(link):
    key, transport, trust = link
    key.hello(); key.unlock(trust)
    transport.control("!fail-storage")
    with pytest.raises(ProtocolError):
        key.append({"type": "forgot", "at": 123, "rule_id": "pa1"}, "failed")


@pytest.mark.parametrize("counts", [{}, {"memories": None}, {"memories": True}, {"memories": -1},
                                    {"memories": 1.5}, {"memories": "0"}, {"memories": 2**53}])
def test_firmware_invalid_ready_count_is_bad_request_and_can_be_corrected(link, counts):
    key, transport, trust = link
    key.hello(); key.unlock(trust); key.pull()
    request = {"v": 1, "type": "STATUS", "id": "bad-count", "session": key.session,
               "state": "ready", **counts}
    transport.write(encode(request))
    reply = json.loads(transport.read(4096))
    assert reply["type"] == "ERROR" and reply["code"] == "BAD_REQUEST"
    assert reply["reply_to"] == request["id"] and reply["session"] == key.session
    assert transport.inspect()["screen"] == "loading"
    # Keep the session on the device: a well-formed retry can still finish loading.
    key.ack("STATUS", state="ready", memories=0)
    assert transport.inspect()["screen"] == "ready"


def test_firmware_ready_requires_current_durable_count(link):
    key, transport, trust = link
    key.hello(); key.unlock(trust); key.pull()
    key.append({"type": "forgot", "at": 123, "rule_id": "pa1"}, "saved")
    transport.write(encode({"v": 1, "type": "STATUS", "id": "stale-count", "session": key.session,
                            "state": "ready", "memories": 0}))
    assert json.loads(transport.read(4096))["code"] == "INVALID_STATE"
    key.ack("STATUS", state="ready", memories=1)
    assert transport.inspect()["screen"] == "ready"
