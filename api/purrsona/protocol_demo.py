"""Run the protocol without USB, FalkorDB or Claude: python -m purrsona.protocol_demo."""
import argparse
import time
import uuid
from contextlib import closing
from pathlib import Path

from .key_protocol import (JsonLines, ProtocolError, UnlockChallenge, encode, fingerprint,
                           from_base64, integer, json_bytes, require, unpack_pull)
from .virtual_key import KeyStorage, VirtualConnection, VirtualKey, durable_write, read_object

ROOT = Path(__file__).resolve().parents[2]


class DemoBridge:
    """An in-process serial stand-in. Both directions still pass through real byte framing."""

    def __init__(self, connection: VirtualConnection, trust_path: Path):
        self.connection = connection
        self.trust_path = trust_path
        self.reader = JsonLines()
        self.session: str | None = None
        self.key_id: str | None = None

    def responses(self, data: bytes) -> list[dict]:
        result = []
        for offset in range(0, len(data), 43):
            result.extend(self.reader.feed(data[offset:offset + 43], time.monotonic()))
        for message in result:
            if message["type"] == "ERROR":
                raise ProtocolError(message["code"], message["message"])
        return result

    def send(self, message: dict) -> list[dict]:
        data, result = encode(message), []
        for offset in range(0, len(data), 37):
            result.extend(self.responses(self.connection.feed(data[offset:offset + 37])))
        return result

    def request(self, kind: str, **payload) -> tuple[str, list[dict]]:
        request_id = uuid.uuid4().hex
        message = {"v": 1, "type": kind, "id": request_id, **payload}
        if self.session:
            message["session"] = self.session
        replies = self.send(message)
        require(all(reply.get("reply_to") == request_id for reply in replies), "Unexpected response ID")
        return request_id, replies

    def unlock(self):
        _, replies = self.request("HELLO")
        require(len(replies) == 1 and replies[0]["type"] == "HELLO", "Missing HELLO")
        self.key_id = replies[0]["key_id"]
        if not self.trust_path.exists():
            pair_id, replies = self.request("PAIR_BEGIN", laptop_id="protocol-demo")
            require(not replies, "Pairing did not wait for consent")
            paired = self.responses(self.connection.approve())[0]
            public_key = from_base64(paired["pk"], 32)
            require(paired["type"] == "PAIR_OK" and paired["reply_to"] == pair_id and
                    paired["key_id"] == self.key_id and paired["fingerprint"] == fingerprint(public_key),
                    "Pairing response did not match")
            # This is deliberately simulated consent, not a replacement for the real comparison UI.
            require(public_key == self.connection.device.storage.public_key, "Fingerprint comparison failed")
            durable_write(self.trust_path, json_bytes({"key_id": self.key_id, "pk": paired["pk"],
                                                       "last_counter": 0}))
        trust = read_object(self.trust_path)
        require(trust.get("key_id") == self.key_id and integer(trust.get("last_counter")),
                "Invalid trusted key record", "AUTH_FAILED")
        challenge = UnlockChallenge(self.key_id, "protocol-demo", uuid.uuid4().hex)
        require(not self.send(challenge.request()), "Unlock did not wait for consent")
        signed = self.responses(self.connection.approve())[0]
        counter = challenge.verify(signed, from_base64(trust.get("pk"), 32),
                                   trust["last_counter"], now=time.monotonic())
        durable_write(self.trust_path, json_bytes({**trust, "last_counter": counter}))
        self.session = challenge.session
        _, replies = self.request("UNLOCKED")
        require(len(replies) == 1 and replies[0]["type"] == "ACK" and
                replies[0].get("session") == self.session, "Unlock acknowledgement missing")

    def pull(self):
        request_id, messages = self.request("PULL")
        return unpack_pull(messages, request_id, self.session, self.key_id)

    def disconnect(self):
        self.request("BYE", reason="eject")
        self.connection.close()
        self.session = None


def run_demo(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    seed = ROOT / "data/seed/demo-maulik.json"
    trust = directory / "laptop-trust.json"
    event_id = uuid.uuid4().hex
    event = {"type": "sent", "at": int(time.time() * 1000), "platform": "linkedin",
             "person": {"id": "priya-nair", "name": "Priya Nair", "circle": "Work", "new": False},
             "draft": {"id": f"d-{event_id}", "text": "Congratulations on the launch!", "rule_ids": []}}
    print("Protocol simulator: button presses and fingerprint approval are simulated.")
    with closing(KeyStorage(directory / "device", seed)) as storage:
        connection = VirtualConnection(VirtualKey(storage))
        bridge = DemoBridge(connection, trust)
        bridge.unlock()
        print("1/5 PASS: paired and verified a fresh Ed25519 unlock")
        snapshot, records = bridge.pull()
        print(f"2/5 PASS: loaded the demo snapshot and {len(records)} saved memories")
        _, replies = bridge.request("APPEND", event_id=event_id, event=event)
        require(replies[0]["type"] == "ACK" and replies[0].get("event_id") == event_id,
                "Save acknowledgement missing")
        count = replies[0]["count"]
        _, duplicate = bridge.request("APPEND", event_id=event_id, event=event)
        require(duplicate[0].get("count") == count, "Retry duplicated a memory")
        print("3/5 PASS: saved one memory; retry did not duplicate it")
        bridge.request("STATUS", state="ready", memories=count)
        user = snapshot["user"]
        bridge.request("GRAPH_VIEW", revision=1, truncated=True,
                       nodes=[{"id": f"User:{user['id']}", "kind": "User", "label": user["name"]}], edges=[])
        require(connection.device.graph is not None, "Graph view was not accepted")
        bridge.disconnect()
        require(connection.device.graph is None and connection.device.status is None,
                "Disconnect retained display data")
        print("4/5 PASS: accepted display packets; disconnect cleared screen state")

    with closing(KeyStorage(directory / "device", seed)) as reopened:
        bridge = DemoBridge(VirtualConnection(VirtualKey(reopened)), trust)
        bridge.unlock()
        _, records = bridge.pull()
        require(len(records) == count and any(record["event_id"] == event_id for record in records),
                "Saved memory did not survive reopening")
        _, duplicate = bridge.request("APPEND", event_id=event_id, event=event)
        require(duplicate[0].get("count") == count, "Retry after restart duplicated a memory")
        bridge.disconnect()
    print("5/5 PASS: reopened storage, unlocked again, and restored the saved memory")
    print(f"Demo files: {directory}")
    print("USB hardware, LCD rendering and the FastAPI/FalkorDB integration are not exercised here.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "private/protocol-demo",
                        help="Isolated simulator storage; never the API's existing private/keys directory")
    args = parser.parse_args()
    try:
        run_demo(args.state_dir)
    except (ProtocolError, OSError) as exc:
        code = exc.code if isinstance(exc, ProtocolError) else "STORAGE_ERROR"
        parser.exit(1, f"Protocol demo failed: {code}. Check storage and rerun the protocol tests.\n")


if __name__ == "__main__":
    main()
