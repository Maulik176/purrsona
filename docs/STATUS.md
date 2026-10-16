# Purrsona build status

## Day 1 · Thu Oct 15 (done)

1. **Repo + database:** this private repo; FalkorDB v4.22 in Docker (`docker compose up -d`, port 6380, Browser on 3001) with its data saved on a volume.
2. **Graph model** ([GRAPH-MODEL.md](GRAPH-MODEL.md)): people → circles → style rules. Rules can also hang off a platform, a topic, or you. Every rule links to the edits or posts that taught it (`LEARNED_FROM`). When rules compete, the narrowest scope wins: Person > Circle > Topic > Platform > You. Old rules are superseded, never deleted.
3. **Synthetic voice graph** (`data/seed/demo-maulik.json`): 6 made-up people in 3 circles (Friends, Work, Clients), 2 platforms, 4 topics with keywords, 21 rules, 8 edits, 3 posts. No real people.
4. **Virtual key** (`api/purrsona/keys.py`): plug in = build `voice:demo-maulik` from the key's snapshot (about 40 ms, 57 nodes, 103 links); unplug = `GRAPH.DELETE`, the laptop forgets.
5. **The main query** (`RULES_FOR_CONTEXT` in `api/purrsona/queries.py`): which rules apply when you write to this person, on this platform, about these topics. Someone not in the graph yet inherits their circle's rules.
6. **Drafter agent** (`api/purrsona/drafter.py`, Claude): writes the reply from those rules. Unplugged, it writes a generic reply with no graph at all.
7. **The why:** every draft lists each rule it used, where the rule came from, and how many edits taught it. Drafts are saved with `USED` links to their rules.
8. **Demo console** at http://localhost:8765/: plug in → pick a person → draft → read the why → unplug → draft again (generic), side by side.
9. **13 automated tests** (`cd api && uv run pytest`). Two bugs found while testing and fixed (double-counted evidence; topic matching inside words).

**Not built yet:** authentication between the key and the laptop (#4) · learning from what you send (capture, the Learner #9, write-back to the key) · the Critic agent · the browser extension · the real key.

## Day 2 · Fri Oct 16 (done)

1. **Learning loop:** send → the Learner (one Claude call) turns your edit into rules → saved to the graph *and* the key's memory log in the same step → replayed on every plug-in, so it survives unplugging.
2. **Every send leaves a memory:** sent unchanged = no Claude call, but the person (and their circle, if new) and "the draft was right" are saved; the cat says "you've written to X 3× with me" next time.
3. **Rewrite mode:** type a rough note, then Option+M: your words get polished in your voice, and your own words teach the Learner.
4. **Chrome extension + the cat** (`extension/`): the cat shows only by the box you're typing in, paces while thinking, walks along the draft as it lands; asks the circle for someone new; "why?" lists the rules behind the draft. Works on the practice page and LinkedIn (comments, replies under a comment, your own new post); X untested.
5. **Practice page** `/practice/`: a made-up feed where anyone can try the cat without installing anything.
6. Own-post rules no longer shape replies (`applies_to` on rules). **22 tests.**

**Not built yet:** the Critic · the hold-Option refine chat · the "why this draft" query (Q6, Maulik) · dashboard + "it learns" chart · load test (#11) · the real key (#1–#5) · hosted demo for judges.

## Day 2 · Fri Oct 16 (the original plan)

- **Maulik:** the learning loop for the virtual key ("I sent this" → write the edit back to the key so it survives unplugging) · rewrite mode (your rough note → your voice) · Critic agent · browser extension (practice page first, then LinkedIn comments) · the "why this draft" query · own-post rules shouldn't apply to replies.
- **Mustafa:** #1 protocol contract first → #2 board setup, #3 protocol on the key, #4 Ed25519 login → #9 the Learner.

## Run it locally

```bash
docker compose up -d
cd api && uv sync
# put ANTHROPIC_API_KEY=... in the repo-root .env (it's gitignored; never commit it)
uv run uvicorn purrsona.app:app --port 8765 --reload
# open http://localhost:8765/
```
