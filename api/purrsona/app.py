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

from . import keys, learning  # noqa: E402
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


class LearnRequest(BaseModel):
    draft_id: str
    draft_text: str
    sent_text: str
    person_id: str
    person_name: str
    platform: str
    circle: str | None = None
    note_text: str | None = None


class DraftRequest(BaseModel):
    person_id: str
    person_name: str
    platform: str
    post_text: str
    circle_hint: str | None = None
    user_note: str | None = None


@app.get("/key")
def key_status():
    if state["key_id"] is None:
        return {"plugged": False, "key_id": None}
    graph = db.select_graph(graph_name(state["key_id"]))
    return {"plugged": True, "key_id": state["key_id"], "memories": len(keys.load_events(state["key_id"])),
            **graph_summary(graph)}


@app.get("/people")
def list_people():
    if state["key_id"] is None:
        raise HTTPException(409, "Plug in a key first")
    return people(db.select_graph(graph_name(state["key_id"])))


@app.get("/person")
def get_person(id: str):
    """Does the plugged-in graph know this person? (The cat asks for a circle when it doesn't.)"""
    if state["key_id"] is None:
        return {"plugged": False, "known": False}
    graph = db.select_graph(graph_name(state["key_id"]))
    return {"plugged": True, **person_context(graph, id), "circles": graph_summary(graph)["circles"]}


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
        text = write_draft(claude(), req.post_text, req.person_name, req.platform, rules=None, note=req.user_note)
        return {"mode": "generic", "draft": text, "rules": [], "person": None,
                "ms": round((time.perf_counter() - started) * 1000)}

    graph = db.select_graph(graph_name(key_id))
    person = person_context(graph, req.person_id)
    circle = person["circle"] or req.circle_hint  # the hint only matters for someone new
    topics = topics_in(graph, req.post_text)
    rules = rules_for_context(graph, req.person_id, req.platform, topics, circle_hint=circle)
    graph_ms = round((time.perf_counter() - started) * 1000)

    text = write_draft(claude(), req.post_text, req.person_name, req.platform, rules, note=req.user_note)
    draft_id = f"d-{uuid.uuid4().hex[:8]}"
    record_draft(graph, draft_id, text, int(time.time() * 1000), req.person_id, req.platform,
                 topics, [r["id"] for r in rules])
    return {"mode": "voice", "draft": text, "draft_id": draft_id, "rules": rules,
            "person": {**person, "circle": circle}, "topics": topics,
            "graph_ms": graph_ms, "ms": round((time.perf_counter() - started) * 1000)}


@app.post("/learn")
def learn(req: LearnRequest):
    """You sent something: learn your style from the edit, save it to the graph AND the key."""
    key_id = state["key_id"]
    if key_id is None:
        raise HTTPException(409, "Plug in a key first: there's nowhere to save what Purrsona learns")
    if " ".join(req.sent_text.split()) == " ".join(req.draft_text.split()) and not req.note_text:
        return {"learned": [], "note": "You sent the draft as it was, so there's nothing new to learn.",
                "memories": len(keys.load_events(key_id))}

    graph = db.select_graph(graph_name(key_id))
    person = person_context(graph, req.person_id)
    circle = person["circle"] or req.circle
    if not person["known"] and not circle:
        raise HTTPException(400, f"{req.person_name} is new: which circle are they in?")
    current = rules_for_context(graph, req.person_id, req.platform, topics_in(graph, req.draft_text),
                                circle_hint=circle)

    started = time.perf_counter()
    proposals = learning.learn_rules(claude(), req.draft_text, req.sent_text, req.person_name,
                                     req.platform, current, note=req.note_text)
    event = learning.build_event(req.person_id, req.person_name, circle, not person["known"], req.platform,
                                 req.draft_id, req.draft_text, req.sent_text, proposals, current)
    learning.apply_event(graph, event)        # the laptop's graph learns now...
    memories = keys.append_event(key_id, event)  # ...and the key remembers, in the same step
    return {"learned": event["rules"], "person_added": event["person"]["new"], "edit_id": event["edit"]["id"],
            "memories": memories, "ms": round((time.perf_counter() - started) * 1000)}


@app.post("/key/reset")
def reset_key(req: PlugRequest):
    """Demo helper: wipe what the virtual key learned (back to the seed), then re-plug if plugged."""
    keys.reset_memories(req.key_id)
    if state["key_id"] == req.key_id:
        keys.plug(db, req.key_id)
    return {"key_id": req.key_id, "memories": 0}


# The extension's scripts, served so the practice page can run the cat without installing anything.
app.mount("/ext", StaticFiles(directory=Path(__file__).resolve().parents[2] / "extension"), name="extension")

# The demo console and the practice page (served last so the API routes above take precedence).
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="console")
