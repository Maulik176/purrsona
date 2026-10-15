"""Purrsona bridge API: the browser extension and dashboard talk to this on localhost.

Run from the api/ folder:  uv run uvicorn purrsona.app:app --port 8765
"""
import time
import uuid
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from . import keys  # noqa: E402
from .db import connect, graph_name  # noqa: E402
from .drafter import write_draft  # noqa: E402
from .queries import (graph_summary, people, person_context, record_draft,  # noqa: E402
                      rules_for_context, topics_in)

app = FastAPI(title="Purrsona bridge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db = connect()
state: dict = {"key_id": None}
_client: anthropic.Anthropic | None = None


def claude() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class PlugRequest(BaseModel):
    key_id: str = "demo-maulik"


class DraftRequest(BaseModel):
    person_id: str
    person_name: str
    platform: str
    post_text: str
    circle_hint: str | None = None


@app.get("/key")
def key_status():
    if state["key_id"] is None:
        return {"plugged": False, "key_id": None}
    graph = db.select_graph(graph_name(state["key_id"]))
    return {"plugged": True, "key_id": state["key_id"], **graph_summary(graph)}


@app.get("/people")
def list_people():
    if state["key_id"] is None:
        raise HTTPException(409, "Plug in a key first")
    return people(db.select_graph(graph_name(state["key_id"])))


@app.post("/key/plug")
def plug(req: PlugRequest):
    try:
        info = keys.plug(db, req.key_id)
    except FileNotFoundError:
        raise HTTPException(404, f"No virtual key named {req.key_id}")
    state["key_id"] = req.key_id
    return info


@app.post("/key/unplug")
def unplug():
    if state["key_id"] is None:
        return {"forgotten": True, "note": "no key was plugged in"}
    info = keys.unplug(db, state["key_id"])
    state["key_id"] = None
    return info


@app.post("/draft")
def draft(req: DraftRequest):
    started = time.perf_counter()
    key_id = state["key_id"]
    if key_id is None:
        text = write_draft(claude(), req.post_text, req.person_name, req.platform, rules=None)
        return {"mode": "generic", "draft": text, "rules": [], "person": None,
                "ms": round((time.perf_counter() - started) * 1000)}

    graph = db.select_graph(graph_name(key_id))
    person = person_context(graph, req.person_id)
    circle = person["circle"] or req.circle_hint  # the hint only matters for someone new
    topics = topics_in(graph, req.post_text)
    rules = rules_for_context(graph, req.person_id, req.platform, topics, circle_hint=circle)
    graph_ms = round((time.perf_counter() - started) * 1000)

    text = write_draft(claude(), req.post_text, req.person_name, req.platform, rules)
    draft_id = f"d-{uuid.uuid4().hex[:8]}"
    record_draft(graph, draft_id, text, int(time.time() * 1000), req.person_id, req.platform,
                 topics, [r["id"] for r in rules])
    return {"mode": "voice", "draft": text, "draft_id": draft_id, "rules": rules,
            "person": {**person, "circle": circle}, "topics": topics,
            "graph_ms": graph_ms, "ms": round((time.perf_counter() - started) * 1000)}


# The demo console (served last so the API routes above take precedence).
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="console")
