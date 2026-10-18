# Physical key demo evidence

Use **20–30 seconds of the physical key in the main submission video**, with a
**60–90 second full recording** here as supporting evidence. The short segment
shows the hardware; the longer clip shows the save/unplug/restore sequence.

## Current evidence

The existing physical-key checks passed with **mocked AI**. They verified pairing,
graph display, saving a memory, deleting the laptop graph on unplug, and restoring
saved memories after reconnecting. The latest automated suite also accepts the
new `moved` event (moving a person to another circle).

The board now has the latest firmware and seed, with its identity and all three
saved memories preserved. Replaying that data produces **34 active rules across
4 circles**. See the [October 18 acceptance record](key-check-2026-10-18.md).
The physical move check, real-Claude run, and video capture are still pending.
No recording or real-AI pass is claimed until captured and checked.

## Record one complete run

1. Start `bun run hardware-demo --real-ai` with `ANTHROPIC_API_KEY` configured in
   the repo's local `.env`. Open `http://127.0.0.1:8765/practice/`. Start a laptop
   screen recording and a landscape phone video of the board. Keep secrets and
   unrelated windows out of frame.
2. Plug in the LilyGO and press **B (GPIO14)**. Film the brain animation, connected
   state, and rule count. The latest seed includes **Community**, Reddit, Slack,
   Teams, and email; saved edits can change the active rule count.
3. On Priya's practice post, focus the reply box and press **Option+M**. Wait for
   the real draft, edit it, and press **Reply**. Show that a memory was saved.
4. Pull the cable. Show **Forgotten** on the laptop. Reconnect and press **B**;
   show that the saved memory is restored once, with the same rule count.
5. Save the phone clip as `key-phone.mp4` and the screen recording as
   `key-screen.webm` (or `.mp4`). Combine their matching moments into
   `key-demo.mp4`. Review the actual files before adding their links here.

The practice feed uses made-up people and only displays the reply locally.
The real-AI run sends the selected synthetic post, voice rules, and edited reply
to Claude. `bun run hardware-demo` without `--real-ai` is a mock rehearsal and
must be labelled as such. Hosted virtual-key footage is separate from physical
USB evidence.

## Acceptance record

Record the firmware commit, seed hash, observed rule/memory counts, model used,
and pass/fail for the real run. Verify both the laptop's visible state and the
underlying deletion/restoration: a disappearing screen alone does not establish
that FalkorDB forgot the graph. Preserve existing event IDs across reconnects.

Raw flash backups, trust files, API keys, and personal exports belong in ignored
local storage, never in this folder. Keep the shared clips limited to synthetic
demo content. A phone clip is needed to prove what the physical screen showed;
the simulator is not a substitute for it.
