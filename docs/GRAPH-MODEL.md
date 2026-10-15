# Purrsona graph model (v1, Oct 15)

One FalkorDB graph per memory key, named `voice:<key_id>`. Plugging the key in builds it; unplugging runs `GRAPH.DELETE`.

## Nodes

| Label | Key property | Other properties | Example |
|---|---|---|---|
| `User` | `id` | `name` | the key's owner |
| `Person` | `id` (profile slug or @handle) | `name`, `headline` | a colleague you reply to |
| `Circle` | `name` | | Friends, Work, Clients |
| `Platform` | `name` | | linkedin, x |
| `Topic` | `name` | | ServiceNow, AI, cricket |
| `StyleRule` | `id` | `kind`, `text`, `created_at`, `superseded_at` | "no emojis" |
| `Phrase` | `text` | | "I hope this finds you well" |
| `Post` | `id` | `text`, `platform`, `at` | one of your own public posts |
| `Draft` | `id` | `text`, `at` | what the Drafter wrote |
| `Edit` | `id` | `before`, `after`, `at` | your correction of a draft |

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
| `(:Person)-[:WRITES_LIKE {shared}]->(:Person)` | derived: people who share rules (input to community detection) |

## Rule precedence

When rules of the same `kind` compete, the narrowest scope wins: **Person (1) → Circle (2) → Topic (3) → Platform (4) → User (5)**. Phrase rules (`phrase_use`, `phrase_avoid`) stack instead of competing. Superseded rules (`superseded_at` set) are kept for history but never applied.

## Design decisions (Maulik, Oct 15)
- Rules hang off four scopes, not only people, so someone new still gets a sensible first draft from their circle, the platform and the topic.
- Every rule points to the edits or posts that taught it (`LEARNED_FROM`), so each draft can show why it sounds the way it does.
- A person-level rule overrides the circle rule with `OVERRIDES`; the circle rule keeps its history.
