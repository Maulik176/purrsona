"""Serialized USB protocol client and explicit, fingerprint-checked laptop pairing."""
import argparse
import json
import os
import threading
import time
import uuid
from pathlib import Path

import serial
from serial.tools import list_ports

from .key_protocol import (UNLOCK_TIMEOUT, JsonLines, ProtocolError, UnlockChallenge, encode, fingerprint,
                           from_base64, identifier, integer, json_bytes, require, unpack_pull)

ROOT = Path(__file__).resolve().parents[2]


def usb_ports() -> list[str]:
    configured = os.getenv("PURRSONA_USB_PORT")
    if configured:
        return [port.device for port in list_ports.comports() if port.device == configured]
    return sorted(port.device for port in list_ports.comports() if port.vid == 0x303A)


class TrustStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(os.getenv("PURRSONA_TRUST_DIR", ROOT / "private/trusted-keys"))

    def load(self, key_id: str) -> dict | None:
        require(identifier(key_id), "Invalid key ID")
        path = self.directory / f"{key_id}.json"
        if not path.exists():
            return None
        try:
            with path.open("rb") as stream:
                data = stream.read(4097)
            require(len(data) <= 4096, "Invalid trust file", "AUTH_FAILED")
            result = json.loads(data)
            require(isinstance(result, dict) and result.get("key_id") == key_id
                    and integer(result.get("last_counter")), "Invalid trust file", "AUTH_FAILED")
            from_base64(result.get("pk"), 32)
            return result
        except (OSError, ValueError) as exc:
            raise ProtocolError("AUTH_FAILED", "Trusted key could not be read") from exc

    def save(self, record: dict):
        from .virtual_key import durable_write

        require(identifier(record.get("key_id")) and integer(record.get("last_counter")),
                "Invalid trust record", "AUTH_FAILED")
        from_base64(record.get("pk"), 32)
        existing = self.load(record["key_id"])
        require(existing is None or (existing["pk"] == record["pk"]
                and record["last_counter"] >= existing["last_counter"]),
                "Refusing to replace a trusted key or roll back its counter", "AUTH_FAILED")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        durable_write(self.directory / f"{record['key_id']}.json", json_bytes(record))


class UsbKey:
    def __init__(self, port: str, *, transport=None, clock=time.monotonic):
        self.port = port
        self.clock = clock
        self.lock = threading.RLock()
        self.reader = JsonLines()
        self.session: str | None = None
        self.key_id: str | None = None
        self.closed = False
        self.authenticated = False
        if transport is None:
            transport = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=10)
            transport.dtr = False
            transport.rts = False
            transport.port = port
            transport.open()
            # macOS can reset native USB on open. Drain ROM/boot logs before
            # sending any protocol request; later responses remain strict JSON.
            try:
                boot_deadline = self.clock() + 2
                while self.clock() < boot_deadline:
                    transport.read(4096)
            except OSError:
                transport.close()
                raise
        self.transport = transport

    def close(self):
        # Do not wait for the request lock: unplug must interrupt a pending read/write.
        self.closed = True
        self.authenticated = False
        self.session = None
        try:
            self.transport.cancel_read()
            self.transport.cancel_write()
        except (AttributeError, OSError):
            pass
        self.transport.close()

    def request(self, kind: str, *, timeout: float = 10, terminal: str | None = None,
                message: dict | None = None, **payload) -> list[dict]:
        with self.lock:
            require(not self.closed, "USB connection is closed", "LOCKED")
            request = message or {"v": 1, "type": kind, "id": uuid.uuid4().hex, **payload}
            if self.session and "session" not in request:
                request["session"] = self.session
            wire = encode(request)
            deadline = self.clock() + timeout
            try:
                sent = 0
                while sent < len(wire):
                    require(self.clock() < deadline and not self.closed, "USB write timed out", "LOCKED")
                    count = self.transport.write(wire[sent:])
                    require(bool(count), "USB write interrupted", "LOCKED")
                    sent += count
                replies = []
                while self.clock() < deadline and not self.closed:
                    data = self.transport.read(4096)
                    self.reader.check_timeout(self.clock())
                    if not data:
                        continue
                    for reply in self.reader.feed(data, self.clock()):
                        if reply["type"] == "ERROR":
                            raise ProtocolError(reply.get("code", "BAD_REQUEST"),
                                                "The key rejected the request")
                        require(reply.get("reply_to") == request["id"], "Unexpected USB response")
                        if "session" in request:
                            require(reply.get("session") == request["session"], "Stale USB response", "LOCKED")
                        if reply["type"] == "DENIED":
                            raise ProtocolError("AUTH_FAILED", "Button approval was denied or timed out")
                        replies.append(reply)
                    if replies and (terminal is None or replies[-1]["type"] == terminal):
                        require(self.reader.started is None, "Unexpected partial response")
                        return replies
                raise ProtocolError("LOCKED", "USB response timed out; reconnect to check saved memories")
            except (OSError, ProtocolError):
                self.close()
                raise

    def hello(self) -> str:
        reply = self.request("HELLO", timeout=3)
        require(len(reply) == 1 and reply[0]["type"] == "HELLO" and identifier(reply[0].get("key_id")),
                "Invalid HELLO response")
        self.key_id = reply[0]["key_id"]
        return self.key_id

    def unlock(self, trust: TrustStore):
        record = trust.load(self.key_id)
        require(record is not None, "Pair this key before using it", "AUTH_FAILED")
        challenge = UnlockChallenge(self.key_id, "Purrsona-laptop", uuid.uuid4().hex,
                                    issued_at=self.clock())
        replies = self.request("CHALLENGE", timeout=UNLOCK_TIMEOUT, message=challenge.request())
        require(len(replies) == 1, "Invalid unlock response")
        counter = challenge.verify(replies[0], from_base64(record["pk"], 32),
                                   record["last_counter"], now=self.clock())
        trust.save({**record, "last_counter": counter})
        self.session = challenge.session
        self.ack("UNLOCKED")
        self.authenticated = True

    def ack(self, kind: str, **payload) -> dict:
        replies = self.request(kind, **payload)
        require(len(replies) == 1 and replies[0]["type"] == "ACK", "Missing acknowledgement")
        return replies[0]

    def pull(self) -> tuple[dict, list[dict]]:
        request = {"v": 1, "type": "PULL", "id": uuid.uuid4().hex, "session": self.session}
        replies = self.request("PULL", timeout=30, terminal="PULL_DONE", message=request)
        return unpack_pull(replies, request["id"], self.session, self.key_id)

    def append(self, event: dict, event_id: str) -> int:
        reply = self.ack("APPEND", event=event, event_id=event_id)
        require(reply.get("event_id") == event_id and integer(reply.get("count"), 1),
                "Invalid memory acknowledgement")
        return reply["count"]


def pair(port: str, trust: TrustStore):
    key = UsbKey(port)
    try:
        key_id = key.hello()
        print("Press B on the T-Display to pair this laptop.", flush=True)
        replies = key.request("PAIR_BEGIN", laptop_id="Purrsona-laptop", timeout=UNLOCK_TIMEOUT)
        require(len(replies) == 1 and replies[0]["type"] == "PAIR_OK" and
                replies[0].get("key_id") == key_id, "Invalid pairing response")
        reply = replies[0]
        mark = fingerprint(from_base64(reply.get("pk"), 32))
        require(reply.get("fingerprint") == mark, "Fingerprint did not match", "AUTH_FAILED")
        print(f"Laptop fingerprint: {mark}")
        confirmation = input("Type the six characters displayed on the board: ").strip().upper()
        require(confirmation == mark, "Pairing comparison was not confirmed", "AUTH_FAILED")
        previous = trust.load(key_id)
        trust.save({"key_id": key_id, "pk": reply["pk"],
                    "last_counter": previous["last_counter"] if previous else 0})
        print("Paired. Start the API with PURRSONA_USB=1; approve each new connection on the board.")
    finally:
        key.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["ports", "pair"])
    parser.add_argument("--port")
    args = parser.parse_args()
    try:
        ports = usb_ports()
        if args.command == "ports":
            print("\n".join(ports) if ports else "No ESP32 USB port found.")
            return
        port = args.port or (ports[0] if len(ports) == 1 else None)
        require(port is not None, "Connect one board or specify --port")
        pair(port, TrustStore())
    except (ProtocolError, OSError, EOFError) as exc:
        parser.exit(1, f"Pairing failed: {exc}\n")


if __name__ == "__main__":
    main()
