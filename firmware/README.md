# LilyGO T-Display-S3 memory key

Firmware and the laptop USB bridge are implemented and exercised on a physical
T-Display-S3 on macOS (2026-10-17). Its owner confirmed the animated brain and
graph. Fingerprint pairing, 90 USB greetings, ten messages over 60 KB, and rejection
of unapproved memory reads passed. A demo memory saved through the API returned
exactly once after USB unplug/reconnect and a new signed unlock; the laptop's
FalkorDB graph was deleted while disconnected. AI replies were mocked in this run.
Button navigation on every view, interruption during a write, and battery-powered
disconnect behavior remain manual acceptance checks below.

Target: the standard **LilyGO T-Display-S3**, ESP32-S3R8, 16 MB flash, 8 MB PSRAM,
1.9-inch 170 × 320 ST7789 LCD. This is not the AMOLED or original ESP32 model.

## Try the complete demo without hardware

From the repository root:

```sh
bun run simulator
```

Open **http://127.0.0.1:8876/sim/**. This runs the actual C++ protocol, Ed25519,
navigation and display source on the laptop. The display uses the pinned board
library's bitmap fonts. Drawing primitives are implemented by a desktop adapter;
this is a screen simulation, not an ESP32 instruction-set emulator.

1. Click **Connect cable**, then **B**. The brain animates and the graph opens.
2. Use **A** to select, **B** to open, and hold **A** to go back. Keyboard A/B
   work too; Shift+A is the keyboard shortcut for holding A.
3. Open the practice page. Focus Priya's reply box, press Option+M, change the
   draft to “Congrats Priya!”, and press Reply. One memory is saved.
4. Under **Test a failure**, restart the board, connect and approve again. The
   saved memory returns, including after restarting the simulator command.
5. Enable **Disconnect after the next save**, save another edit, and reconnect.
   The app reports an interrupted save; the board restores the one saved event.

The simulator uses synthetic data, a public test identity, pipe-based USB I/O,
desktop files instead of LittleFS/NVS, and fixed mock AI responses. It makes no
model calls. FalkorDB is real, starts on a temporary local port, and is shut down
when you stop the simulator. Your normal Docker database and physical key trust
store are not used. Simulated board state lives in ignored
`private/board-simulator/`; pass `--state-dir /tmp/my-new-demo` to use a fresh
directory, or `--port 8877` if the default port is occupied. Run one simulator
process per state directory.

## Get the physical demo running

1. **Connect and flash.** Use a USB-C data cable. Close serial monitors and stop
   any API process using the board's port. From the repository root:

   ```sh
   uv tool run --from platformio==6.1.18 pio run --project-dir firmware --target upload
   ```

   This replaces the board's application firmware. It does not burn eFuses or
   intentionally erase NVS or the memory filesystem. If upload cannot find the
   board, hold BOOT (GPIO 0), tap RESET, release BOOT, and retry. If there are
   multiple serial devices, add `--upload-port /dev/cu.usbmodem...` with the
   actual port. `uv run python -m purrsona.usb_key ports` from `api/` lists ESP32
   candidates.

   The laptop drains startup output for two seconds when opening the native USB
   port, because macOS can reset the board on open. Boot logs are discarded only
   before the first request; protocol responses still require valid JSON.

2. **Initialize storage only if prompted.** A fresh board can show “Initialize
   memory”. Hold both buttons for three seconds, then release them to restart.
   This explicitly formats the memory filesystem. It is not an automatic
   recovery step, and the button combination is inactive when storage mounts
   normally. The existing synthetic seed is copied to flash once; there is no
   need to create data or run `uploadfs`.

3. **Pair the laptop once.** From `api/`, before starting the USB-enabled API:

   ```sh
   uv run python -m purrsona.usb_key pair
   ```

   Press **B** on the board. Compare the six-character fingerprint on its screen
   with the laptop, then type those characters into the terminal. The laptop
   saves the public key and counter in ignored `private/trusted-keys/`. Pairing
   never silently replaces an existing trusted identity.

4. **Start FalkorDB and the API.** For the hardware demo without Docker or an AI
   key, run `bun run hardware-demo` from the repo root. It starts a real temporary
   FalkorDB and the USB bridge at `http://127.0.0.1:8765`, with fixed mock AI replies.
   It uses your physical board and normal pairing trust store. Saved memories
   stay on the board after the command stops. Use one bridge process at a time.

   For real AI replies, keep the existing Docker/Chrome setup from the
   [project README](../README.md#try-it). From the repo
   root, run `docker compose up -d`. Then, from `api/`:

   ```sh
   PURRSONA_USB=1 uv run uvicorn purrsona.app:app --port 8765
   ```

   Use one API process/worker. Without `PURRSONA_USB=1`, the app retains its
   existing virtual-key behavior. `PURRSONA_USB_PORT=/dev/cu.usbmodem...` selects
   one port if multiple ESP32 devices are connected. The extension still talks
   to the same API; it needs no USB permissions or new configuration.

5. **Approve and try the demo.** Press **B** for the fresh unlock prompt; it stays
   open for two minutes. The
   ASCII brain animates while memories load. “Memory connected” appears after
   successful graph loading; the graph opens automatically. Open
   `http://localhost:8765/practice/`, draft a message, edit it, and press Reply.
   Unplug, reconnect, approve again, and confirm that the learned memory returns.

## Two-button controls

| Screen | A / GPIO 0 | B / GPIO 14 |
|---|---|---|
| Pair or unlock prompt | Cancel | Approve with a fresh press |
| Graph overview | Select the next node; pages advance automatically | Open selected node |
| Node neighborhood | Select the node itself or a neighbor; pages advance automatically | Open details or navigate to the neighbor |
| Node details | Next text page | Return to graph |

Hold A for 700 ms to go back. A long press does not also trigger “Next”. GPIO 0
is also BOOT when held during reset. Disconnected nodes remain reachable from
the overview. A “DEMO” label means the display view was capped at 64 nodes or
128 edges; the laptop still has the complete loaded graph.

The UI uses the app's dark background, amber accents and teal graph colors.
Serial reads, button debounce and display redraws run in the main loop. Graph
navigation happens locally on the board, so button presses need no round trip.

## What is stored where

| Location | Contents |
|---|---|
| Board LittleFS | `snapshot.json` from the committed demo seed; append-only `events.jsonl` |
| Board NVS | Ed25519 private key and increasing unlock counter |
| Laptop trust directory | Public key and last verified counter; no private key |
| Laptop during a connection | FalkorDB graph and memory cache, cleared on disconnect |

The demo log is capped at 512 events or 2 MiB. A full or failed write gets an
error, never a successful save acknowledgement. The board flushes and syncs a
record before ACK. At startup it recovers an incomplete final record and rejects
corruption in earlier complete records. Saving followed by USB unplug/reconnect
has passed on the board; interruption during a write remains an acceptance check.

The snapshot and event log survive a normal firmware upload and unplugging.
The UI and its personal graph buffer are cleared on USB reset or heartbeat
expiry, including when a battery keeps the board powered. The laptop polls for
port loss every 500 ms, invalidates the session, and deletes its active graph.
Failed deletion is retried with personalization disabled. A late draft cannot
write through a graph handle from an earlier connection.

Authentication uses the [protocol contract](../docs/PROTOCOL.md). This hackathon
build uses plaintext USB and NVS storage. It does not enable secure boot, flash
encryption or any eFuse programming.

## Validate without the board

From the repository root:

```sh
bun run allcheck --force
```

This cleans and compiles firmware, starts a temporary FalkorDB using the official
[FalkorDBLite](https://github.com/FalkorDB/falkordblite) package, and runs all API,
protocol and firmware interoperability tests. It uses a temporary database and
does not alter your Docker database. A C++ compiler is needed for native firmware
tests (Xcode command-line tools on macOS). `uv` downloads the pinned build tools
and libraries on first use; no JavaScript dependencies are installed.

For only the board compile:

```sh
uv tool run --from platformio==6.1.18 pio run --project-dir firmware
```

The binary is `.pio/build/tdisplay_s3/firmware.bin` under this directory. The
native tests compile the same protocol, display and rweather Ed25519 code that
the ESP32 uses. They exercise complete draft/save/reconnect flows against the
API, native-process restarts, lost ACKs, storage errors, concurrent saves,
screen transitions and button navigation. Their fake storage and simulated
button presses do not establish physical flash, USB timing, LCD or button behavior.
The actual Arduino main loop is also tested with controlled time advancing during
request processing, timer wraparound, and a blocked USB output buffer. This covers
the hardware-discovered bug where a new reply was mistaken for an expired one.

## Board acceptance checklist

1. Pair fingerprints match; cancel and timeout reveal no memories.
2. Approve, see the brain animation, then see the graph after loading succeeds.
3. Use A/B to open nodes on multiple pages, read details, and go back.
4. Save a lesson, remove all power, reconnect, and confirm it reappears once.
5. Unplug during drafting or saving; check the laptop becomes generic and the
   battery-powered display clears. Reconnect to check any save whose ACK was lost.

## Library choices

The build pins LilyGO's ESP32 platform baseline and TFT_eSPI's standard
[Setup206 pin mapping](https://github.com/Bodmer/TFT_eSPI/blob/master/User_Setups/Setup206_LilyGo_T_Display_S3.h).
TFT_eSPI supplies the small drawing API used here; LVGL would add a widget system
we do not need for these screens. ArduinoJson handles the bounded messages, with
strict validation before decoding. rweather Crypto supplies standard Ed25519,
checked against Python's `cryptography` implementation. pySerial is the laptop
transport. See the [LilyGO examples](https://github.com/Xinyuan-LilyGO/T-Display-S3)
and [authentication notes](../docs/KEY-AUTH-NOTES.md) for the underlying choices.
