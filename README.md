# Purrsona

**Purrsona carries your voice on a key: plug in anywhere; unplug, and the AI forgets.**

Purrsona maps how you write to each person, platform and topic, and shows which edits taught it.

> Work in progress for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), track **Agent Memory and Coordination**.

## How it works (short)
- A browser extension: press **Option+M** (or click the cat) in a reply box, and a draft appears in your voice for that person.
- Your voice lives in a **FalkorDB graph**: people → circles → style rules, platforms and topics, with every rule linked to the edits that taught it. Each draft shows *why* it sounds that way.
- The graph is loaded from a **memory key** (an ESP32) that you plug in and approve with a button press. **Unplug it, and the graph is deleted**; the next draft is generic.

## Run it locally (about 10 minutes)

You need: **Docker Desktop**, **[uv](https://docs.astral.sh/uv/getting-started/installation/)** (it installs Python 3.12 for you), **Chrome**, and **your own Anthropic API key** (each draft costs about 1–2 cents).

```bash
git clone https://github.com/Maulik176/purrsona.git
cd purrsona
docker compose up -d                 # FalkorDB on localhost:6380 (graph browser on http://localhost:3001)
cp .env.example .env                 # then put your key after ANTHROPIC_API_KEY= (.env is gitignored)
cd api
uv sync
uv run uvicorn purrsona.app:app --port 8765 --reload
```

Then try it in this order:
1. **Console**, http://localhost:8765/: plug in the virtual key → pick a person → draft → read the *why* → teach it an edit → unplug (the graph is deleted, the next draft is generic) → plug back in (what it learned comes back).
2. **Practice page**, http://localhost:8765/practice/: a made-up feed. Click into a reply box and the cat sits by it. Press **Option+M** (Alt+M on Windows/Linux) or click the cat. Kabir is new: the cat asks who he is to you. Change the draft and press Reply: the cat learns from what you sent. Send it unchanged and it still remembers who you wrote to.
3. **Chrome extension** (LinkedIn): `chrome://extensions` → turn on Developer mode → **Load unpacked** → pick the `extension/` folder. Refresh LinkedIn, click a comment box, press Option+M. Purrsona never posts for you. After changing extension code, press the ↻ on its card and refresh the page.
4. **Tests**: `cd api && uv run pytest`. They plug and unplug the same demo graph, so don't run them during a demo, and plug the key in again afterwards.

The key's memory (what it learned) is a local file in `private/keys/` (gitignored). The bridge forgets the plugged key whenever it restarts: plug in again from the console or the practice page.

## Where things are
- `api/purrsona/queries.py`: every Cypher query the product depends on (`RULES_FOR_CONTEXT`, `PERSON_CONTEXT`, the save queries).
- `api/purrsona/keys.py`: the virtual key (plug in = build the graph from the snapshot and replay the memory log; unplug = `GRAPH.DELETE`). The real ESP32 key replaces this behind the same calls.
- `api/purrsona/drafter.py` (writes the reply) · `learning.py` (the Learner: turns what you sent into rules) · `app.py` (the bridge API).
- `extension/`: `cat.js` (the cat), `content.js` (boxes, Option+M, site adapters, learning on send), `background.js` (the only part that calls the bridge).
- `data/seed/demo-maulik.json`: the demo voice, made-up people only. `docs/GRAPH-MODEL.md`: labels, relationships and rule precedence.

## AI assistance
Built with help from Claude Code (Anthropic). The team designs the graph model and queries and reviews all code.
