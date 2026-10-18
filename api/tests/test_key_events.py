"""Real event streams, USB-worker notifications, reconnection and cleanup boundaries."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from purrsona import app as api, keys
from purrsona.key_bridge import MemoryBridge
from purrsona.key_events import KeyEvents


def next_event(lines):
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("Event stream ended before a state arrived")


def test_worker_events_keep_order_reconnect_with_latest_state_and_release_listener():
    async def check():
        hub = KeyEvents()
        hub.publish({"phase": "waiting"})
        stream = hub.stream()
        assert '"waiting"' in await anext(stream)
        def worker():
            for phase in ("awaiting_button", "loading", "connected", "drafting", "forgotten"):
                hub.publish({"phase": phase})
        thread = threading.Thread(target=worker)
        thread.start(); thread.join()
        for phase in ("awaiting_button", "loading", "connected", "drafting", "forgotten"):
            assert f'"{phase}"' in await asyncio.wait_for(anext(stream), 1)
        await stream.aclose()
        assert not hub.listeners
        again = hub.stream()
        assert '"forgotten"' in await anext(again)
        await again.aclose()
    asyncio.run(check())


def test_slow_browser_is_bounded_and_receives_current_state():
    async def check():
        hub = KeyEvents()
        hub.publish({"phase": "connected"})
        stream = hub.stream(heartbeat=0.01)
        await anext(stream)
        for index in range(100):
            hub.publish({"phase": "connected", "revision": index})
        hub.publish({"phase": "forgotten"})
        await asyncio.sleep(0)
        queue = next(iter(hub.listeners))[1]
        assert queue.qsize() <= 32
        while not queue.empty():
            last = await anext(stream)
        assert '"forgotten"' in last
        assert await anext(stream) == ": keep-alive\n\n"
        await stream.aclose()
    asyncio.run(check())


@pytest.fixture
def live_server(monkeypatch):
    bridge = MemoryBridge(api.db, usb_enabled=False)
    monkeypatch.setattr(api, "memory", bridge)
    monkeypatch.setattr(api, "state", bridge.state)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(api.app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=3) as client:
                yield client, bridge
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_two_real_sse_clients_see_consent_activity_saves_and_forgetting(live_server, monkeypatch):
    client, bridge = live_server
    with client.stream("GET", "/key/events") as first, client.stream("GET", "/key/events") as second:
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("text/event-stream")
        assert first.headers["cache-control"] == "no-store"
        streams = [first.iter_lines(), second.iter_lines()]
        for lines in streams:
            assert next_event(lines)["phase"] == "waiting"
        assert client.post("/key/plug", json={"key_id": "demo-maulik"}).status_code == 200
        for lines in streams:
            assert next_event(lines)["phase"] == "loading"
            ready = next_event(lines)
            assert ready["phase"] == "connected" and ready["rules"] == 33
        graph = bridge.graph()
        with bridge.activity(graph, "drafting"):
            for lines in streams:
                assert next_event(lines)["phase"] == "drafting"
        for lines in streams:
            assert next_event(lines)["phase"] == "connected"
        assert bridge.save_event(graph, {"type": "forgot", "at": 123, "rule_id": "pa1"}) == 1
        for lines in streams:
            assert next_event(lines)["phase"] == "learning"
            saved = next_event(lines)
            assert saved["memories"] == 1 and saved["rules"] == 32
            assert next_event(lines)["phase"] == "connected"
        original = keys.unplug
        def unavailable(*args):
            raise OSError("Database unavailable")
        monkeypatch.setattr(keys, "unplug", unavailable)
        bridge.disconnect("disconnected")
        assert not bridge.cleanup()
        for lines in streams:
            locked = next_event(lines)
            assert locked["phase"] == "forgetting" and not locked["plugged"]
            assert locked["key_id"] is None and locked["rules"] == locked["memories"] == 0
        monkeypatch.setattr(keys, "unplug", original)
        assert bridge.cleanup()
        for lines in streams:
            forgotten = next_event(lines)
            assert forgotten["phase"] == "forgotten"
            assert set(forgotten) == {"id", "phase", "plugged", "key_id", "usb", "generation", "revision", "rules", "memories"}
    deadline = time.monotonic() + 2
    while bridge.live.listeners and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not bridge.live.listeners
    with client.stream("GET", "/key/events") as response:
        assert next_event(response.iter_lines())["phase"] == "forgotten"
