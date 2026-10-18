# Memory key protocol — hackathon draft

**Status: proposed for [issue #1](https://github.com/Maulik176/purrsona/issues/1).**
The Python reference implementation, persistent simulator, T-Display-S3 firmware
and FastAPI USB bridge are implemented. Compilation and interoperability tests
pass. Physical pairing, display, signed unlock, save/restore after USB reconnect,
and laptop graph deletion have also passed with mocked AI. Remaining manual
checks are listed in [hardware setup](../firmware/README.md).
Maulik still needs to agree to the message names, and the document needs to reach
`main` before issue #1 is complete.

## Run the protocol now (no board required)

For the interactive screen and complete API flow, run `bun run simulator` from
the repo root and open **http://127.0.0.1:8876/sim/**. It executes the C++ firmware
protocol and display with simulated hardware, fixed mock AI responses, and a
temporary real FalkorDB. See the [walkthrough](../firmware/README.md#try-the-complete-demo-without-hardware).

For the smaller Python-only protocol demonstration:

From the repository root:

```sh
cd api
uv run python -m purrsona.protocol_demo
```

Expect five `PASS` lines: signed unlock, snapshot/event loading, a durable save
with a duplicate retry, display-state cleanup, and restore after reopening the
key. This simulator automatically supplies button presses and fingerprint
approval; the real device must require a person. It passes actual JSON bytes
through the parser in both directions, including fragmented reads.

Demo state lives in ignored `private/protocol-demo/`: the simulated device has
its own identity, snapshot and log, and `laptop-trust.json` holds only its public
key and last verified counter. Each run saves one synthetic memory. It never
reads or changes the app's existing `private/keys/` logs. The simulator's storage
lock uses POSIX `flock`, so this command targets macOS/Linux.

Run its tests from `api/`:

```sh
uv run pytest tests/test_key_protocol.py -q
```

| Implementation | Responsibility |
|---|---|
| [`key_protocol.py`](../api/purrsona/key_protocol.py) | Bounded JSON framing, request validation, exact signed bytes, unlock verification and complete PULL validation |
| [`virtual_key.py`](../api/purrsona/virtual_key.py) | Consent/session state machine, synced storage, persistent retry IDs, display state and connection expiry |
| [`protocol_demo.py`](../api/purrsona/protocol_demo.py) | A simulated bridge exercising pairing, durable trust counters and the full protocol round trip |

The browser/API keep their virtual-key behavior unless `PURRSONA_USB=1` is set.
The simulator command above does not connect to
FalkorDB, call Claude, open a USB port, render the LCD, or establish real-board
power-loss durability. The real USB path is in `usb_key.py` and `key_bridge.py`.
Its physical save/unplug/reconnect/restore flow has passed on the T-Display-S3;
power interruption during saving remains a separate acceptance check.

## 1. What we are building

The laptop does the thinking. The LilyGO T-Display-S3 stores the memories, asks
for button approval, and shows a small graph. USB-C supplies power and carries
messages. The existing Chrome extension continues to talk to the FastAPI app.

```text
Chrome extension <-> FastAPI bridge <-> FalkorDB + Claude
                           |
                     USB serial worker
                           |
                    LilyGO T-Display-S3
                    snapshot + event log
                    screen + two buttons
```

Use one board, one laptop, and one active key. Reuse the existing Python app and
LilyGO's working display example. A single bounded snapshot message is enough
for the supplied demo; chunking, wireless, and a live 3D engine are outside v1.

The synthetic data is already committed in
[`data/seed/demo-maulik.json`](../data/seed/demo-maulik.json). Provision that file
as `snapshot.json` on the board once; there is no snapshot-upload command in v1.
New memories go into `events.jsonl`. The board keeps both when unplugged.

## 2. Wire format and limits

USB serial carries UTF-8 JSON objects, one per line, terminated by LF (`\n`).
Send compact JSON, with newlines inside strings escaped. Do not print boot logs,
debug text, or private memory contents on this protocol channel.

| Setting | v1 contract |
|---|---|
| Serial configuration | Native USB CDC; bridge opens at 115200, 8-N-1, no flow control |
| Version | Every message contains `"v":1`; reject other versions |
| Maximum line | 65,536 bytes before LF, including the entire JSON envelope |
| Request ID | `id`: 1–64 ASCII letters, digits, `.`, `_`, or `-`; unique per connection |
| Response correlation | `reply_to`: the request's `id`; responses have no `id` |
| Concurrency | One request in flight; finish a `PULL` stream before sending another request |
| HELLO timeout | 3 seconds |
| Pair/unlock consent | Device waits 120 seconds; bridge allows 130 seconds for the response |
| Other requests | 10 seconds for a complete response; `PULL` has a 30-second total deadline |
| Partial frame | 10 seconds from its first byte to LF; discard and close on timeout |

Receivers enforce the byte limit while reading, before parsing JSON. Malformed
JSON, an oversized line, or an unsupported version ends the connection after a
best-effort `ERROR`. Never allocate an unbounded receive buffer.

JSON objects with duplicate fields, non-finite numbers, invalid Unicode, NUL
characters or nesting deeper than 32 levels are malformed. Unknown message types
produce `ERROR` with code `UNKNOWN_TYPE`. Missing required
fields, wrong field types, and invalid enum values produce `BAD_REQUEST` before
any side effect. Unknown extra fields may be ignored. All counters are integers
in the range 0 to 9,007,199,254,740,991.

`key_id` and `laptop_id` use the same character set and length as request IDs.
`session` is 16 random bytes encoded as 32 lowercase hex characters. The bridge
creates a new session for every unlock attempt. Protected messages and their
responses carry it; stale or missing sessions produce `LOCKED`.

```json
{"v":1,"type":"HELLO","id":"hello-1"}
{"v":1,"type":"HELLO","reply_to":"hello-1","key_id":"demo-maulik","fw":"0.1.0","counter":0,"max_line_bytes":65536}
```

`HELLO` is allowed only while locked. Sending it during an unlocked session is
an invalid state transition; start a new connection to reset the session.

## 3. Pair once; approve every connection

Follow the Ed25519 approach in [KEY-AUTH-NOTES.md](KEY-AUTH-NOTES.md). The messages
below fix the wire details that those research notes intentionally left open.

| Request / response | Required payload and behavior |
|---|---|
| `PAIR_BEGIN` → `PAIR_OK` | Request: `laptop_id`. Response: `key_id`, `pk`, `fingerprint`. Locked state only; requires a fresh physical press. |
| `CHALLENGE` → `UNLOCK` | Request: `laptop_id`, `session`, `nonce`. Response: `session`, `counter`, `sig`. Requires a fresh physical press. |
| `CHALLENGE` → `DENIED` | Response: `session`, `reason`: `timeout` or `cancelled`. Stay locked. |
| `PAIR_BEGIN` → `DENIED` | Response: `reason`: `timeout` or `cancelled`; no session exists yet. |
| `UNLOCKED` → `ACK` | Request: `session`. Response: `session`. Accepted only after this device signed the pending challenge following a fresh press. |

1. **Pair:** the board generates and persists an Ed25519 key pair if it does not
   already have one. It shows the laptop label and public-key fingerprint. A
   fresh press of button B approves `PAIR_BEGIN`; button A cancels. `PAIR_OK.pk`
   is the standard Base64 encoding of the raw 32-byte public key. The fingerprint
   is the first six uppercase hex characters of SHA-256 of those raw bytes.
2. **Trust:** the bridge shows that fingerprint and requires the user to compare
   it with the board before saving `{key_id, pk, last_counter: 0}` locally. Never
   silently replace an existing trusted public key or reset its saved counter.
   Repeating pairing returns the same public key; pairing does not unlock data.
3. **Challenge:** on each connection the bridge sends a fresh 32-byte random
   `nonce`, encoded as 64 lowercase hex characters, plus its `laptop_id` and a
   new `session`. The board shows “Press B to unlock”; A cancels. A held button
   from before the prompt is not consent. Only one challenge may be pending.
4. **Sign and verify:** after consent, the board increments and durably saves its
   counter before signing. The bridge verifies the signature using the trusted
   key, its own pending challenge fields, a counter greater than the saved one,
   and the 130-second deadline. It consumes the challenge and durably saves the
   new counter before sending `UNLOCKED`.
5. **Open:** the board accepts `UNLOCKED` only for its signed, unexpired pending
   session and acknowledges it. Only now may `PULL`, `APPEND`, `STATUS`, and
   `GRAPH_VIEW` access personal data. Failure, cancellation, timeout, reboot, or
   disconnect discards the pending session and leaves the device locked.

The exact Ed25519 signed bytes are UTF-8 encoding of these six fields joined
with a single LF, **with no trailing LF**:

```text
MEMKEY-UNLOCK-v1
key_id
laptop_id
session
nonce
counter
```

Use the literal domain string followed by the five field values, in that order.
`counter` is unsigned decimal with no leading zeroes. `sig` is standard Base64
of the raw 64-byte Ed25519 signature.
Neither implementation signs serialized JSON or trusts echoed challenge values.
The device waits up to two minutes for a fresh button press. Its pending session
expires 130 seconds after receiving `CHALLENGE`, leaving time to verify and
acknowledge a press near the end of the consent window.

This authenticates the key to the laptop and records physical consent. It does
not authenticate the laptop cryptographically; `laptop_id` is a display label.
USB payloads are plaintext. Keep the hackathon private key in NVS and leave
eFuses, secure boot, and flash encryption untouched, as the auth notes require.

## 4. Load and save memories

### PULL: rebuild the laptop graph

The bridge sends `PULL` with `id` and `session`. The board responds in this order;
every response includes the same `reply_to` and `session`:

| Response | Required fields |
|---|---|
| `SNAPSHOT` | `snapshot`: the complete object with the same shape as the seed file |
| `EVENT` | `seq`: consecutive 1-based position, `event_id`, `event`: one stored event; zero or more responses |
| `PULL_DONE` | `count`: total number of event records transmitted |

The snapshot's `key_id` must match `HELLO.key_id`. Do not send snapshot fragments
or a filename in place of the object. If it cannot fit in one line, return
`TOO_LARGE`; supporting larger snapshots requires a later protocol version.

The bridge calls `snapshot.build_graph` with the snapshot, then calls
`learning.apply_event` with each inner `event` in order. Event payloads keep the
existing `learned`, `sent`, and `forgot` shapes documented in
[CONTRIBUTING.md](../CONTRIBUTING.md#3-plugging-in-the-real-key-what-the-bridge-needs).
Do not pass the USB envelope to `apply_event`.

Keep personalization disabled until `PULL_DONE`, sequence/count validation, and
graph construction all succeed. An interrupted or invalid load deletes its
partial graph and leaves no active key. Send `STATUS ready` and `GRAPH_VIEW` only
after success. Empty event logs are valid and end with `count: 0`.

### APPEND: save one new memory

The bridge creates an `event_id` once and retains it for retries. It sends the
existing event object inside an `APPEND` request:

```json
{"v":1,"type":"APPEND","id":"save-1","session":"0123456789abcdef0123456789abcdef","event_id":"event-1","event":{"type":"forgot","at":1792172000000,"rule_id":"pa1","rule_text":"Calls Aarav bro"}}
{"v":1,"type":"ACK","reply_to":"save-1","session":"0123456789abcdef0123456789abcdef","event_id":"event-1","count":1}
```

`event_id` has the same syntax as a request ID and must remain unique across the
key's lifetime. The board stores `{event_id, event}` as one record in
`events.jsonl`; the extra wrapper does not change the event schema.

An `ACK` means the record is durably stored. Flush/sync before acknowledging.
After reboot, recover complete records; discard an incomplete final write, and
report `STORAGE_ERROR` for corruption earlier in the log. An acknowledged
record must survive power loss; verify this with the chosen filesystem.

An identical retry of an existing `event_id` returns an `ACK` without appending
again. Compare parsed event contents, not JSON whitespace or property order.
The same ID with different contents produces `EVENT_CONFLICT`. `ACK.count` is
the current total record count. Rebuild the deduplication index from flash on
boot so retries also work after a reconnect.

The bridge reports “Saved” only after `ACK`, then applies the event to the live
graph and refreshes the display. If the graph update fails after storage succeeds,
rebuild from the board before resuming personalization. Lost ACKs have an unknown
outcome: retry with the same `event_id`, never create a replacement ID.

The current API virtual key writes bare events to laptop files. That path is
unchanged. The protocol simulator uses a separate directory with wrapped event
records, so existing memory logs need no migration. The USB bridge unwraps records
before `apply_event`, preserves the existing API's bare-event interface, and keeps
hardware events in memory only on the laptop. The board caps its demo log at 512 events or 2 MiB;
capacity exhaustion returns `STORAGE_ERROR` without ACK.

## 5. Screen, animation, and two buttons

The firmware runs its animation, button handling, and serial parser without
blocking each other. A small shaded ASCII brain animation provides the 3D-looking
effect without a 3D rendering engine.

| Screen state | What causes it |
|---|---|
| “Plug me in” / “Waiting for bridge” | No unlocked session |
| “Press B to unlock” | A pending challenge; show the laptop label |
| Brain animation + “Loading memories” | Unlock succeeds and the bridge is loading the graph |
| “Memory connected” → graph | Real graph load succeeds and a valid graph view arrives |
| “Disconnected” | Clear personal display data and lock when the session ends |

`STATUS` is a protected bridge request with `state` equal to `loading`, `ready`,
`drafting`, `learning`, or `error`. `ready` also carries `memories`, the durable
event count, and requires a completed `PULL` in this session and the current
stored count. The board replies `ACK`. Display errors use a short `message`
(maximum 160 UTF-8 bytes); do not send stack traces or secrets.

`GRAPH_VIEW` is a protected bridge request with an increasing integer `revision`,
`nodes`, `edges`, and `truncated`. Each node has a unique string `id`, `kind`,
`label` (up to 72 UTF-8 bytes), and optional `detail` (up to 512 UTF-8 bytes).
Each edge has `source`, `target`, and `type`; both endpoints must exist in nodes.
Use stable semantic IDs such as `Person:priya-nair`, not FalkorDB internal IDs.

Limit the demo view to 64 nodes and 128 edges, within the overall line limit.
If more exist, select a useful subset and set `truncated: true`; show “Demo view”
on screen so it does not imply the whole graph is visible. Accept only complete,
valid views, replace the previous view atomically, and reply `ACK` with
`revision`. Reject older or repeated revisions with `STALE_VIEW`. Revisions
restart at 1 in each new session; failed writes retry with a new revision.

The bridge sends a view after loading and after each saved graph change. The
board shows a selected node with a few neighbors in fixed positions. Additional
neighbors use local pages. Button navigation needs no USB request:

| Control | Graph behavior |
|---|---|
| Short press A | Move to the next item, including the next page when needed |
| Short press B | Open the selected node or its details |
| Hold A for 700 ms | Go back one level; do not also fire a short press |

The standard T-Display-S3 uses A = GPIO 0 and B = GPIO 14. GPIO 0 also selects
bootloader mode when held during reset. See the
[vendor example](https://github.com/Xinyuan-LilyGO/T-Display-S3) for display and
button initialization. Keep labels readable in the 320 × 170 landscape view.

## 6. Disconnect, timeouts, and errors

`BYE` is a protected request in either direction with `id`, `session`, and
`reason`: `eject` or `shutdown`. The receiver returns `ACK` if the link still
works. Both sides end the session without waiting for an ACK to begin cleanup.
There is no flash erase operation: disconnect preserves the key's memories.

The bridge polls USB availability every 500 ms and also handles serial I/O
errors. On disconnect, immediately disable personalization, clear the active
key, cancel or discard work from that session, and delete its FalkorDB graph.
Target completion within one second of detected cable loss. If deletion fails,
keep personalization disabled and report/retry cleanup rather than showing
“Forgotten”. In-flight drafts or learning must never reactivate an old session.

While unlocked and otherwise idle, the bridge sends the current `STATUS` once
per second as a heartbeat and waits for its `ACK`. Three seconds without a
completed heartbeat ends an idle session. While a request or partial frame is
in flight, use its bounded deadline from section 2 instead. Both sides lock or
disconnect when that deadline expires. A reconnect always needs fresh consent.

On USB disconnect, BYE, or heartbeat expiry, the board clears graph/display
buffers and returns to its locked screen, even if a battery keeps it powered.
Reset button-navigation state and discard any partial frame. The snapshot and
event log remain stored. Deleting the active FalkorDB graph is application-level
forgetting; it does not promise forensic erasure of Docker's persistent volume.

```json
{"v":1,"type":"ERROR","reply_to":"save-1","session":"0123456789abcdef0123456789abcdef","code":"STORAGE_ERROR","message":"Memory could not be saved"}
```

`ERROR` requires `code` and `message` (maximum 160 UTF-8 bytes). Include
`reply_to` when a request ID was parsed, and `session` only for a known pending
or unlocked session. Defined codes are `BAD_REQUEST`, `UNSUPPORTED_VERSION`,
`UNKNOWN_TYPE`, `INVALID_STATE`, `LOCKED`, `TOO_LARGE`, `AUTH_FAILED`,
`EVENT_CONFLICT`, `STORAGE_ERROR`, and `STALE_VIEW`. Errors never imply success
or change durable data; an interrupted APPEND still needs the retry rules above.

## 7. Build order and demo checks

1. **Agree on this document (#1).** Review the names, 64 KiB limit, signed bytes,
   and consent flow with Maulik; merge the agreed contract into `main`.
2. **Bring up the board (#2–#3).** Show text, read both buttons, exchange `HELLO`,
   and implement the framing/parser with locked-state rejection.
3. **Unlock and restore (#4, #5, #7).** Provision the committed seed, pair, verify
   signed consent, pull snapshot/events, and build the existing FalkorDB graph.
4. **Complete the physical demo (#5–#8).** Save with a durable ACK, show the brain
   animation and graph, navigate with both buttons, and clear the laptop graph
   when unplugged. Feed actual key state into the browser for #12.
5. **Verify the whole path.** Plug → approve → graph → draft/edit → saved ACK →
   unplug → reconnect → approve → learned memory restored. Also check denied
   consent, replayed signatures, duplicate APPEND, oversized lines, power loss
   during saving, and a battery-powered board after USB disconnection.

The 1,000-user load test (#11) is separate from getting this one-board demo
working. The required final acceptance evidence is a real unplug/replug demo;
virtual-key behavior alone does not establish hardware persistence.

Implementation references: [pyca Ed25519 signing and verification](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/),
[Python JSON parsing](https://docs.python.org/3/library/json.html), and
[flush plus fsync](https://docs.python.org/3/library/os.html#os.fsync).
`cryptography` follows the repo's existing authentication recommendation; PyNaCl
would also support Ed25519, but would add a different API without helping this
demo. JSON handling and file persistence use Python's standard library.
