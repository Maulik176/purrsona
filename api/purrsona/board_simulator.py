"""Development-only board demo. Run with bun run simulator, never as the production API."""
import base64
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import app as api
from .key_bridge import MemoryBridge
from .simulated_board import SimulatedBoard, TEST_PUBLIC_KEY, screen_png
from .usb_key import TrustStore


def mock_draft(client, post, name, platform, rules=None, **kwargs):
    return "Congratulations on the launch. Wishing the team a strong start."


def mock_refine(client, text, instruction, *args, **kwargs):
    return "Congratulations on the launch. Excited to see what comes next."


def mock_learning(client, draft, sent, person, platform, current, **kwargs):
    # Deliberately fixed fixture, not a claim that a model inferred the user's style.
    return [{"kind": "length", "text": f"Keeps replies to {person.split()[0]} short."}]


class Command(BaseModel):
    command: Literal["connect", "disconnect", "restart", "a", "b", "back", "lose_ack", "fail_storage"]


def create_demo(binary: Path, directory: Path):
    board = SimulatedBoard(binary, directory / "board")
    trust = TrustStore(directory / "trust")
    if trust.load("demo-maulik") is None:
        trust.save({"key_id": "demo-maulik", "pk": TEST_PUBLIC_KEY, "last_counter": 0})
    bridge = MemoryBridge(api.db, usb_enabled=True, trust=trust, ports=board.ports, client_factory=board.client)
    originals = (api.memory, api.state, api.claude, api.write_draft, api.refine_draft,
                 api.learning.learn_rules, api.relationships.resolve)
    api.memory, api.state = bridge, bridge.state
    api.claude = lambda: None
    api.write_draft, api.refine_draft = mock_draft, mock_refine
    api.learning.learn_rules = mock_learning
    api.relationships.resolve = lambda graph, client, text, circles: {
        "name": api.relationships.normalize(text), "circle": None, "source": "mock"}

    @asynccontextmanager
    async def lifespan(app):
        bridge.start()
        try:
            yield
        finally:
            bridge.stop()
            board.close()
            (api.memory, api.state, api.claude, api.write_draft, api.refine_draft,
             api.learning.learn_rules, api.relationships.resolve) = originals

    demo = FastAPI(lifespan=lifespan)
    demo.state.board, demo.state.bridge = board, bridge

    @demo.get("/sim/")
    def screen():
        return FileResponse(Path(__file__).parent / "static/simulator/index.html")

    @demo.get("/sim/state")
    def status():
        frame = board.frame()
        return {"connected": board.connected, "usb": bridge.usb_status,
                "plugged": bridge.state["key_id"] is not None, "screen": frame["screen"],
                "memories": frame["events"], "nodes": frame["nodes"], "lose_ack": board.drop_save_ack,
                "texts": [item["text"] for item in frame["texts"]],
                "image": base64.b64encode(screen_png(frame)).decode()}

    @demo.post("/sim/command")
    def command(request: Command):
        action = request.command
        if action == "connect":
            board.connect()
        elif action in ("disconnect", "restart"):
            board.disconnect()
            bridge.disconnect("disconnected")
            bridge.cleanup()
            bridge.blocked.clear()
            if action == "restart":
                board.power_cycle()
        elif action == "lose_ack":
            board.drop_save_ack = not board.drop_save_ack
        else:
            board.command({"a": "!button-a", "b": "!button-b", "back": "!hold-a",
                           "fail_storage": "!fail-storage"}[action])
        return {"ok": True}

    demo.mount("/", api.app)
    return demo
