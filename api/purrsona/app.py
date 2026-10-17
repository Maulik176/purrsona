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

from . import keys, learning, relationships  # noqa: E402
from .db import connect, graph_name, rows  # noqa: E402
from .drafter import refine_draft, write_draft  # noqa: E402
from .queries import (DRAFT_RULES, graph_summary, people, person_context, record_draft,  # noqa: E402
                      rules_for_context, topics_in, why_this_draft)

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
    relationship: str | None = None  # your own word for someone new ("manager")
    asks: list[str] = []  # what you asked the cat to change on the way ("make it shorter")


class DraftRequest(BaseModel):
    person_id: str = ""
    person_name: str = ""
    platform: str
    post_text: str = ""
    circle_hint: str | None = None
    user_note: str | None = None
    relationship: str | None = None
    mode: str = "reply"  # "reply" to someone, or "post" = your own new post (needs user_note)


class RefineRequest(BaseModel):
    current_text: str
    instruction: str
    person_id: str = ""
    person_name: str = ""
    platform: str
    post_text: str = ""
    circle_hint: str | None = None
    relationship: str | None = None
    mode: str = "reply"


class RelationshipRequest(BaseModel):
    text: str


def addressee(name: str, relationship: str | None) -> str:
    """'Priya Nair (your manager)': the relationship tells Claude a lot about how to write."""
    return f"{name} (your {relationship})" if name and relationship else name


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
    if req.mode not in ("reply", "post"):
        raise HTTPException(400, "mode must be 'reply' or 'post'")
    if req.mode == "post" and not (req.user_note or "").strip():
        raise HTTPException(400, "Type what your post is about first")
    key_id = state["key_id"]
    if key_id is None:
        text = write_draft(claude(), req.post_text, req.person_name, req.platform, rules=None,
                           note=req.user_note, mode=req.mode)
        return {"mode": "generic", "draft": text, "rules": [], "person": None,
                "ms": round((time.perf_counter() - started) * 1000)}

    graph = db.select_graph(graph_name(key_id))
    # A new post has nobody to reply to: only the topic, platform and your own rules apply.
    person = person_context(graph, req.person_id)
    circle = person["circle"] or req.circle_hint  # the hint only matters for someone new
    to = addressee(req.person_name, person["relationship"] or req.relationship)
    topics = topics_in(graph, req.post_text if req.mode == "reply" else req.user_note)
    rules = rules_for_context(graph, req.person_id, req.platform, topics, circle_hint=circle, mode=req.mode)
    graph_ms = round((time.perf_counter() - started) * 1000)

    text = write_draft(claude(), req.post_text, to, req.platform, rules, note=req.user_note, mode=req.mode)
    draft_id = f"d-{uuid.uuid4().hex[:8]}"
    record_draft(graph, draft_id, text, int(time.time() * 1000), req.person_id, req.platform,
                 topics, [r["id"] for r in rules])
    return {"mode": "voice", "draft": text, "draft_id": draft_id, "rules": rules,
            "person": {**person, "circle": circle}, "topics": topics,
            "graph_ms": graph_ms, "ms": round((time.perf_counter() - started) * 1000)}


@app.get("/why/{draft_id}")
def why(draft_id: str):
    """Q6: the graph path behind a draft (for the dashboard and the judges)."""
    if state["key_id"] is None:
        raise HTTPException(409, "Plug in a key first")
    rules = why_this_draft(db.select_graph(graph_name(state["key_id"])), draft_id)
    if not rules:
        raise HTTPException(404, "No draft with that id in the plugged-in graph")
    return {"draft_id": draft_id, "rules": rules}


@app.post("/relationship")
def relationship(req: RelationshipRequest):
    """Someone new, in your own words ("my manager"): which circle is that? The graph answers first."""
    if state["key_id"] is None:
        return {"name": relationships.normalize(req.text), "circle": None, "source": "none"}
    graph = db.select_graph(graph_name(state["key_id"]))
    return relationships.resolve(graph, claude(), req.text, graph_summary(graph)["circles"])


@app.post("/refine")
def refine(req: RefineRequest):
    """The cat's chat: change the draft the way you ask, still in your voice (the graph's rules come along)."""
    started = time.perf_counter()
    rules, to = None, req.person_name
    if state["key_id"] is not None:
        graph = db.select_graph(graph_name(state["key_id"]))
        person = person_context(graph, req.person_id)
        circle = person["circle"] or req.circle_hint
        to = addressee(req.person_name, person["relationship"] or req.relationship)
        topics = topics_in(graph, req.post_text or req.current_text)
        rules = rules_for_context(graph, req.person_id, req.platform, topics, circle_hint=circle, mode=req.mode)
    text = refine_draft(claude(), req.current_text, req.instruction, req.post_text, to,
                        req.platform, rules, mode=req.mode)
    return {"draft": text, "mode": "voice" if rules else "generic",
            "ms": round((time.perf_counter() - started) * 1000)}


@app.post("/learn")
def learn(req: LearnRequest):
    """You sent something, and every send leaves a memory in the graph AND on the key: who you wrote to
    (and their circle, if they're new), plus either the lessons from your edit or "the draft was right"."""
    key_id = state["key_id"]
    if key_id is None:
        raise HTTPException(409, "Plug in a key first: there's nowhere to save what Purrsona learns")
    graph = db.select_graph(graph_name(key_id))
    person = person_context(graph, req.person_id)
    circle = person["circle"] or req.circle
    first = req.person_name.split(" ")[0]
    rule_ids = [r["id"] for r in rows(graph.ro_query(DRAFT_RULES, {"draft_id": req.draft_id}))]
    rel = relationships.normalize(req.relationship) if req.relationship else None
    named = f"{first} ({rel})" if rel else first
    joined = None if person["known"] else (f"{named} is now in your {circle} circle" if circle
                                           else f"I'll remember {named}, no circle")

    if " ".join(req.sent_text.split()) == " ".join(req.draft_text.split()) and not req.note_text and not req.asks:
        event = learning.build_sent_event(req.person_id, req.person_name, circle, not person["known"],
                                          req.platform, req.draft_id, req.draft_text, rule_ids)
        event["person"]["relationship"] = rel
        learning.apply_event(graph, event)
        memories = keys.append_event(key_id, event)
        return {"learned": [], "saved": True, "person_added": event["person"]["new"], "memories": memories,
                "note": f"Sent as-is, so the draft was right. {joined or f'Noted that you wrote to {first}'}."}

    current = rules_for_context(graph, req.person_id, req.platform, topics_in(graph, req.draft_text),
                                circle_hint=circle)

    started = time.perf_counter()
    proposals = learning.learn_rules(claude(), req.draft_text, req.sent_text, req.person_name,
                                     req.platform, current, note=req.note_text, asks=req.asks)
    event = learning.build_event(req.person_id, req.person_name, circle, not person["known"], req.platform,
                                 req.draft_id, req.draft_text, req.sent_text, proposals, current, rule_ids=rule_ids)
    event["person"]["relationship"] = rel
    learning.apply_event(graph, event)        # the laptop's graph learns now...
    memories = keys.append_event(key_id, event)  # ...and the key remembers, in the same step
    return {"learned": event["rules"], "saved": True, "person_added": event["person"]["new"],
            "edit_id": event["edit"]["id"], "memories": memories, "note": joined,
            "ms": round((time.perf_counter() - started) * 1000)}


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
