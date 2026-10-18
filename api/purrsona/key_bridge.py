"""One active memory key, with USB monitoring and guards against work after unplug."""
import hashlib
import logging
import os
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy

from . import keys
from .db import graph_name
from .key_events import KeyEvents
from .key_protocol import ProtocolError, require, validate_graph
from .learning import apply_event
from .queries import graph_summary, graph_view
from .snapshot import build_graph
from .usb_key import TrustStore, UsbKey, usb_ports

log = logging.getLogger(__name__)


def display_text(value, limit: int) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return " ".join(text.split())[:limit]


def display_graph(view: dict, revision: int) -> dict:
    priority = {kind: index for index, kind in enumerate(
        ("User", "Person", "Circle", "StyleRule", "Platform", "Topic", "Relationship", "Draft"))}
    candidates = sorted(view["nodes"], key=lambda node: (priority.get(node["label"], 99), str(node["name"])))
    nodes, mapped, seen = [], {}, set()
    for node in candidates:
        semantic = f"{node['label']}:{node.get('key') or node['name']}"
        if len(semantic.encode()) > 120:
            semantic = f"{node['label']}:{hashlib.sha256(semantic.encode()).hexdigest()}"
        if semantic in seen:
            mapped[node["id"]] = semantic
            continue
        if len(nodes) == 64:
            continue
        seen.add(semantic)
        mapped[node["id"]] = semantic
        nodes.append({"id": semantic, "kind": node["label"],
                      "label": display_text(node["name"], 72) or node["label"],
                      "detail": display_text(node["name"], 512)})
    edges = [{"source": mapped[edge["source"]], "target": mapped[edge["target"]], "type": edge["type"]}
             for edge in view["links"] if edge["source"] in mapped and edge["target"] in mapped]
    packet = {"revision": revision, "nodes": nodes, "edges": edges[:128],
              "truncated": len(mapped) < len(candidates) or len(edges) > 128}
    validate_graph(packet)
    return packet


class SessionGraph:
    def __init__(self, bridge, key_id, token, *, loading=False):
        self.bridge, self.key_id, self.token, self.loading = bridge, key_id, token, loading

    def query(self, *args, **kwargs):
        with self.bridge.lock:
            self.bridge.ensure_current(self)
            return self.bridge.db.select_graph(graph_name(self.key_id)).query(*args, **kwargs)

    def ro_query(self, *args, **kwargs):
        with self.bridge.lock:
            self.bridge.ensure_current(self)
            return self.bridge.db.select_graph(graph_name(self.key_id)).ro_query(*args, **kwargs)


class MemoryBridge:
    def __init__(self, db, *, usb_enabled=None, trust=None, ports=usb_ports, client_factory=UsbKey):
        self.db = db
        self.usb_enabled = os.getenv("PURRSONA_USB") == "1" if usb_enabled is None else usb_enabled
        self.trust = trust or TrustStore()
        self.ports, self.client_factory = ports, client_factory
        self.state = {"key_id": None}
        self.lock = threading.RLock()
        self.save_lock = threading.Lock()
        self.token = 0
        self.client = None
        self.pending_key = None
        self.records = []
        self.screen = "loading"
        self.activities = {}
        self.revision = 0
        self.dirty = False
        self.usb_status = "waiting"
        self.cleanup_keys = set()
        self.blocked = {}
        self.stop_event = threading.Event()
        self.threads = []
        self.rule_count = self.memory_count = self.content_revision = 0
        self.live = KeyEvents()
        self._publish()

    def _publish(self):
        with self.lock:
            key_id = self.state["key_id"]
            phase = self.screen if key_id and self.screen in ("drafting", "learning") else (
                "connected" if key_id else "forgetting" if self.cleanup_keys else
                "forgotten" if self.usb_status in ("ejected", "disconnected", "shutdown", "save_interrupted") else
                self.usb_status)
            self.live.publish({"plugged": key_id is not None, "key_id": key_id, "phase": phase,
                "usb": self.usb_status if self.usb_enabled else None, "generation": self.token,
                "revision": self.content_revision, "rules": self.rule_count, "memories": self.memory_count})

    def changed(self, graph):
        with self.lock:
            self.ensure_current(graph)
            self.content_revision += 1
            self._publish()

    def graph(self, key_id=None) -> SessionGraph:
        with self.lock:
            key_id = key_id or self.state["key_id"]
            require(key_id is not None and key_id == self.state["key_id"],
                    "The memory key is no longer connected", "LOCKED")
            return SessionGraph(self, key_id, self.token)

    def ensure_current(self, graph: SessionGraph):
        expected = self.pending_key if graph.loading else self.state["key_id"]
        require(graph.token == self.token and graph.key_id == expected,
                "The key disconnected; this result was discarded", "LOCKED")

    def events(self, key_id):
        with self.lock:
            self.graph(key_id)
            return deepcopy([record["event"] for record in self.records]) if self.usb_enabled else keys.load_events(key_id)

    @contextmanager
    def activity(self, graph, state):
        with self.lock:
            self.ensure_current(graph)
            self.activities[state] = self.activities.get(state, 0) + 1
            self.screen = "learning" if self.activities.get("learning") else state
            self._publish()
        try:
            yield
            with self.lock:
                self.ensure_current(graph)
        finally:
            with self.lock:
                if graph.token == self.token:
                    self.activities[state] -= 1
                    self.screen = "learning" if self.activities.get("learning") else (
                        "drafting" if self.activities.get("drafting") else "ready")
                    self._publish()

    def save_event(self, graph: SessionGraph, event: dict) -> int:
        # Keep board ACKs, the event cache and graph updates in the same order.
        # This is separate from the session lock so unplug can interrupt a save.
        with self.save_lock:
            return self._save_event(graph, event)

    def _save_event(self, graph: SessionGraph, event: dict) -> int:
        with self.lock:
            self.ensure_current(graph)
            client = self.client if self.usb_enabled else None
        event_id = uuid.uuid4().hex
        try:
            with self.activity(graph, "learning"):
                count = client.append(event, event_id) if client else keys.append_event(graph.key_id, event)
                with self.lock:
                    self.ensure_current(graph)
                    if client:
                        self.records.append({"event_id": event_id, "event": deepcopy(event)})
                        require(count == len(self.records), "Memory count changed unexpectedly", "STORAGE_ERROR")
                    apply_event(graph, event)
                    self.dirty = True
                    self.memory_count = count
                    self.rule_count = graph_summary(graph)["rules"]
                    self.changed(graph)
                return count
        except Exception as exc:
            if client:
                self.disconnect("save_interrupted", expected_client=client)
                raise ProtocolError("STORAGE_ERROR", "Save interrupted; reconnect to check saved memories") from exc
            raise

    def plug_virtual(self, key_id):
        require(not self.usb_enabled, "USB mode: connect the cable and approve on the display", "INVALID_STATE")
        with self.lock:
            if self.state["key_id"]:
                self.unplug()
            self.token += 1
            self.usb_status = "loading"
            self._publish()
            info = keys.plug(self.db, key_id)
            self.state["key_id"] = key_id
            self.usb_status, self.screen = "connected", "ready"
            self.rule_count = graph_summary(self.graph())["rules"]
            self.memory_count = info["memories"]
            self._publish()
            return info

    def reset_virtual(self, key_id):
        require(not self.usb_enabled, "Reset is available only for virtual keys", "INVALID_STATE")
        with self.lock:
            keys.reset_memories(key_id)
            if self.state["key_id"] == key_id:
                self.plug_virtual(key_id)

    def unplug(self):
        with self.lock:
            key_id = self.state["key_id"] or self.pending_key
            client = self.client
            if client:
                self.blocked[client.port] = None
        if client and client.authenticated:
            try:
                client.ack("BYE", timeout=1, reason="eject")
            except (OSError, ProtocolError):
                pass
        self.disconnect("ejected")
        forgotten = self.cleanup()
        if key_id is None:
            return {"forgotten": forgotten, "note": "no key was plugged in"}
        return {"key_id": key_id, "graph": graph_name(key_id), "forgotten": forgotten}

    def disconnect(self, reason, *, expected_client=None):
        with self.lock:
            if expected_client is not None and self.client is not expected_client:
                return
            key_id = self.state["key_id"] or self.pending_key
            if key_id:
                self.cleanup_keys.add(key_id)
            self.token += 1
            self.state["key_id"] = None
            self.pending_key = None
            client, self.client = self.client, None
            self.records.clear()
            self.activities.clear()
            self.dirty = False
            self.screen = "loading"
            self.usb_status = reason
            self.rule_count = self.memory_count = self.content_revision = 0
            self._publish()
        if client:
            client.close()
        log.info("memory_key_disconnected reason=%s", reason)

    def cleanup(self) -> bool:
        with self.lock:
            for key_id in list(self.cleanup_keys):
                try:
                    require(keys.unplug(self.db, key_id)["forgotten"], "Graph cleanup is incomplete")
                    self.cleanup_keys.remove(key_id)
                except Exception:
                    log.warning("memory_key_cleanup_pending")
            self._publish()
            return not self.cleanup_keys

    def attach(self, port):
        client = self.client_factory(port)
        with self.lock:
            self.client = client
            self.usb_status = "connecting"
            self._publish()
        try:
            key_id = client.hello()
            if self.trust.load(key_id) is None:
                self.blocked[port] = key_id
                self.disconnect("needs_pairing")
                return
            self.usb_status = "awaiting_button"
            self._publish()
            client.unlock(self.trust)
            with self.lock:
                require(self.client is client, "Connection was interrupted", "LOCKED")
                self.token += 1
                self.pending_key = key_id
                graph = SessionGraph(self, key_id, self.token, loading=True)
                self.screen = "loading"
                self.usb_status = "loading"
                self._publish()
            snapshot, records = client.pull()
            with self.lock:
                self.ensure_current(graph)
                keys.unplug(self.db, key_id)
            build_graph(graph, snapshot)
            for record in records:
                apply_event(graph, record["event"])
            packet = display_graph(graph_view(graph), 1)
            rule_count = graph_summary(graph)["rules"]
            client.ack("GRAPH_VIEW", **packet)
            client.ack("STATUS", state="ready", memories=len(records), rules=rule_count)
            with self.lock:
                self.ensure_current(graph)
                self.records = records
                self.revision = 1
                self.state["key_id"] = key_id
                self.pending_key = None
                self.screen = "ready"
                self.usb_status = "connected"
                self.dirty = False
                self.rule_count = rule_count
                self.memory_count = len(records)
                self._publish()
            log.info("memory_key_connected memories=%s", len(records))
        except Exception:
            self.blocked[port] = None
            self.disconnect("connection_failed", expected_client=client)
            self.cleanup()
            raise

    def refresh_display(self, client):
        with self.lock:
            if self.client is not client or not client.authenticated:
                return
            screen, count, rule_count = self.screen, len(self.records), self.rule_count
            graph = self.graph() if self.state["key_id"] and self.dirty else None
            if graph:
                self.dirty = False
                self.revision += 1
                revision = self.revision
        if graph:
            packet = display_graph(graph_view(graph), revision)
            client.ack("GRAPH_VIEW", **packet)
        client.ack("STATUS", state=screen, rules=rule_count,
                   **({"memories": count} if screen == "ready" else {}))

    def _discover(self):
        while not self.stop_event.is_set():
            try:
                available = self.ports()
                self.blocked = {port: key for port, key in list(self.blocked.items()) if port in available}
                if self.client is None and self.cleanup() and len(available) == 1:
                    port = available[0]
                    if port not in self.blocked or (self.blocked[port] and self.trust.load(self.blocked[port])):
                        self.attach(port)
                elif len(available) > 1 and self.client is None:
                    self.usb_status = "choose_port"
                    self._publish()
            except Exception:
                log.warning("memory_key_connect_failed")
            self.stop_event.wait(0.5)

    def _monitor(self):
        while not self.stop_event.wait(0.5):
            client = self.client
            if client is None:
                continue
            try:
                if client.port not in self.ports():
                    self.disconnect("disconnected", expected_client=client)
                    self.cleanup()
                elif client.authenticated and client.lock.acquire(blocking=False):
                    try:
                        self.refresh_display(client)
                    finally:
                        client.lock.release()
            except Exception:
                self.blocked[client.port] = None
                self.disconnect("connection_failed", expected_client=client)
                self.cleanup()

    def start(self):
        if not self.usb_enabled:
            return
        self.stop_event.clear()
        for target in (self._discover, self._monitor):
            thread = threading.Thread(target=target, daemon=True, name=target.__name__)
            self.threads.append(thread)
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.disconnect("shutdown")
        for thread in self.threads:
            thread.join(timeout=2)
        self.threads.clear()
        self.cleanup()
