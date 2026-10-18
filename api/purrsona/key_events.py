"""Bounded, metadata-only key notifications from USB workers to browser streams."""
import asyncio
import json
import threading


class KeyEvents:
    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = set()
        self.current = {}
        self.sequence = 0

    def publish(self, state):
        with self.lock:
            if state == self.current:
                return
            self.current = dict(state)
            self.sequence += 1
            event = {"id": self.sequence, **self.current}
            for loop, queue in tuple(self.listeners):
                try:
                    loop.call_soon_threadsafe(self._offer, queue, event)
                except RuntimeError:  # the tab's ASGI loop has already shut down
                    self.listeners.discard((loop, queue))

    @staticmethod
    def _offer(queue, event):
        if queue.full():
            # A slow tab needs the latest complete state, not a backlog of old sessions.
            while not queue.empty():
                queue.get_nowait()
        queue.put_nowait(event)

    async def stream(self, heartbeat=15):
        loop, queue = asyncio.get_running_loop(), asyncio.Queue(maxsize=32)
        listener = (loop, queue)
        with self.lock:
            self.listeners.add(listener)
            queue.put_nowait({"id": self.sequence, **self.current})
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                else:
                    yield f"id: {event['id']}\nevent: key\ndata: {json.dumps(event)}\n\n"
        finally:
            with self.lock:
                self.listeners.discard(listener)
