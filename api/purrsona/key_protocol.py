"""Version 1 wire format shared by the bridge and the virtual memory key."""
import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = 1
MAX_LINE_BYTES = 65_536
MAX_INTEGER = 2**53 - 1
FRAME_TIMEOUT = 10
CONSENT_TIMEOUT = 120
UNLOCK_TIMEOUT = CONSENT_TIMEOUT + 10
IDLE_TIMEOUT = 3
IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
PROTECTED = {"PULL", "APPEND", "STATUS", "GRAPH_VIEW", "BYE"}


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.code = code
        self.fatal = fatal


def require(condition: bool, message: str, code: str = "BAD_REQUEST") -> None:
    if not condition:
        raise ProtocolError(code, message)


def identifier(value) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def integer(value, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= MAX_INTEGER


def hex_string(value, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def text_bytes(value, limit: int) -> bool:
    try:
        return isinstance(value, str) and len(value.encode("utf-8")) <= limit
    except UnicodeEncodeError:
        return False


def json_bytes(value) -> bytes:
    try:
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            require(depth <= 32, "JSON nesting exceeds 32 levels")
            if isinstance(item, str):
                require("\0" not in item, "NUL characters are not supported")
            elif isinstance(item, dict):
                for name, child in item.items():
                    require(isinstance(name, str) and "\0" not in name, "Invalid JSON field name")
                    pending.append((child, depth + 1))
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                          sort_keys=True).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("BAD_REQUEST", "Invalid JSON value") from exc


def _unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON field")
        result[name] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite number")


def parse_object(data: bytes) -> dict:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_fields,
                           parse_constant=_reject_constant)
        require(isinstance(value, dict), "Expected a JSON object")
        json_bytes(value)  # Also reject overflowed floats and unpaired Unicode surrogates.
        return value
    except (ValueError, UnicodeError, RecursionError, ProtocolError) as exc:
        raise ProtocolError("BAD_REQUEST", "Malformed JSON object", fatal=True) from exc


def validate_envelope(message: dict) -> None:
    require(type(message.get("v")) is int and message["v"] == VERSION,
            "Unsupported protocol version", "UNSUPPORTED_VERSION")
    require(isinstance(message.get("type"), str), "Missing message type")
    request, response = "id" in message, "reply_to" in message
    require(not (request and response), "Use id or reply_to, not both")
    require(request or response or message["type"] == "ERROR", "Missing message ID")
    for name in ("id", "reply_to"):
        if name in message:
            require(identifier(message[name]), "Invalid message ID")


def encode(message: dict) -> bytes:
    validate_envelope(message)
    data = json_bytes(message)
    require(len(data) <= MAX_LINE_BYTES, "Message exceeds the line limit", "TOO_LARGE")
    return data + b"\n"


class JsonLines:
    """Incremental, bounded framing. Call check_timeout even when no bytes arrive."""

    def __init__(self):
        self.buffer = bytearray()
        self.started: float | None = None
        self.closed = False

    def close(self):
        self.buffer.clear()
        self.started = None
        self.closed = True

    def check_timeout(self, now: float):
        if self.started is not None and now - self.started >= FRAME_TIMEOUT:
            self.close()
            raise ProtocolError("BAD_REQUEST", "Incomplete frame timed out", fatal=True)

    def feed(self, data: bytes, now: float) -> list[dict]:
        require(not self.closed, "Connection is closed", "INVALID_STATE")
        try:
            self.check_timeout(now)
            messages = []
            offset = 0
            while offset < len(data):
                end = data.find(b"\n", offset)
                stop = len(data) if end < 0 else end
                if self.started is None:
                    self.started = now
                if len(self.buffer) + stop - offset > MAX_LINE_BYTES:
                    raise ProtocolError("TOO_LARGE", "Message exceeds the line limit", fatal=True)
                self.buffer.extend(data[offset:stop])
                if end < 0:
                    break
                message = parse_object(bytes(self.buffer))
                validate_envelope(message)
                messages.append(message)
                self.buffer.clear()
                self.started = None
                offset = end + 1
            return messages
        except ProtocolError as exc:
            self.close()
            exc.fatal = True
            raise


def fields(value, schema: dict) -> None:
    require(isinstance(value, dict), "Expected an object")
    for name, kind in schema.items():
        require(type(value.get(name)) is kind, f"Invalid or missing {name}")


def string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_event(event: dict) -> None:
    fields(event, {"type": str, "at": int})
    require(integer(event["at"]), "Invalid event timestamp")
    require(event["type"] in ("learned", "sent", "forgot"), "Unknown event type")
    if event["type"] == "forgot":
        fields(event, {"rule_id": str})
        if "rule_text" in event:
            fields(event, {"rule_text": str})
        return
    fields(event, {"platform": str, "person": dict, "draft": dict})
    fields(event["person"], {"id": str, "name": str, "new": bool})
    require("circle" in event["person"], "Missing circle")
    for name in ("circle", "relationship"):
        require(event["person"].get(name) is None or isinstance(event["person"][name], str),
                f"Invalid {name}")
    fields(event["draft"], {"id": str, "text": str})
    require(string_list(event["draft"].get("rule_ids", [])), "Invalid rule_ids")
    if event["type"] == "learned":
        fields(event, {"edit": dict, "rules": list})
        fields(event["edit"], {"id": str, "before": str, "after": str})
        for rule in event["rules"]:
            fields(rule, {"id": str, "kind": str, "text": str})
            for name in ("overrides", "supersedes"):
                require(string_list(rule.get(name)), f"Invalid {name}")
    require(isinstance(event.get("trail", []), list), "Invalid trail")
    for step in event.get("trail", []):
        fields(step, {"n": int, "agent": str, "action": str, "detail": str, "at": int, "ms": int})
        require(all(integer(step[name]) for name in ("n", "at", "ms")), "Invalid trail counter")
        for name in ("taught", "flagged"):
            require(string_list(step.get(name, [])), f"Invalid {name}")


def validate_graph(message: dict) -> None:
    fields(message, {"revision": int, "nodes": list, "edges": list, "truncated": bool})
    require(integer(message["revision"], 1), "Invalid graph revision")
    require(len(message["nodes"]) <= 64 and len(message["edges"]) <= 128, "Graph exceeds display limits")
    ids = set()
    for node in message["nodes"]:
        fields(node, {"id": str, "kind": str, "label": str})
        require(bool(node["id"]) and node["id"] not in ids, "Duplicate or empty graph node ID")
        require(text_bytes(node["label"], 72) and text_bytes(node.get("detail", ""), 512),
                "Graph text exceeds display limits")
        ids.add(node["id"])
    for edge in message["edges"]:
        fields(edge, {"source": str, "target": str, "type": str})
        require(edge["source"] in ids and edge["target"] in ids, "Graph edge has a missing endpoint")


def validate_request(message: dict) -> None:
    validate_envelope(message)
    require("id" in message, "Expected a request")
    kind = message["type"]
    require(kind in PROTECTED | {"HELLO", "PAIR_BEGIN", "CHALLENGE", "UNLOCKED"},
            "Unknown message type", "UNKNOWN_TYPE")
    if kind in ("PAIR_BEGIN", "CHALLENGE"):
        require(identifier(message.get("laptop_id")), "Invalid laptop ID")
    if kind in ("CHALLENGE", "UNLOCKED") or kind in PROTECTED:
        require(hex_string(message.get("session"), 32), "Invalid session", "LOCKED")
    if kind == "CHALLENGE":
        require(hex_string(message.get("nonce"), 64), "Invalid nonce")
    elif kind == "APPEND":
        require(identifier(message.get("event_id")), "Invalid event ID")
        validate_event(message.get("event"))
    elif kind == "STATUS":
        require(message.get("state") in ("loading", "ready", "drafting", "learning", "error"),
                "Unknown screen state")
        if message["state"] == "ready":
            require(integer(message.get("memories")), "Invalid memory count")
        require(text_bytes(message.get("message", ""), 160), "Invalid status message")
    elif kind == "GRAPH_VIEW":
        validate_graph(message)
    elif kind == "BYE":
        require(message.get("reason") in ("eject", "shutdown"), "Invalid disconnect reason")


def unpack_pull(messages: list[dict], request_id: str, session: str, key_id: str) -> tuple[dict, list[dict]]:
    """Do not expose a partial snapshot/log to the bridge when a stream is interrupted."""
    require(len(messages) >= 2, "Incomplete PULL stream")
    for message in messages:
        validate_envelope(message)
        require(message.get("reply_to") == request_id and message.get("session") == session,
                "PULL response belongs to another request")
    require(messages[0]["type"] == "SNAPSHOT" and messages[-1]["type"] == "PULL_DONE",
            "Incomplete PULL stream")
    snapshot = messages[0].get("snapshot")
    require(isinstance(snapshot, dict) and snapshot.get("key_id") == key_id,
            "Snapshot belongs to another key")
    records, ids = [], set()
    for expected, message in enumerate(messages[1:-1], 1):
        require(message["type"] == "EVENT" and integer(message.get("seq"), 1)
                and message["seq"] == expected, "Invalid event sequence")
        event_id = message.get("event_id")
        require(identifier(event_id) and event_id not in ids, "Invalid or duplicate event ID")
        validate_event(message.get("event"))
        ids.add(event_id)
        records.append({"event_id": event_id, "event": message["event"]})
    require(integer(messages[-1].get("count")) and messages[-1]["count"] == len(records),
            "PULL count does not match the stream")
    return snapshot, records


def from_base64(value, size: int) -> bytes:
    try:
        require(isinstance(value, str), "Invalid authentication encoding", "AUTH_FAILED")
        raw = base64.b64decode(value, validate=True)
        require(len(raw) == size and base64.b64encode(raw).decode() == value,
                "Invalid authentication encoding", "AUTH_FAILED")
        return raw
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("AUTH_FAILED", "Invalid authentication encoding") from exc


def to_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def fingerprint(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:6].upper()


def signed_bytes(key_id: str, laptop_id: str, session: str, nonce: str, counter: int) -> bytes:
    require(identifier(key_id) and identifier(laptop_id), "Invalid signing identifier")
    require(hex_string(session, 32) and hex_string(nonce, 64), "Invalid challenge")
    require(integer(counter, 1), "Invalid unlock counter")
    return "\n".join(("MEMKEY-UNLOCK-v1", key_id, laptop_id, session, nonce, str(counter))).encode()


@dataclass
class UnlockChallenge:
    """Bridge-side pending challenge. A verification attempt consumes it, even on failure."""

    key_id: str
    laptop_id: str
    request_id: str
    issued_at: float = field(default_factory=time.monotonic)
    session: str = field(default_factory=lambda: secrets.token_hex(16))
    nonce: str = field(default_factory=lambda: secrets.token_hex(32))
    used: bool = False

    def request(self) -> dict:
        message = {"v": VERSION, "type": "CHALLENGE", "id": self.request_id,
                   "laptop_id": self.laptop_id, "session": self.session, "nonce": self.nonce}
        validate_request(message)
        return message

    def verify(self, response: dict, public_key: bytes, last_counter: int, *, now: float) -> int:
        available = not self.used
        self.used = True
        require(available and 0 <= now - self.issued_at < UNLOCK_TIMEOUT,
                "Challenge expired or already used", "AUTH_FAILED")
        validate_envelope(response)
        require(response["type"] == "UNLOCK" and response.get("reply_to") == self.request_id
                and response.get("session") == self.session,
                "Unlock does not match the pending challenge", "AUTH_FAILED")
        counter = response.get("counter")
        require(integer(last_counter) and integer(counter, 1) and counter > last_counter,
                "Unlock counter did not advance", "AUTH_FAILED")
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(
                from_base64(response.get("sig"), 64),
                signed_bytes(self.key_id, self.laptop_id, self.session, self.nonce, counter))
        except (ValueError, InvalidSignature) as exc:
            raise ProtocolError("AUTH_FAILED", "Unlock signature did not verify") from exc
        return counter
