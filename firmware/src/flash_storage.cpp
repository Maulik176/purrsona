#include "flash_storage.h"
#include "strict_json.h"
#include "demo_seed.h"
#include <LittleFS.h>
#include <Ed25519.h>
#include <SHA256.h>
#include <bootloader_random.h>
#include <esp_system.h>
#include <mbedtls/base64.h>
#include <cstdio>
#include <set>
#include <unistd.h>

constexpr size_t MAX_LOG_BYTES = 2 * 1024 * 1024;
static std::string base64(const uint8_t *bytes, size_t count) {
    unsigned char output[128]; size_t size = 0;
    if (mbedtls_base64_encode(output, sizeof(output), &size, bytes, count)) return "";
    return std::string(reinterpret_cast<char *>(output), size);
}
static bool syncFile(FILE *file) { return fflush(file) == 0 && fsync(fileno(file)) == 0; }

bool FlashStorage::begin() {
    if (!LittleFS.begin(false, "/littlefs", 10, "littlefs")) { needsFormat = true; return false; }
    if (!preferences.begin("purrsona", false)) return false;
    if (preferences.getBytesLength("private") == 0) {
        bootloader_random_enable();
        esp_fill_random(privateKeyBytes, sizeof(privateKeyBytes));
        bootloader_random_disable();
        if (preferences.putULong64("counter", 0) != 8 ||
            preferences.putBytes("private", privateKeyBytes, sizeof(privateKeyBytes)) != sizeof(privateKeyBytes)) return false;
    } else if (preferences.getBytesLength("private") != sizeof(privateKeyBytes) ||
               !preferences.isKey("counter") ||
               preferences.getBytes("private", privateKeyBytes, sizeof(privateKeyBytes)) != sizeof(privateKeyBytes)) return false;
    savedCounter = preferences.getULong64("counter");
    if (savedCounter > MAX_COUNTER) return false;
    Ed25519::derivePublicKey(publicKeyBytes, privateKeyBytes);
    if (!LittleFS.exists("/snapshot.json")) {
        FILE *file = fopen("/littlefs/snapshot.tmp", "wb");
        if (!file) return false;
        bool written = fwrite(DEMO_SEED, 1, sizeof(DEMO_SEED) - 1, file) == sizeof(DEMO_SEED) - 1;
        bool synced = syncFile(file); bool closed = fclose(file) == 0;
        if (!written || !synced || !closed || rename("/littlefs/snapshot.tmp", "/littlefs/snapshot.json")) return false;
    }
    FILE *snapshotFile = fopen("/littlefs/snapshot.json", "rb");
    if (!snapshotFile) return false;
    int byte;
    while ((byte = fgetc(snapshotFile)) != EOF && snapshotJson.size() <= MAX_LINE) snapshotJson += char(byte);
    bool readOk = !ferror(snapshotFile); fclose(snapshotFile);
    if (!readOk || snapshotJson.size() > 60000 || !StrictJson(snapshotJson).object()) return false;
    ready = recover();
    return ready;
}
bool FlashStorage::recover() {
    FILE *file = fopen("/littlefs/events.jsonl", "a+b");
    if (!file) return false;
    rewind(file);
    std::string line; std::set<std::string> ids;
    size_t complete = 0; bool valid = true; int byte;
    while ((byte = fgetc(file)) != EOF) {
        if (byte != '\n') {
            if (line.size() == MAX_LINE) { valid = false; break; }
            line += char(byte); continue;
        }
        Document record(256 * 1024);
        if (!StrictJson(line).object() || deserializeJson(record, line) || !record["event_id"].is<const char *>() ||
            !validEvent(record["event"])) { valid = false; break; }
        std::string eventId = record["event_id"].as<std::string>();
        if (eventId.empty() || eventId.size() > 64 || !ids.insert(eventId).second) { valid = false; break; }
        for (char c : eventId) if (!isalnum(static_cast<unsigned char>(c)) && c != '.' && c != '_' && c != '-') valid = false;
        if (!valid) break;
        savedEvents.push_back({eventId, canonicalJson(record["event"])});
        complete += line.size() + 1; line.clear();
        if (complete > MAX_LOG_BYTES || savedEvents.size() > 512) { valid = false; break; }
    }
    valid = valid && !ferror(file);
    if (valid && !line.empty()) valid = ftruncate(fileno(file), complete) == 0;
    if (valid) valid = syncFile(file);
    if (fclose(file)) valid = false;
    logBytes = complete;
    return valid;
}
std::string FlashStorage::publicKey() const { return base64(publicKeyBytes, sizeof(publicKeyBytes)); }
std::string FlashStorage::fingerprint() const {
    SHA256 hash; uint8_t digest[32]; char value[7];
    hash.update(publicKeyBytes, sizeof(publicKeyBytes)); hash.finalize(digest, sizeof(digest));
    snprintf(value, sizeof(value), "%02X%02X%02X", digest[0], digest[1], digest[2]);
    return value;
}
bool FlashStorage::advanceCounter() {
    if (!ready || savedCounter >= MAX_COUNTER) return false;
    uint64_t next = savedCounter + 1;
    if (preferences.putULong64("counter", next) != 8) { ready = false; return false; }
    savedCounter = next; return true;
}
std::string FlashStorage::sign(const std::string &message) const {
    uint8_t signature[64];
    Ed25519::sign(signature, privateKeyBytes, publicKeyBytes, message.data(), message.size());
    return base64(signature, sizeof(signature));
}
bool FlashStorage::append(const MemoryEvent &event) {
    Document record(256 * 1024), payload(256 * 1024);
    if (!ready || deserializeJson(payload, event.json)) return false;
    record["event_id"] = event.id; record["event"] = payload.as<JsonVariantConst>();
    std::string line; serializeJson(record, line); line += '\n';
    if (record.overflowed() || logBytes + line.size() > MAX_LOG_BYTES || savedEvents.size() >= 512) return false;
    FILE *file = fopen("/littlefs/events.jsonl", "ab");
    if (!file) { ready = false; return false; }
    bool written = fwrite(line.data(), 1, line.size(), file) == line.size();
    bool synced = syncFile(file); bool closed = fclose(file) == 0;
    if (!written || !synced || !closed) { ready = false; return false; }
    savedEvents.push_back(event); logBytes += line.size(); return true;
}
