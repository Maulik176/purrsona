# Working on Purrsona

## 1. Get it running

Follow [Run it locally](README.md#run-it-locally) in the README: Docker (FalkorDB), `uv`, Chrome, and **your own** Anthropic API key in `.env` (never share keys in chat). Then try, in order:

- http://localhost:8765/practice/ : the cat on a made-up feed (⌥M drafts, click the cat to change it, Reply teaches it)
- http://localhost:8765/dashboard/ : the live voice graph, writing rules, what the key learned, the agents' handoffs
- http://localhost:8765/ : the step-by-step demo console
- http://localhost:3001 : FalkorDB Browser (graph `voice:demo-maulik` while the key is plugged in)
- http://localhost:8765/docs : every endpoint

Without an Anthropic key everything runs except drafting and learning (the tests don't need one).

## 2. How we work

- **One branch per issue**, e.g. `mustafa/b1-protocol`, then a pull request to `main` with `Fixes #1` in the description. Small commits.
- **Run the tests before pushing:** `cd api && uv run pytest -q`. They rebuild the `voice:demo-maulik` graph, so plug the key in again afterwards (practice page header, or `POST /key/plug`).
- **Never commit** `.env`, anything in `private/` (the key's memory log can hold real names), or real data exports.
- **Honest authorship:** commits written with an AI assistant end with a `Co-Authored-By:` line naming it; hand-written commits don't. The README's "How we built it" section relies on this.
- Every Cypher query lives in `api/purrsona/queries.py` and gets a test (one "found" and one "nothing found" case).

## 3. Plugging in the real key: what the bridge needs

The virtual key in [`api/purrsona/keys.py`](api/purrsona/keys.py) is the stand-in. The real key replaces **where the data comes from**, nothing else:

| Moment | Today (virtual key) | Real key |
|---|---|---|
| Plug in | `keys.plug(db, key_id)`: reads the **snapshot** `data/seed/<key_id>.json`, builds the graph, then replays the **memory log** `private/keys/<key_id>.events.jsonl` with `apply_event` | `PULL` streams the snapshot (same shape as `data/seed/demo-maulik.json`) **and** the memory log |
| Learn | `keys.append_event(key_id, event)` after every send, returns the memory count | `APPEND` one event, the key acks with the new count |
| Unplug | `keys.unplug(db, key_id)`: `GRAPH.DELETE`; `POST /key/unplug` also sets `state["key_id"] = None` | `BYE`, or the cable is pulled: the bridge does the same two things |

A suggested seam (your call in #1 and #7): a small key store with `snapshot()`, `events()` and `append(event) -> count`, one for files (`VirtualKey`) and one for serial (`UsbKey`), so `app.py` doesn't change.

**The memory log has four event types** (one JSON object per line; these examples use made-up people):

```json
{"type": "learned", "at": 1792170000000, "platform": "linkedin",
 "person": {"id": "kabir-shah", "name": "Kabir Shah", "circle": "Friends", "new": true, "relationship": "college friend"},
 "draft": {"id": "d-1a2b3c4d", "text": "Yo congrats on the new role 🔥", "rule_ids": ["f1", "f3"]},
 "edit": {"id": "e-5e6f7a8b", "before": "Yo congrats on the new role 🔥", "after": "Bhaila congrats on the new role 🔥"},
 "rules": [{"id": "r-9c0d1e2f", "kind": "phrase_use", "text": "Calls Kabir 'bhaila'", "overrides": [], "supersedes": []}],
 "trail": [{"n": 1, "agent": "Drafter", "action": "wrote", "detail": "from 8 rules in your graph", "at": 1792169990000, "ms": 2900}]}
{"type": "sent", "at": 1792171000000, "platform": "linkedin",
 "person": {"id": "priya-nair", "name": "Priya Nair", "circle": "Work", "new": false, "relationship": null},
 "draft": {"id": "d-3b4c5d6e", "text": "Hi Priya, congratulations on the go-live.", "rule_ids": ["w1", "w3"]}, "trail": []}
{"type": "forgot", "at": 1792172000000, "rule_id": "pa1", "rule_text": "Calls Aarav 'bro'"}
{"type": "moved", "at": 1792173000000, "person": {"id": "neha-kulkarni", "name": "Neha Kulkarni"}, "from": "Work", "circle": "Community"}
```

Events are only ever appended, never edited; forgetting a rule and moving someone to another circle are events too. That's what makes an append-only log on the key (plus a hash chain, if there's time) a good fit.

## 4. Where things are

See [Where things are](README.md#where-things-are) in the README, and `docs/GRAPH-MODEL.md` for every label and relationship.
