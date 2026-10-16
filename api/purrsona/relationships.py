"""Your own words for people ("my manager", "a customer") and the circle each one belongs to.

The graph answers first: the exact words, or a known word after a harmless modifier ("my new manager"
is a "manager"). Anything else ("cricket teammate" is not a work teammate) costs one small Claude call,
and the answer is saved, so the graph knows it next time.
"""
import json
import re

import anthropic

from . import queries
from .db import rows

MODEL = "claude-opus-5-5"


# Words that don't change who someone is to you.
MODIFIERS = re.compile(r"^(new|old|former|ex|current|future|favourite|favorite|dear|close)\s+")


def normalize(text: str) -> str:
    """'My Manager!' -> 'manager'."""
    t = re.sub(r"['’]", "", text.lower())
    t = re.sub(r"[^\w\s-]", " ", t)
    t = re.sub(r"^\s*(he|she|they|this person)\s+(is|are|was)\s+", "", t)  # "he is my manager"
    t = re.sub(r"^\s*(my|our|a|an|the)\s+", "", t)
    return re.sub(r"\s+", " ", t).strip()[:60]


def resolve(graph, client: anthropic.Anthropic | None, text: str, circles: list[str]) -> dict:
    """Which circle does this word belong to? Returns {name, circle (or None), source}."""
    name = normalize(text)
    known = {r["name"]: r["circle"] for r in rows(graph.ro_query(queries.RELATIONSHIP_WORDS))}
    if name in known:
        return {"name": name, "circle": known[name], "source": "graph"}
    base = MODIFIERS.sub("", name)
    if base in known:
        return {"name": name, "circle": known[base], "source": "graph", "like": base}
    circle, label = classify(client, name, circles) if client else (None, None)
    if label and len(name.split()) > 3:  # a whole sentence becomes a short label: "hackathon partner"
        name = normalize(label)
    graph.query(queries.SAVE_RELATIONSHIP, {"name": name, "circle": circle})
    return {"name": name, "circle": circle, "source": "claude"}


def classify(client: anthropic.Anthropic, name: str, circles: list[str]) -> tuple[str | None, str | None]:
    """One small call for words the graph has never seen: which of your circles fits, if any, and a
    short label for the relationship (1 to 3 words)."""
    schema = {"type": "object",
              "properties": {"circle": {"type": "string", "enum": [*circles, "none"]},
                             "label": {"type": "string", "description": "1 to 3 words, e.g. 'hackathon partner'"}},
              "required": ["circle", "label"], "additionalProperties": False}
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system="You sort the people someone writes to into their circles. Answer with the best circle, "
               "or none if no circle fits (for example a company page or a stranger), and a short label "
               "for who this person is to them, in their words, without 'my'.",
        messages=[{"role": "user", "content": f"Their circles: {', '.join(circles)}.\n"
                                              f"This person is their: {name}\nWhich circle?"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return None, None
    out = json.loads(next(b.text for b in response.content if b.type == "text"))
    return (None if out["circle"] == "none" else out["circle"]), out.get("label")
