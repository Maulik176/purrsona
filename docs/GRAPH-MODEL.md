# Purrsona graph model (v2, Oct 17)

One FalkorDB graph per memory key, named `voice:<key_id>`. Plugging the key in builds it; unplugging runs `GRAPH.DELETE`.

## Nodes

| Label | Key property | Other properties | Example |
|---|---|---|---|
| `User` | `id` | `name` | the key's owner |
| `Person` | `id` (profile slug, @handle, `company-<slug>`, `reddit-<user>`) | `name`, `headline`, `kind` | a colleague you reply to |
| `Circle` | `name` | | Friends, Work, Clients |
| `Platform` | `name` | | linkedin, x, reddit, slack, teams, email |
| `Topic` | `name` | `keywords` (whole-word matches that mean a post is about it) | ServiceNow ("flow designer", "cmdb"), AI, cricket ("last over", "wicket") |
| `StyleRule` | `id` | `kind`, `text`, `created_at`, `superseded_at`, `applies_to` (`post` for rules that only shape your own posts) | "no emojis" |
| `Phrase` | `text` | | "I hope this finds you well" |
| `Relationship` | `name` | your own word for someone, seeded with common ones and learned as you use new ones | "manager", "customer", "college friend" |
| `Post` | `id` | `text`, `platform`, `at` | one of your own public posts |
| `Draft` | `id` | `text`, `at`, `sent_at`, `edited` | what the Drafter wrote (and whether you sent it, changed or not) |
| `Edit` | `id` | `before`, `after`, `at` | your correction of a draft |
| `Step` | `n` (per draft) | `agent`, `action`, `detail`, `at`, `ms`, `queries` (the Drafter's step: each graph query it ran, with ms and rows) | "Critic · redo · broke Work's 'No emojis'" |

`kind` is one of `tone`, `greeting`, `signoff`, `emoji`, `length`, `format`, `phrase_use`, `phrase_avoid`. Timestamps are epoch milliseconds.

## Relationships

| Pattern | Meaning |
|---|---|
| `(:User)-[:KNOWS]->(:Person)` | people you write to |
| `(:Person)-[:IN_CIRCLE]->(:Circle)` | which circle they're in |
| `(:User\|:Person)-[:INTERESTED_IN]->(:Topic)` | shared topics (Q3) |
| `(scope)-[:HAS_RULE]->(:StyleRule)` | a rule's scope: a `Person`, `Circle`, `Topic`, `Platform` or the `User` |
| `(:StyleRule)-[:LEARNED_FROM]->(:Edit\|:Post)` | provenance: the evidence that taught the rule |
| `(:StyleRule)-[:OVERRIDES]->(:StyleRule)` | a narrower rule beats a broader one, e.g. a person's "emojis OK" over the Work circle's "no emojis" |
| `(:Draft)-[:TO]->(:Person)`, `(:Draft)-[:ON]->(:Platform)`, `(:Draft)-[:ABOUT]->(:Topic)` | a draft's context |
| `(:Draft)-[:USED]->(:StyleRule)` | which rules shaped the draft (the "why" view; PageRank runs over these for the signature style) |
| `(:Edit)-[:OF]->(:Draft)` | the draft an edit corrected |
| `(:Edit)-[:DELETED\|:ADDED]->(:Phrase)` | phrases you remove or add |
| `(:Person)-[:IS]->(:Relationship)-[:PART_OF]->(:Circle)` | "Kabir is my new manager", and a manager is part of Work. A word the graph doesn't know is sorted by one small Claude call and saved, so next time the graph answers |
| `(:Draft)-[:HAS_STEP]->(:Step)-[:NEXT]->(:Step)` | the agents' handoff trail: Drafter → Critic → (one redo) → Critic → Learner |
| `(:Step)-[:FLAGGED]->(:StyleRule)` | the rule the Critic found broken |
| `(:Step)-[:TAUGHT]->(:StyleRule)` | the rule the Learner learned from your send |
| `(:Person)-[:WRITES_LIKE {shared}]->(:Person)` | planned, not built yet: people who share rules (input to community detection) |

## Rule precedence

When rules of the same `kind` compete, the narrowest scope wins: **Person (1) → Circle (2) → Topic (3) → Platform (4) → User (5)**. Phrase rules (`phrase_use`, `phrase_avoid`) stack instead of competing. Superseded rules (`superseded_at` set) are kept for history but never applied.

## Memory on the key

The graph is rebuilt on every plug-in: first the key's snapshot (`data/seed/<key_id>.json` for the virtual key), then every memory in its append-only log, in order (`learned`, `sent` and `forgot` events; see [CONTRIBUTING.md](../CONTRIBUTING.md#3-plugging-in-the-real-key-what-the-bridge-needs)). Each plug-in starts from an empty graph, so every memory is applied exactly once; the trail's steps `MERGE` on their number, so a draft's trail saved live and again from the memory never doubles.

## Design decisions (Maulik, Oct 15)
- Rules hang off four scopes, not only people, so someone new still gets a sensible first draft from their circle, the platform and the topic.
- Every rule points to the edits or posts that taught it (`LEARNED_FROM`), so each draft can show why it sounds the way it does.
- A person-level rule overrides the circle rule with `OVERRIDES`; the circle rule keeps its history.
