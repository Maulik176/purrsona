"""The real USB client exercised against a fragmented serial stand-in."""
import threading
from pathlib import Path

import pytest

from purrsona.key_protocol import ProtocolError, to_base64
from purrsona.usb_key import TrustStore, UsbKey
from purrsona.virtual_key import KeyStorage, VirtualConnection, VirtualKey

SEED = Path(__file__).resolve().parents[2] / "data/seed/demo-maulik.json"


class SerialStandIn:
    def __init__(self, storage, *, short_writes=False):
        self.now = 0.0
        self.connection = VirtualConnection(VirtualKey(storage), clock=lambda: self.now)
        self.buffer = bytearray()
        self.closed = False
        self.short_writes = short_writes
        self.drop_reply = False

    def write(self, data):
        if self.closed:
            raise OSError("Cable disconnected")
        sent = min(7, len(data)) if self.short_writes else len(data)
        output = self.connection.feed(data[:sent])
        if self.connection.device.pending:
            output += self.connection.approve()
        if not self.drop_reply:
            self.buffer.extend(output)
        return sent

    def read(self, size):
        if self.closed:
            raise OSError("Cable disconnected")
        if not self.buffer:
            self.now += 0.1
            return b""
        size = min(size, 23)
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def close(self):
        self.closed = True
        self.connection.close()


@pytest.fixture
def usb_fixture(tmp_path):
    storage = KeyStorage(tmp_path / "device", SEED)
    trust = TrustStore(tmp_path / "trust")
    trust.save({"key_id": storage.key_id, "pk": to_base64(storage.public_key), "last_counter": 0})

    def factory(port="test-port", short_writes=False):
        transport = SerialStandIn(storage, short_writes=short_writes)
        return UsbKey(port, transport=transport, clock=lambda: transport.now)

    yield storage, trust, factory
    storage.close()


def test_real_client_handles_partial_writes_and_fragmented_responses(usb_fixture):
    storage, trust, factory = usb_fixture
    key = factory(short_writes=True)
    assert key.hello() == storage.key_id
    key.unlock(trust)
    assert key.authenticated and trust.load(key.key_id)["last_counter"] == 1
    snapshot, records = key.pull()
    assert snapshot["key_id"] == storage.key_id and records == []
    assert key.append({"type": "forgot", "at": 1, "rule_id": "pa1"}, "event-1") == 1
    key.ack("BYE", reason="eject")
    key.close()


def test_native_port_drains_reset_logs_before_hello_but_rejects_later_noise(usb_fixture, monkeypatch):
    storage, _, _ = usb_fixture
    transport = SerialStandIn(storage)

    def open_port():
        transport.buffer.extend(b"ESP-ROM:esp32s3-20210327\r\nBuild:Mar 27 2021\r\n")

    transport.open = open_port
    monkeypatch.setattr("purrsona.usb_key.serial.Serial", lambda **kwargs: transport)
    key = UsbKey("test-port", clock=lambda: transport.now)
    assert key.hello() == storage.key_id
    transport.buffer.extend(b"unexpected debug output\n")
    with pytest.raises(ProtocolError, match="Malformed JSON"):
        key.request("HELLO")
    assert key.closed


def test_trust_store_never_replaces_keys_or_rolls_back_counters(usb_fixture):
    storage, trust, _ = usb_fixture
    record = trust.load(storage.key_id)
    trust.save({**record, "last_counter": 8})
    with pytest.raises(ProtocolError):
        trust.save(record)
    with pytest.raises(ProtocolError):
        trust.save({**record, "pk": to_base64(b"x" * 32), "last_counter": 9})
    assert trust.load(storage.key_id)["last_counter"] == 8
    with pytest.raises(ProtocolError):
        trust.load("../escape")


def test_client_does_not_unlock_an_unpaired_key(usb_fixture, tmp_path):
    _, _, factory = usb_fixture
    key = factory()
    key.hello()
    with pytest.raises(ProtocolError, match="Pair"):
        key.unlock(TrustStore(tmp_path / "unpaired"))
    assert not key.authenticated
    key.close()


def test_client_allows_time_to_press_the_physical_button(usb_fixture):
    _, trust, factory = usb_fixture
    key = factory()
    key.hello()
    approve = key.transport.connection.approve

    def delayed_press():
        key.transport.now += 119
        return approve()

    key.transport.connection.approve = delayed_press
    key.unlock(trust)
    assert key.authenticated
    key.close()


def test_lost_ack_is_unknown_outcome_and_reconnect_retry_uses_same_id(usb_fixture):
    storage, trust, factory = usb_fixture
    key = factory(); key.hello(); key.unlock(trust)
    key.transport.drop_reply = True
    event = {"type": "forgot", "at": 1, "rule_id": "pa1"}
    with pytest.raises(ProtocolError, match="timed out"):
        key.append(event, "same-id")
    assert key.closed and len(storage.records) == 1
    reconnected = factory(); reconnected.hello(); reconnected.unlock(trust)
    assert reconnected.append(event, "same-id") == 1
    reconnected.close()


def test_close_can_interrupt_a_request_without_waiting_for_lock(usb_fixture):
    _, _, factory = usb_fixture
    key = factory()
    held, release = threading.Event(), threading.Event()

    def hold_request_lock():
        with key.lock:
            held.set()
            release.wait(2)

    thread = threading.Thread(target=hold_request_lock)
    thread.start(); assert held.wait(1)
    try:
        key.close()
        assert key.closed
    finally:
        release.set(); thread.join()
