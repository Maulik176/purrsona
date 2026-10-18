"""Persistent protocol simulator. Existing API virtual-key files are left untouched."""
import fcntl
import logging
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .key_protocol import (CONSENT_TIMEOUT, IDLE_TIMEOUT, MAX_INTEGER, MAX_LINE_BYTES,
                           PROTECTED, UNLOCK_TIMEOUT, JsonLines, ProtocolError, encode,
                           fingerprint, from_base64, identifier, integer, json_bytes,
                           parse_object, require, signed_bytes, to_base64, validate_event,
                           validate_request)

log = logging.getLogger(__name__)


def durable_write(path: Path, data: bytes) -> None:
    """Replace a small file only after syncing its contents, then sync its directory."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_object(path: Path) -> dict:
    with path.open("rb") as stream:
        data = stream.read(MAX_LINE_BYTES + 1)
    require(len(data) <= MAX_LINE_BYTES, "Stored object exceeds size limit", "STORAGE_ERROR")
    return parse_object(data)


class KeyStorage:
    """One process owns a simulated board; identity, counter and events survive restart."""

    def __init__(self, directory: Path, seed: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self.failed = False
        self.lock_file = (directory / ".lock").open("a+b")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            snapshot_path = directory / "snapshot.json"
            if not snapshot_path.exists():
                snapshot = read_object(seed)
                durable_write(snapshot_path, json_bytes(snapshot))
            self.snapshot = read_object(snapshot_path)
            require(identifier(self.snapshot.get("key_id")), "Invalid snapshot key ID")
            self.key_id = self.snapshot["key_id"]
            encode({"v": 1, "type": "SNAPSHOT", "reply_to": "x" * 64, "session": "0" * 32,
                    "snapshot": self.snapshot})
            self.identity_path = directory / "identity.json"
            if not self.identity_path.exists():
                private_key = Ed25519PrivateKey.generate()
                durable_write(self.identity_path, json_bytes({"key_id": self.key_id, "counter": 0,
                              "private_key": to_base64(private_key.private_bytes_raw())}))
            self.identity = read_object(self.identity_path)
            require(self.identity.get("key_id") == self.key_id and integer(self.identity.get("counter")),
                    "Identity does not match snapshot")
            self.private_key = Ed25519PrivateKey.from_private_bytes(
                from_base64(self.identity.get("private_key"), 32))
            self.events_path = directory / "events.jsonl"
            if not self.events_path.exists():
                durable_write(self.events_path, b"")
            self.records = self._recover_events()
        except (OSError, ProtocolError, ValueError) as exc:
            self.close()
            raise ProtocolError("STORAGE_ERROR", "Could not open virtual key storage") from exc

    @property
    def public_key(self) -> bytes:
        return self.private_key.public_key().public_bytes_raw()

    @property
    def counter(self) -> int:
        return self.identity["counter"]

    def close(self):
        self.lock_file.close()

    def _recover_events(self) -> dict[str, dict]:
        records = {}
        with self.events_path.open("r+b") as stream:
            while True:
                start = stream.tell()
                line = stream.readline(MAX_LINE_BYTES + 2)
                if not line:
                    break
                require(len(line) <= MAX_LINE_BYTES + 1, "Stored event exceeds size limit", "STORAGE_ERROR")
                if not line.endswith(b"\n"):
                    stream.truncate(start)
                    log.info("virtual_key_recovered_partial_record")
                    break
                record = parse_object(line[:-1])
                require(identifier(record.get("event_id")) and record["event_id"] not in records,
                        "Invalid stored event ID", "STORAGE_ERROR")
                validate_event(record.get("event"))
                records[record["event_id"]] = record["event"]
            stream.flush()
            os.fsync(stream.fileno())
        return records

    def next_counter(self) -> int:
        require(not self.failed and self.counter < MAX_INTEGER, "Counter unavailable", "STORAGE_ERROR")
        identity = {**self.identity, "counter": self.counter + 1}
        try:
            durable_write(self.identity_path, json_bytes(identity))
        except OSError:
            self.failed = True
            raise
        self.identity = identity
        return self.counter

    def append(self, event_id: str, event: dict) -> int:
        require(not self.failed, "Storage needs reopening", "STORAGE_ERROR")
        require(identifier(event_id), "Invalid event ID")
        validate_event(event)
        if event_id in self.records:
            require(json_bytes(self.records[event_id]) == json_bytes(event),
                    "Event ID already has different contents", "EVENT_CONFLICT")
            return len(self.records)
        # A saved event must also fit its larger replay envelope with any valid request ID.
        encode({"v": 1, "type": "EVENT", "reply_to": "x" * 64, "session": "0" * 32,
                "seq": MAX_INTEGER, "event_id": event_id, "event": event})
        record = json_bytes({"event_id": event_id, "event": event}) + b"\n"
        try:
            with self.events_path.open("ab") as stream:
                stream.write(record)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self.failed = True
            raise
        self.records[event_id] = deepcopy(event)
        return len(self.records)


class VirtualKey:
    """Device state machine. approve/cancel simulate fresh button edges, never held levels."""

    def __init__(self, storage: KeyStorage):
        self.storage = storage
        self.disconnect()

    def disconnect(self):
        self.session: str | None = None
        self.pending: dict | None = None
        self.pending_at = 0.0
        self.signed = False
        self.last_activity = 0.0
        self.status: dict | None = None
        self.graph: dict | None = None
        self.pulled = False

    @staticmethod
    def reply(request: dict, kind: str, **payload) -> dict:
        result = {"v": 1, "type": kind, "reply_to": request["id"]}
        if "session" in request:
            result["session"] = request["session"]
        return {**result, **payload}

    def error(self, request: dict, exc: ProtocolError) -> dict:
        message = {"v": 1, "type": "ERROR", "code": exc.code, "message": str(exc)}
        if identifier(request.get("id")):
            message["reply_to"] = request["id"]
        known = self.session or (self.pending or {}).get("session")
        if known and request.get("session") == known:
            message["session"] = known
        log.info("virtual_key_request_rejected code=%s", exc.code)
        return message

    def tick(self, now: float, *, receiving_frame: bool = False) -> list[dict]:
        if self.pending:
            deadline = UNLOCK_TIMEOUT if self.signed else CONSENT_TIMEOUT
            if now - self.pending_at >= deadline:
                result = [] if self.signed else [self.reply(self.pending, "DENIED", reason="timeout")]
                self.disconnect()
                return result
        if self.session and not receiving_frame and now - self.last_activity >= IDLE_TIMEOUT:
            self.disconnect()
            log.info("virtual_key_session_expired")
        return []

    def approve(self, now: float) -> list[dict]:
        expired = self.tick(now)
        if expired or not self.pending or self.signed:
            return expired
        request = self.pending
        if request["type"] == "PAIR_BEGIN":
            self.pending = None
            return [self.reply(request, "PAIR_OK", key_id=self.storage.key_id,
                               pk=to_base64(self.storage.public_key),
                               fingerprint=fingerprint(self.storage.public_key))]
        try:
            counter = self.storage.next_counter()
            signature = self.storage.private_key.sign(signed_bytes(
                self.storage.key_id, request["laptop_id"], request["session"], request["nonce"], counter))
            self.signed = True
            return [self.reply(request, "UNLOCK", counter=counter, sig=to_base64(signature))]
        except (OSError, ProtocolError):
            result = self.error(request, ProtocolError("STORAGE_ERROR", "Unlock counter could not be saved"))
            self.disconnect()
            return [result]

    def cancel(self, now: float) -> list[dict]:
        expired = self.tick(now)
        if expired or not self.pending or self.signed:
            return expired
        result = self.reply(self.pending, "DENIED", reason="cancelled")
        self.disconnect()
        return [result]

    def receive(self, request: dict, now: float) -> list[dict]:
        try:
            validate_request(request)
            require(not self.storage.failed, "Storage needs reopening", "STORAGE_ERROR")
            kind = request["type"]
            if kind in PROTECTED:
                require(self.session is not None and request["session"] == self.session,
                        "Unlock this connection first", "LOCKED")
            elif kind == "UNLOCKED":
                require(self.pending is not None and self.signed and
                        request["session"] == self.pending["session"] and
                        now - self.pending_at < UNLOCK_TIMEOUT,
                        "No approved challenge for this session", "LOCKED")
            else:
                require(self.session is None and self.pending is None,
                        "Request is not allowed in this state", "INVALID_STATE")

            if kind == "HELLO":
                return [self.reply(request, "HELLO", key_id=self.storage.key_id, fw="virtual-0.1.0",
                                   counter=self.storage.counter, max_line_bytes=MAX_LINE_BYTES)]
            if kind in ("PAIR_BEGIN", "CHALLENGE"):
                self.pending = deepcopy(request)
                self.pending_at = now
                return []
            if kind == "UNLOCKED":
                self.session = request["session"]
                self.pending = None
                self.signed = False
                self.last_activity = now
                return [self.reply(request, "ACK")]

            if kind == "PULL":
                result = [self.reply(request, "SNAPSHOT", snapshot=deepcopy(self.storage.snapshot))]
                result.extend(self.reply(request, "EVENT", seq=index, event_id=event_id, event=deepcopy(event))
                              for index, (event_id, event) in enumerate(self.storage.records.items(), 1))
                result.append(self.reply(request, "PULL_DONE", count=len(self.storage.records)))
                self.pulled = True
                self.last_activity = now
                return result
            if kind == "APPEND":
                count = self.storage.append(request["event_id"], request["event"])
                self.last_activity = now
                return [self.reply(request, "ACK", event_id=request["event_id"], count=count)]
            if kind == "STATUS":
                if request["state"] == "ready":
                    require(self.pulled and request["memories"] == len(self.storage.records),
                            "Ready needs a completed pull and current memory count", "INVALID_STATE")
                self.status = {name: deepcopy(request[name]) for name in ("state", "memories", "message")
                               if name in request}
                self.last_activity = now
                return [self.reply(request, "ACK")]
            if kind == "GRAPH_VIEW":
                require(request["revision"] > (self.graph or {}).get("revision", 0),
                        "Graph revision did not advance", "STALE_VIEW")
                self.graph = {name: deepcopy(request[name]) for name in ("revision", "nodes", "edges", "truncated")}
                self.last_activity = now
                return [self.reply(request, "ACK", revision=request["revision"])]
            result = self.reply(request, "ACK")  # BYE
            self.disconnect()
            return [result]
        except ProtocolError as exc:
            return [self.error(request, exc)]
        except OSError:
            result = self.error(request, ProtocolError("STORAGE_ERROR", "Memory could not be saved"))
            self.disconnect()
            return [result]


class VirtualConnection:
    """Serial-shaped byte interface; poll tick during silence, feed on reads, close on unplug."""

    def __init__(self, device: VirtualKey, clock=time.monotonic):
        device.disconnect()
        self.device = device
        self.clock = clock
        self.reader = JsonLines()

    def close(self):
        self.reader.close()
        self.device.disconnect()

    def _failure(self, exc: ProtocolError) -> bytes:
        result = encode({"v": 1, "type": "ERROR", "code": exc.code, "message": str(exc)})
        self.close()
        return result

    def tick(self) -> bytes:
        if self.reader.closed:
            return b""
        try:
            self.reader.check_timeout(self.clock())
            return b"".join(encode(message) for message in self.device.tick(
                self.clock(), receiving_frame=self.reader.started is not None))
        except ProtocolError as exc:
            return self._failure(exc)

    def feed(self, data: bytes) -> bytes:
        result = self.tick()
        if self.reader.closed:
            return result
        try:
            for request in self.reader.feed(data, self.clock()):
                replies = self.device.receive(request, self.clock())
                result += b"".join(encode(message) for message in replies)
            return result
        except ProtocolError as exc:
            return result + self._failure(exc)

    def approve(self) -> bytes:
        result = self.tick()
        if self.reader.closed:
            return result
        return result + b"".join(encode(message) for message in self.device.approve(self.clock()))

    def cancel(self) -> bytes:
        result = self.tick()
        if self.reader.closed:
            return result
        return result + b"".join(encode(message) for message in self.device.cancel(self.clock()))
