"""The Learner agent: turns what you actually sent into new style rules.

Each lesson becomes one event. The event is applied to the voice graph and appended to the key's
log in the same step, and replayed on every plug-in, so the memory lives on the key.
"""
import json
import re
import time
import uuid

import anthropic

from . import queries

MODEL = "claude-opus-5-5"
KINDS = ["tone", "greeting", "signoff", "emoji", "length", "format", "phrase_use", "phrase_avoid"]

LEARNER_SYSTEM = """You study how someone edits AI drafts of their own messages, to learn their personal writing style.
You get the AI's draft and what the person actually sent to one recipient.
List the habits this edit shows about how they write to this particular recipient: nicknames, greetings,
emojis, tone, length, sign-offs, phrases they add or remove.
Only include habits the edit clearly shows, at most 3, and never repeat a habit already in their rules.
Write each as a short instruction that names the recipient by first name, for example
"Calls Kabir 'bhaila'" or "Adds a 🏏 when writing to Kabir about cricket".
Choose the kind carefully. A tone, greeting, sign-off, emoji, length or format rule replaces the
recipient's circle rule of that kind, so use those kinds only for a full replacement (for example
"Opens with 'Bhaila' instead of 'Yo'"). When a habit adds to what they already do, such as a nickname
or one extra emoji for a topic, use phrase_use so the circle's rule still applies.
How they open the message or address the person (a greeting, a name, a nickname at the start, an @handle) is
kind greeting, written as "Opens with '...'", so it replaces the circle's greeting.
If they cut the draft down a lot, that is a length habit: use kind length and give a number, for example
"Keeps replies to Kabir to one short sentence". Describe what they do, not a list of what they removed;
only when they removed one specific phrase, use phrase_avoid with that phrase in quotes.
If the edit only fixes facts or typos, return no rules."""

SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": KINDS}, "text": {"type": "string"}},
                "required": ["kind", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rules"],
    "additionalProperties": False,
}


def learn_rules(client: anthropic.Anthropic, draft: str, sent: str, person_name: str, platform: str,
                current_rules: list[dict], note: str | None = None, asks: list[str] | None = None) -> list[dict]:
    """One Claude call: which personal rules does this edit teach? Returns [{kind, text}].
    note = words the user typed themselves before asking for the draft (strong evidence of their style).
    asks = what they asked the cat to change ("make it shorter"), in order."""
    known = "\n".join(f"- [{r['kind']}] {r['text']}" for r in current_rules) or "- (none yet)"
    user = (f"Recipient: {person_name}\nPlatform: {platform}\n\nAI draft:\n{draft}\n\n"
            f"What they actually sent:\n{sent}\n\nTheir current rules for this recipient:\n{known}")
    if note:
        user += (f"\n\nBefore asking for the draft they typed this note themselves:\n{note}\n"
                 "Their own words in it (nicknames, pet names, phrases) are strong evidence of how they write "
                 "to this recipient, even if the draft already kept them.")
    if asks:
        user += ("\n\nOn the way, they asked the assistant to change the draft: "
                 + "; ".join(f"'{a}'" for a in asks)
                 + "\nRequests about style (length, emojis, formality) are strong evidence of a habit with this "
                   "recipient. Requests about this message's content only (a fact, a date, a topic) are not habits.")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=LEARNER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to learn from this edit.")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["rules"][:3]


# A habit about how the message opens is a greeting, whatever kind the model picked: otherwise the circle's
# greeting ("Hi <first name>") still applies and the Critic sends the new habit back (found Oct 17 with
# "Addresses Kunal by his X handle instead of his first name", saved as phrase_use).
OPENING = re.compile(r"^(opens|starts) with\b|^(addresses|greets)\b.*\binstead of\b", re.IGNORECASE)


def kind_for(kind: str, text: str) -> str:
    return "greeting" if kind in queries.STACKING_KINDS and OPENING.search(text) else kind


def build_event(person_id: str, person_name: str, circle: str | None, person_is_new: bool, platform: str,
                draft_id: str, draft_text: str, sent_text: str, proposals: list[dict],
                current_rules: list[dict], at: int | None = None, rule_ids: list[str] | None = None) -> dict:
    """Package one lesson. Works out which existing rules each new rule outranks."""
    at = at or int(time.time() * 1000)
    winners = {r["kind"]: r for r in current_rules if r["kind"] not in queries.STACKING_KINDS}
    rules = []
    for p in proposals:
        p = {**p, "kind": kind_for(p["kind"], p["text"])}
        old = winners.get(p["kind"])
        overrides = [old["id"]] if old else []
        supersedes = [old["id"]] if old and old["scope"] == "Person" else []
        rules.append({"id": f"r-{uuid.uuid4().hex[:8]}", "kind": p["kind"], "text": p["text"],
                      "overrides": overrides, "supersedes": supersedes})
    return {"type": "learned", "at": at, "platform": platform,
            "person": {"id": person_id, "name": person_name, "circle": circle, "new": person_is_new},
            "draft": {"id": draft_id, "text": draft_text, "rule_ids": rule_ids or []},
            "edit": {"id": f"e-{uuid.uuid4().hex[:8]}", "before": draft_text, "after": sent_text},
            "rules": rules}


def build_sent_event(person_id: str, person_name: str, circle: str | None, person_is_new: bool,
                     platform: str, draft_id: str, draft_text: str, rule_ids: list[str],
                     at: int | None = None) -> dict:
    """You sent the draft unchanged: nothing to learn about style, but still a memory (who, which circle,
    that the draft was right). No Claude call."""
    return {"type": "sent", "at": at or int(time.time() * 1000), "platform": platform,
            "person": {"id": person_id, "name": person_name, "circle": circle, "new": person_is_new},
            "draft": {"id": draft_id, "text": draft_text, "rule_ids": rule_ids}}


def apply_event(graph, event: dict) -> None:
    """Write one memory into the voice graph (used live and when replaying the key's log)."""
    if event["type"] == "forgot":  # you asked to forget a rule: retire it (kept as history, never applied)
        graph.query(queries.SUPERSEDE, {"old_ids": [event["rule_id"]], "at": event["at"]})
        return
    if event["type"] == "moved":  # you moved someone to another circle on the dashboard
        graph.query(queries.MOVE_TO_CIRCLE, {"person_id": event["person"]["id"], "circle": event["circle"]})
        return
    person, at = event["person"], event["at"]
    if person["new"]:
        graph.query(queries.SAVE_PERSON, {"person_id": person["id"], "name": person["name"], "circle": person["circle"],
                                          "kind": "company" if person["id"].startswith("company-") else "person"})
        if person.get("relationship"):  # "my manager": the word joins the graph (if new) and links to them
            graph.query(queries.SAVE_RELATIONSHIP, {"name": person["relationship"], "circle": person["circle"]})
            graph.query(queries.LINK_RELATIONSHIP, {"person_id": person["id"], "name": person["relationship"]})
    rule_ids = event["draft"].get("rule_ids") or []
    if event["type"] == "sent":
        graph.query(queries.SAVE_SENT, {"person_id": person["id"], "platform": event["platform"],
                                        "draft_id": event["draft"]["id"], "text": event["draft"]["text"], "at": at})
        graph.query(queries.LINK_USED, {"draft_id": event["draft"]["id"], "rule_ids": rule_ids})
        queries.save_trail(graph, event["draft"]["id"], event.get("trail", []))  # the agents' handoffs
        return
    edit = event["edit"]
    graph.query(queries.SAVE_EDIT, {"person_id": person["id"], "platform": event["platform"],
                                    "draft_id": event["draft"]["id"], "edit_id": edit["id"],
                                    "before": edit["before"], "after": edit["after"], "at": at})
    graph.query(queries.LINK_USED, {"draft_id": event["draft"]["id"], "rule_ids": rule_ids})
    for r in event["rules"]:
        graph.query(queries.SAVE_LEARNED_RULE, {"person_id": person["id"], "edit_id": edit["id"],
                                                "rule_id": r["id"], "kind": r["kind"], "text": r["text"],
                                                "at": at})
        if r["overrides"]:
            graph.query(queries.LINK_OVERRIDES, {"rule_id": r["id"], "old_ids": r["overrides"]})
        if r["supersedes"]:
            graph.query(queries.SUPERSEDE, {"old_ids": r["supersedes"], "at": at})
    queries.save_trail(graph, event["draft"]["id"], event.get("trail", []))  # after the rules: TAUGHT links them
