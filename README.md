# Purrsona

**Purrsona carries your voice on a key: plug in anywhere; unplug, and the AI forgets.**

Purrsona maps how you write to each person, platform and topic, and shows which edits taught it.

> Work in progress for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), track **Agent Memory and Coordination**.

## How it works (short)
- A browser extension: press **Option+M** (or click the cat) in a reply box, and a draft appears in your voice for that person.
- Your voice lives in a **FalkorDB graph**: people → circles → style rules, platforms and topics, with every rule linked to the edits that taught it. Each draft shows *why* it sounds that way.
- The graph is loaded from a **memory key** (an ESP32) that you plug in and approve with a button press. **Unplug it, and the graph is deleted**; the next draft is generic.

## Run locally
```bash
docker compose up -d   # FalkorDB on localhost:6380, Browser on http://localhost:3001
```
Full setup, the graph data model and the Cypher queries are coming during the build.

## AI assistance
Built with help from Claude Code (Anthropic). The team designs the graph model and queries and reviews all code.
