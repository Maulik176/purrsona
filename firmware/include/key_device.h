#pragma once
#include <ArduinoJson.h>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

constexpr size_t MAX_LINE = 65536;
constexpr uint32_t CONSENT_TIMEOUT_MS = 120000;
constexpr uint32_t UNLOCK_TIMEOUT_MS = CONSENT_TIMEOUT_MS + 10000;
constexpr uint64_t MAX_COUNTER = 9007199254740991ULL;

#ifdef ESP32
#include <esp_heap_caps.h>
struct JsonAllocator {
    void *allocate(size_t n) { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
    void deallocate(void *p) { heap_caps_free(p); }
    void *reallocate(void *p, size_t n) { return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT); }
};
using Document = BasicJsonDocument<JsonAllocator>;
#else
using Document = DynamicJsonDocument;
#endif

struct MemoryEvent { std::string id, json; };

class DeviceStorage {
public:
    virtual ~DeviceStorage() = default;
    virtual bool healthy() const = 0;
    virtual const std::string &snapshot() const = 0;
    virtual const std::vector<MemoryEvent> &events() const = 0;
    virtual std::string publicKey() const = 0;
    virtual std::string fingerprint() const = 0;
    virtual uint64_t counter() const = 0;
    virtual bool advanceCounter() = 0;
    virtual std::string sign(const std::string &message) const = 0;
    virtual bool append(const MemoryEvent &event) = 0;
};

bool validEvent(JsonVariantConst event);
std::string canonicalJson(JsonVariantConst value);

class KeyDevice {
    DeviceStorage &store;
    std::function<void(const std::string &)> send;
    std::string incoming, pendingId, pendingKind, nonce, pendingSession;
    uint32_t frameAt = 0, pendingAt = 0, lastActivity = 0;
    bool frameStarted = false, signedPending = false, pulled = false, poisoned = false;
    void receive(const std::string &line, uint32_t now);
    void reply(Document &document, const std::string &type, const std::string &id, const std::string &requestSession = "");
    void emit(Document &document);
    void error(const std::string &code, const std::string &id = "", const std::string &requestSession = "");
public:
    std::string keyId, session, laptop, screen = "locked", displayMessage, pairingFingerprint;
    Document graph{128 * 1024};
    uint32_t loadingAt = 0, graphChanged = 0;
    bool graphReceived = false;
    uint64_t ruleCount = 0;
    bool rulesKnown = false;
    KeyDevice(DeviceStorage &storage, std::function<void(const std::string &)> output);
    void feed(const char *bytes, size_t size, uint32_t now);
    void tick(uint32_t now);
    void approve(uint32_t now);
    void cancel();
    void disconnect();
    void transmissionComplete(uint32_t now) { if (!session.empty()) lastActivity = now; }
    bool awaitingConsent() const { return !pendingKind.empty() && !signedPending; }
};
