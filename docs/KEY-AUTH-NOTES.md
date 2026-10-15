# Key authentication notes (research, Oct 12)

Research notes for the memory key's login: challenge and signed reply over USB serial. Starting point for the protocol contract; not final. Sources are in the team's research notes.

### HMAC vs a key pair

| | HMAC-SHA256, shared secret | Ed25519 key pair (recommended) | ECDSA P-256 |
|---|---|---|---|
| Device side | Built into mbedTLS (Arduino-ESP32 and ESP-IDF), no extra library | Small library: Monocypher (BSD-2/CC0), rweather Crypto for Arduino (MIT; `Ed25519::sign/verify` checked), espressif/libsodium for ESP-IDF (ISC) | In mbedTLS, but more boilerplate: DER encoding, randomness for every signature |
| Laptop side | Python's built-in `hmac` + `compare_digest` | `cryptography`: `Ed25519PublicKey.from_public_bytes(pk).verify(sig, msg)`, which raises `InvalidSignature` | `cryptography` |
| Pairing | The secret is sent once and stored on **both** sides; anyone who reads the laptop file can fake the key | The key creates its private key and **it never leaves**; the laptop stores only the public key, which isn't a secret | Like Ed25519 |
| Story for judges | Weaker | "The key signs; the laptop only checks" | Same, with more work |

**Recommendation: Ed25519.** Time-box the device side to one hour. If the library fights the toolchain, switch to HMAC with the *same* message layout (only an `alg` field changes). The virtual key (B4) reuses the laptop's Ed25519 code. **Gotcha:** Monocypher's main `crypto_eddsa_*` functions use BLAKE2b and *won't* verify in Python. Use its optional `monocypher-ed25519` file (`crypto_ed25519_*`), or rweather/libsodium, which are standard Ed25519.

### Flow sketch (JSON lines over USB serial)

**Pairing, once:**
1. The laptop sends `PAIR_BEGIN`.
2. If the key has no key pair, it makes one with real randomness on (see below), saves the private key in NVS, and shows a 6-character fingerprint of the public key plus "Press to pair".
3. On the button press, the key replies `PAIR_OK {key_id, pk}`.
4. The laptop shows the same fingerprint. Once Maulik confirms, it saves `{key_id, pk, last_counter: 0}` to a local file.

**Each plug-in:**
1. The laptop sends `HELLO`. The key replies `HELLO {key_id, fw, counter}`.
2. The laptop picks a **fresh 32-byte random nonce** (`secrets.token_bytes(32)`) and sends `CHALLENGE {nonce, laptop_id}`.
3. The key's screen shows "Allow Maulik-MBP? Press button". The press is consent; after a 20 s timeout the key sends `DENIED`.
4. On the press, the key adds 1 to its counter and **saves it before replying**. It signs `"MEMKEY-UNLOCK-v1" ‖ key_id ‖ laptop_id ‖ nonce ‖ counter`, a fixed byte layout, and sends `UNLOCK {counter, sig}`.
5. The laptop **rebuilds the message with its own nonce** (it never trusts an echoed one) and checks the signature against the stored public key. It also checks that `counter > last_counter` and that the reply came within 30 s. It marks the nonce used, saves the counter, and sends `UNLOCKED`.
6. The key accepts `PULL`/`APPEND` only after `UNLOCKED` in the current USB session. A reconnect means a new challenge. `BYE` ends the session.

**Replay protection:**
- A new nonce each time means an old signature can't pass.
- The counter catches a copied or rolled-back key.
- The `"MEMKEY-UNLOCK-v1"` prefix stops an unlock signature from being reused for something else, for example if we later sign log entries (A4) with the same key.

**Unplug (B3):** pyserial raises an error on read when the device vanishes. Also poll the port list every 0.5 s and look for Espressif's USB vendor ID `0x303A`. Either signal triggers `GRAPH.DELETE`. On macOS, use `/dev/cu.usbmodem…`. Opening the port can toggle the DTR/RTS lines and reset some ESP32-S3 boards, so check Mustafa's board and switch them off before opening if needed.

### Open-source building blocks
- Monocypher (BSD-2 or CC0), rweather/arduinolibs Crypto (MIT, last push 2024), espressif/libsodium component (ISC; `idf.py add-dependency "espressif/libsodium^1.0.22~1"`), mbedTLS (built in; for the HMAC fallback), pyserial (BSD-3), pyca/cryptography (Apache-2.0 or BSD).
- For ideas only, since their copyleft licences rule out copying: **pico-fido** (AGPL-3.0; runs on ESP32-S3; uses a physical button to prove a person is present) and **krim404/cdc-badge-os** (GPL-3.0; an ESP32-S3 security key with a display and buttons).
- I found **no small permissive "USB serial challenge–response" example**. The flow above is short enough to write ourselves.

### ESP32-S3 security hardware: production only, leave it alone
These all **burn eFuses, which can't be undone**. Never enable them on Mustafa's only board during the hackathon:
- the **HMAC peripheral** (keys burned into eFuse blocks 4–9 and read-protected)
- the **Digital Signature (DS) peripheral** (RSA only; its key is unlocked by an eFuse HMAC key)
- **NVS encryption** (both schemes need flash encryption or an eFuse HMAC key)
- **flash encryption**, **secure boot**, and **disabling JTAG**
- pico-fido's "Secure Boot / Secure Lock"

The ESP-IDF security examples (`flash_encryption`, `nvs_encryption_hmac`, `hmac_soft_jtag`, `key_manager`, `tee`) are all in this group. Don't run any `espefuse.py burn_*` command.

Consequences for us:
- **The private key sits in plain NVS**, so someone holding the board can read it with esptool. Say so honestly in the README: "production: DS/HMAC peripherals + flash encryption".
- **Randomness:** `esp_random()` is truly random only while Wi-Fi/Bluetooth is on or `bootloader_random_enable()` is active. Turn it on around key generation, then off.
