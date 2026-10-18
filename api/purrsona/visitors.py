"""The hosted demo: every visitor gets their own virtual key and their own graph (Oct 18).

Run locally, the bridge has one key: yours. Online (PURRSONA_HOSTED=1), every browser carries a visitor
id (the purrsona_key cookie, or the X-Purrsona-Key header from the extension). Each id gets its own
MemoryBridge, its own memory log and its own graph, voice:v-<id>. Nobody can see, change or unplug
anyone else's key. A key nobody has used for a while is forgotten (GRAPH.DELETE), like pulling it out.
"""
import contextvars
import os
import re
import secrets
import threading
import time
from collections import deque

HOSTED = os.getenv("PURRSONA_HOSTED") == "1"
COOKIE = "purrsona_key"
VALID = re.compile(r"^v-[a-z0-9]{12}$")
MAX_VISITORS = int(os.getenv("PURRSONA_MAX_VISITORS", "300"))
IDLE_SECONDS = int(os.getenv("PURRSONA_IDLE_SECONDS", str(3 * 3600)))
CALLS_PER_HOUR = int(os.getenv("PURRSONA_CALLS_PER_HOUR", "60"))          # each visitor's Claude calls
ALL_CALLS_PER_HOUR = int(os.getenv("PURRSONA_ALL_CALLS_PER_HOUR", "1500"))  # everyone's together
CLAUDE_PATHS = {"/draft", "/refine", "/learn", "/relationship"}

current_bridge = contextvars.ContextVar("purrsona_bridge")
current_id = contextvars.ContextVar("purrsona_visitor")


def new_id() -> str:
    return "v-" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(12))


class Full(Exception):
    """Too many visitors at once, or too many Claude calls this hour."""


class Visitors:
    def __init__(self, make_bridge):
        self.make_bridge = make_bridge
        self.bridges, self.seen, self.calls = {}, {}, {}
        self.all_calls = deque()
        self.lock = threading.Lock()

    def bridge_for(self, visitor: str):
        with self.lock:
            bridge = self.bridges.get(visitor)
            if bridge is None:
                self._forget_idle()
                if len(self.bridges) >= MAX_VISITORS:
                    raise Full("The demo is full right now. Try again in a few minutes.")
                bridge = self.bridges[visitor] = self.make_bridge()
            self.seen[visitor] = time.time()
            return bridge

    def spend(self, visitor: str) -> None:
        """One Claude call: refuse it if this visitor, or everyone together, used up this hour's share."""
        now = time.time()
        with self.lock:
            mine = self.calls.setdefault(visitor, deque())
            for q in (mine, self.all_calls):
                while q and now - q[0] > 3600:
                    q.popleft()
            if len(mine) >= CALLS_PER_HOUR:
                raise Full("You've used this hour's drafts on the demo. Try again later, or run it locally.")
            if len(self.all_calls) >= ALL_CALLS_PER_HOUR:
                raise Full("The demo is busy this hour. Try again later, or run it locally.")
            mine.append(now)
            self.all_calls.append(now)

    def _forget_idle(self) -> None:
        now = time.time()
        for visitor, last in list(self.seen.items()):
            if now - last > IDLE_SECONDS:
                self.bridges.pop(visitor).unplug()  # GRAPH.DELETE: the same as pulling the key out
                del self.seen[visitor]
                self.calls.pop(visitor, None)

    def stop(self) -> None:
        with self.lock:
            for bridge in self.bridges.values():
                bridge.stop()


class CurrentBridge:
    """Stands in for app.py's one MemoryBridge: each request talks to its own visitor's bridge."""
    def __init__(self, hub: Visitors):
        self._hub = hub

    def start(self):
        pass  # virtual keys only: no USB threads to start

    def stop(self):
        self._hub.stop()

    def __getattr__(self, name):
        return getattr(current_bridge.get(), name)


class CurrentState:
    """app.py's state["key_id"], for the current visitor."""
    def __getitem__(self, name):
        return current_bridge.get().state[name]

    def __setitem__(self, name, value):
        current_bridge.get().state[name] = value
