"""Wire, consent and persistence checks; no USB board, database or API key needed."""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from purrsona import virtual_key
from purrsona.key_protocol import (CONSENT_TIMEOUT, UNLOCK_TIMEOUT, MAX_LINE_BYTES, JsonLines, ProtocolError, UnlockChallenge,
                                   encode, from_base64, signed_bytes, to_base64, unpack_pull)
from purrsona.learning import build_event, build_sent_event
from purrsona.virtual_key import KeyStorage, VirtualConnection, VirtualKey

SEED = Path(__file__).resolve().parents[2] / "data/seed/demo-maulik.json"
EVENT = {"type": "forgot", "at": 1792172000000, "rule_id": "pa1", "rule_text": "Demo rule"}


def decode(data):
    return JsonLines().feed(data, 0)


class Harness:
    def __init__(self, storage):
        self.now = 0.0
        self.next_id = 0
        self.device = VirtualKey(storage)
        self.connection = VirtualConnection(self.device, clock=lambda: self.now)
        self.session = None

    def message(self, kind, **payload):
        self.next_id += 1
        message = {"v": 1, "type": kind, "id": f"request-{self.next_id}"}
        if self.session:
            message["session"] = self.session
        return {**message, **payload}

    def send(self, kind, **payload):
        return decode(self.connection.feed(encode(self.message(kind, **payload))))

    def unlock(self):
        challenge = UnlockChallenge(self.device.storage.key_id, "test-laptop", "challenge", issued_at=self.now)
        assert self.connection.feed(encode(challenge.request())) == b""
        response = decode(self.connection.approve())[0]
        challenge.verify(response, self.device.storage.public_key, self.device.storage.counter - 1, now=self.now)
        self.session = challenge.session
        assert self.send("UNLOCKED")[0]["type"] == "ACK"
        return challenge, response


@pytest.fixture
def storage(tmp_path):
    store = KeyStorage(tmp_path / "device", SEED)
    yield store
    store.close()


@pytest.fixture
def key(storage):
    return Harness(storage)


def test_fragmented_utf8_and_coalesced_frames():
    message = {"v": 1, "type": "STATUS", "id": "s1", "message": "Brain 🧠\nReady"}
    data = encode(message)
    reader, result = JsonLines(), []
    for byte in data:
        result.extend(reader.feed(bytes([byte]), 0))
    assert result == [message]
    assert JsonLines().feed(data + data, 0) == [message, message]


def test_exact_byte_limit_and_oversized_frame():
    message = {"v": 1, "type": "HELLO", "id": "a", "extra": ""}
    padding = MAX_LINE_BYTES - len(encode(message)) + 1
    message["extra"] = "x" * padding
    data = encode(message)
    assert len(data) == MAX_LINE_BYTES + 1
    assert decode(data) == [message]
    reader = JsonLines()
    with pytest.raises(ProtocolError, match="line limit"):
        reader.feed(data[:-1] + b"x", 0)
    assert reader.closed and not reader.buffer


@pytest.mark.parametrize("data", [
    b'{"v":1,"v":1,"type":"HELLO","id":"a"}\n',
    b'{"v":true,"type":"HELLO","id":"a"}\n',
    b'{"v":2,"type":"HELLO","id":"a"}\n',
    b'{"v":1,"type":"HELLO","id":"a","extra":NaN}\n',
    b'{"v":1,"type":"HELLO","id":"a","extra":1e999}\n',
    b'{"v":1,"type":"HELLO","id":"a","extra":"\\ud800"}\n',
    b'{"v":1,"type":"HELLO","id":"a","extra":"\\u0000"}\n',
    b'{"v":1,"type":"HELLO","id":"a","reply_to":"b"}\n',
    b'[]\n', b'\xff\n', b'boot log\n', b'\n',
])
def test_malformed_frames_close_connection(data):
    reader = JsonLines()
    with pytest.raises(ProtocolError):
        reader.feed(data, 0)
    assert reader.closed


def test_partial_frame_timeout_is_total_not_sliding():
    reader = JsonLines()
    reader.feed(b'{"v":', 0)
    reader.feed(b'1', 9)
    with pytest.raises(ProtocolError, match="timed out"):
        reader.check_timeout(10)
    assert reader.closed and not reader.buffer


def test_locked_key_and_unrequested_approval_do_not_release_data(key):
    assert key.connection.approve() == b""
    assert key.send("PULL", session="0" * 32)[0]["code"] == "LOCKED"
    assert key.send("UNLOCKED", session="0" * 32)[0]["code"] == "LOCKED"
    assert key.send("APPEND", session="0" * 32, event_id="event", event=EVENT)[0]["code"] == "LOCKED"
    assert not key.device.storage.records


def test_pairing_waits_for_fresh_consent_and_reuses_identity(key):
    key.connection.approve()  # A press before the prompt cannot approve the next request.
    assert key.send("PAIR_BEGIN", laptop_id="test-laptop") == []
    assert key.device.pending is not None
    assert key.send("HELLO")[0]["code"] == "INVALID_STATE"
    pair = decode(key.connection.approve())[0]
    assert pair["type"] == "PAIR_OK"
    assert from_base64(pair["pk"], 32) == key.device.storage.public_key
    assert key.device.session is None
    assert key.send("PAIR_BEGIN", laptop_id="test-laptop") == []
    assert decode(key.connection.approve())[0]["pk"] == pair["pk"]


@pytest.mark.parametrize("cancel", [True, False])
def test_consent_denial_or_timeout_stays_locked(key, cancel):
    challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
    key.connection.feed(encode(challenge.request()))
    if cancel:
        response = decode(key.connection.cancel())[0]
    else:
        key.now = CONSENT_TIMEOUT
        response = decode(key.connection.tick())[0]
    assert response["type"] == "DENIED"
    assert response["reason"] == ("cancelled" if cancel else "timeout")
    assert key.device.storage.counter == 0
    assert key.connection.approve() == b""
    assert key.device.session is None


def test_signed_bytes_match_firmware_layout():
    assert signed_bytes("key", "laptop", "a" * 32, "b" * 64, 7) == (
        b"MEMKEY-UNLOCK-v1\nkey\nlaptop\n" + b"a" * 32 + b"\n" + b"b" * 64 + b"\n7")


def test_unlock_signature_persisted_counter_and_replay_rejection(key):
    challenge, response = key.unlock()
    stored = json.loads(key.device.storage.identity_path.read_text())
    assert stored["counter"] == response["counter"] == 1
    with pytest.raises(ProtocolError, match="already used"):
        challenge.verify(response, key.device.storage.public_key, 0, now=0)
    assert key.send("HELLO")[0]["code"] == "INVALID_STATE"
    assert key.send("PULL", session="f" * 32)[0]["code"] == "LOCKED"


@pytest.mark.parametrize("change", ["nonce", "session", "key_id", "laptop_id", "request_id", "counter", "sig", "expired"])
def test_changed_challenge_or_signature_cannot_unlock(key, change):
    challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
    key.connection.feed(encode(challenge.request()))
    response = decode(key.connection.approve())[0]
    if change == "counter":
        response["counter"] += 1
    elif change == "sig":
        response["sig"] = to_base64(b"\x00" * 64)
    elif change != "expired":
        setattr(challenge, change, "f" * (64 if change == "nonce" else 32))
    with pytest.raises(ProtocolError):
        challenge.verify(response, key.device.storage.public_key, 0, now=UNLOCK_TIMEOUT if change == "expired" else 0)
    assert challenge.used


def test_old_counter_and_wrong_public_key_fail(key):
    challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
    key.connection.feed(encode(challenge.request()))
    response = decode(key.connection.approve())[0]
    stale = deepcopy(challenge)
    with pytest.raises(ProtocolError, match="did not advance"):
        stale.verify(response, key.device.storage.public_key, 1, now=0)
    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(ProtocolError, match="did not verify"):
        challenge.verify(response, other, 0, now=0)


def test_unlock_confirmation_expires_after_signature(key):
    challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
    key.connection.feed(encode(challenge.request()))
    key.connection.approve()
    key.now = UNLOCK_TIMEOUT
    assert key.send("UNLOCKED", session=challenge.session)[0]["code"] == "LOCKED"


def test_approval_near_end_of_window_leaves_time_to_verify_and_ack(key):
    challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
    key.connection.feed(encode(challenge.request()))
    key.now = CONSENT_TIMEOUT - 1
    response = decode(key.connection.approve())[0]
    key.now = UNLOCK_TIMEOUT - 1
    challenge.verify(response, key.device.storage.public_key, 0, now=key.now)
    assert key.send("UNLOCKED", session=challenge.session)[0]["type"] == "ACK"


def test_append_deduplicates_and_rejects_conflicting_event(key):
    key.unlock()
    assert key.send("APPEND", event_id="event-1", event=EVENT)[0]["count"] == 1
    reordered = dict(reversed(list(EVENT.items())))
    assert key.send("APPEND", event_id="event-1", event=reordered)[0]["count"] == 1
    response = key.send("APPEND", event_id="event-1", event={**EVENT, "rule_id": "different"})[0]
    assert response["code"] == "EVENT_CONFLICT"
    assert len(key.device.storage.events_path.read_text().splitlines()) == 1


@pytest.mark.parametrize("event", [
    {**EVENT, "at": True}, {**EVENT, "at": -1}, {**EVENT, "type": "unknown"},
    {"type": "learned", "at": 1}, {"type": "sent", "at": 1}, {"type": "moved", "at": 1, "person": {"id": "p"}},
    None,
])
def test_invalid_events_never_reach_storage(key, event):
    key.unlock()
    assert key.send("APPEND", event_id="event-1", event=event)[0]["code"] == "BAD_REQUEST"
    assert key.device.storage.records == {}


def test_current_learning_events_round_trip_without_changing_payload(key):
    key.unlock()
    events = [EVENT, build_sent_event("p", "Person", "Work", False, "linkedin", "d1", "Hello", []),
              build_event("p", "Person", "Work", False, "linkedin", "d2", "Hello", "Hi",
                          [{"kind": "greeting", "text": "Use Hi"}], []),
              {"type": "moved", "at": 1792173000000, "person": {"id": "p", "name": "Person"},
               "from": "Work", "circle": "Community"}]
    for index, event in enumerate(events):
        assert key.send("APPEND", event_id=f"event-{index}", event=event)[0]["type"] == "ACK"
    request = key.message("PULL")
    messages = decode(key.connection.feed(encode(request)))
    snapshot, records = unpack_pull(messages, request["id"], key.session, key.device.storage.key_id)
    assert snapshot == json.loads(SEED.read_text())
    assert [record["event"] for record in records] == events


@pytest.mark.parametrize("damage", ["missing_end", "missing_event", "wrong_key", "wrong_count", "wrong_session", "after_end"])
def test_invalid_pull_never_returns_partial_memories(key, damage):
    key.unlock()
    key.send("APPEND", event_id="event-1", event=EVENT)
    request = key.message("PULL")
    messages = decode(key.connection.feed(encode(request)))
    if damage == "missing_end":
        messages.pop()
    elif damage == "missing_event":
        messages.pop(1)
    elif damage == "wrong_key":
        messages[0]["snapshot"]["key_id"] = "someone-else"
    elif damage == "wrong_count":
        messages[-1]["count"] += 1
    elif damage == "wrong_session":
        messages[0]["session"] = "f" * 32
    else:
        messages.append(messages[1])
    with pytest.raises(ProtocolError):
        unpack_pull(messages, request["id"], key.session, key.device.storage.key_id)


def test_restart_restores_identity_counter_and_retry_index(storage):
    key = Harness(storage)
    key.unlock()
    key.send("APPEND", event_id="event-1", event=EVENT)
    public_key, counter = storage.public_key, storage.counter
    key.connection.close()
    storage.close()
    reopened = KeyStorage(storage.directory, SEED)
    try:
        assert reopened.public_key == public_key and reopened.counter == counter
        second = Harness(reopened)
        assert second.send("PULL", session=key.session)[0]["code"] == "LOCKED"
        second.unlock()
        assert second.send("APPEND", event_id="event-1", event=EVENT)[0]["count"] == 1
        assert reopened.counter == counter + 1
    finally:
        reopened.close()


def test_partial_final_record_is_recovered_without_losing_acknowledged_event(storage):
    storage.append("event-1", EVENT)
    storage.close()
    with storage.events_path.open("ab") as stream:
        stream.write(b'{"event_id":"partial"')
    reopened = KeyStorage(storage.directory, SEED)
    try:
        assert list(reopened.records) == ["event-1"]
        assert reopened.events_path.read_bytes().endswith(b"\n")
        assert reopened.append("event-2", EVENT) == 2
    finally:
        reopened.close()


def test_complete_corrupt_record_is_not_silently_discarded(storage):
    storage.close()
    storage.events_path.write_bytes(b'{broken}\n')
    with pytest.raises(ProtocolError, match="storage"):
        KeyStorage(storage.directory, SEED)
    assert storage.events_path.read_bytes() == b'{broken}\n'


def test_two_processes_cannot_own_same_simulated_key(storage):
    with pytest.raises(ProtocolError, match="storage"):
        KeyStorage(storage.directory, SEED)


@pytest.mark.parametrize("operation", ["APPEND", "UNLOCK"])
def test_sync_failure_never_acknowledges_or_signs(key, monkeypatch, operation):
    if operation == "APPEND":
        key.unlock()
    else:
        challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
        key.connection.feed(encode(challenge.request()))

    def failed_sync(descriptor):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(virtual_key.os, "fsync", failed_sync)
    if operation == "APPEND":
        response = key.send("APPEND", event_id="event-1", event=EVENT)[0]
    else:
        response = decode(key.connection.approve())[0]
    assert response["type"] == "ERROR" and response["code"] == "STORAGE_ERROR"
    assert key.device.session is None
    assert key.device.storage.failed


def graph_view(revision=1):
    return {"revision": revision, "nodes": [{"id": "Person:p", "kind": "Person", "label": "Person"}],
            "edges": [], "truncated": False}


def test_graph_update_is_atomic_and_rejects_dangling_edges_and_stale_revisions(key):
    key.unlock()
    assert key.send("GRAPH_VIEW", **graph_view())[0]["revision"] == 1
    original = deepcopy(key.device.graph)
    invalid = {**graph_view(2), "edges": [{"source": "Person:p", "target": "missing", "type": "KNOWS"}]}
    assert key.send("GRAPH_VIEW", **invalid)[0]["code"] == "BAD_REQUEST"
    assert key.device.graph == original
    assert key.send("GRAPH_VIEW", **graph_view())[0]["code"] == "STALE_VIEW"
    assert key.device.graph == original


def test_graph_limits_count_utf8_bytes(key):
    key.unlock()
    view = graph_view()
    view["nodes"][0]["label"] = "🧠" * 19
    assert key.send("GRAPH_VIEW", **view)[0]["code"] == "BAD_REQUEST"
    assert key.device.graph is None


@pytest.mark.parametrize("ending", ["bye", "cable", "idle", "malformed"])
def test_disconnect_clears_personal_display_data_but_preserves_memories(key, ending):
    key.unlock()
    key.send("PULL")
    key.send("APPEND", event_id="event-1", event=EVENT)
    key.send("STATUS", state="ready", memories=1)
    key.send("GRAPH_VIEW", **graph_view())
    if ending == "bye":
        assert key.send("BYE", reason="eject")[0]["type"] == "ACK"
    elif ending == "cable":
        key.connection.close()
    elif ending == "idle":
        key.now = 3
        key.connection.tick()
    else:
        assert decode(key.connection.feed(b'bad frame\n'))[0]["type"] == "ERROR"
    assert key.device.session is None
    assert key.device.status is None and key.device.graph is None
    assert list(key.device.storage.records) == ["event-1"]


def test_partial_frame_gets_bounded_transfer_deadline_then_locks(key):
    key.unlock()
    message = encode(key.message("STATUS", state="loading"))
    key.connection.feed(message[:5])
    key.now = 4
    key.connection.tick()
    assert key.device.session is not None
    key.now = 10
    assert decode(key.connection.tick())[0]["type"] == "ERROR"
    assert key.device.session is None and key.connection.reader.closed


def test_button_cannot_approve_after_framing_timeout(key):
    challenge = UnlockChallenge(key.device.storage.key_id, "laptop", "challenge", issued_at=0)
    key.connection.feed(encode(challenge.request()))
    key.connection.feed(b'{')
    key.now = 10
    assert decode(key.connection.approve())[0]["type"] == "ERROR"
    assert key.device.storage.counter == 0 and key.connection.reader.closed


def test_append_rejects_event_that_would_not_fit_during_replay(key):
    key.unlock()
    event = {**EVENT, "rule_text": ""}
    message = key.message("APPEND", event_id="event", event=event)
    event["rule_text"] = "x" * (MAX_LINE_BYTES - len(encode(message)) + 1)
    response = decode(key.connection.feed(encode(message)))[0]
    assert response["code"] == "TOO_LARGE"
    assert key.device.storage.records == {}


def test_status_heartbeat_keeps_session_alive_and_ready_requires_pull(key):
    key.unlock()
    assert key.send("STATUS", state="ready", memories=0)[0]["code"] == "INVALID_STATE"
    for _ in range(5):
        key.now += 1
        assert key.send("STATUS", state="loading")[0]["type"] == "ACK"
    key.send("PULL")
    assert key.send("STATUS", state="ready", memories=0)[0]["type"] == "ACK"


@pytest.mark.parametrize("counts", [{}, {"memories": None}, {"memories": True}, {"memories": -1},
                                    {"memories": 1.5}, {"memories": "0"}, {"memories": 2**53}])
def test_invalid_ready_count_is_bad_request_and_can_be_corrected(key, counts):
    key.unlock(); key.send("PULL")
    assert key.send("STATUS", state="ready", **counts)[0]["code"] == "BAD_REQUEST"
    assert key.device.status is None
    assert key.send("STATUS", state="ready", memories=0)[0]["type"] == "ACK"


def test_rejected_status_does_not_keep_session_alive(key):
    key.unlock()
    key.now = 2
    assert key.send("STATUS", state="ready", memories=0)[0]["code"] == "INVALID_STATE"
    key.now = 3
    key.connection.tick()
    assert key.device.session is None


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "33", None, 2**53])
def test_status_rule_count_is_validated_before_changing_screen(key, invalid):
    key.unlock()
    assert key.send("STATUS", state="drafting", rules=33)[0]["type"] == "ACK"
    assert key.send("STATUS", state="learning", rules=invalid)[0]["code"] == "BAD_REQUEST"
    assert key.device.status == {"state": "drafting", "rules": 33}
    assert key.send("STATUS", state="loading")[0]["type"] == "ACK"
    assert key.device.status == {"state": "loading", "rules": 33}


def test_protocol_demo_runs_twice_with_persistent_state(tmp_path):
    command = [sys.executable, "-m", "purrsona.protocol_demo", "--state-dir", str(tmp_path)]
    for _ in range(2):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count("PASS:") == 5
    assert len((tmp_path / "device/events.jsonl").read_text().splitlines()) == 2
    trust = json.loads((tmp_path / "laptop-trust.json").read_text())
    assert trust["last_counter"] == 4
