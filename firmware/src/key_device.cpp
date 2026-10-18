#include "key_device.h"
#include "strict_json.h"
#include <algorithm>
#include <set>

static std::string text(JsonVariantConst value) { return value.is<const char *>() ? value.as<std::string>() : ""; }
static bool number(JsonVariantConst value, uint64_t minimum = 0) {
    return !value.is<bool>() && value.is<uint64_t>() && value.as<uint64_t>() >= minimum && value.as<uint64_t>() <= MAX_COUNTER;
}
static bool id(const std::string &value) {
    if (value.empty() || value.size() > 64) return false;
    for (char c : value) if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') &&
        !(c >= '0' && c <= '9') && c != '.' && c != '_' && c != '-') return false;
    return true;
}
static bool hex(const std::string &value, size_t size) {
    if (value.size() != size) return false;
    for (char c : value) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) return false;
    return true;
}
static bool strings(JsonVariantConst value) {
    if (!value.is<JsonArrayConst>()) return false;
    for (JsonVariantConst item : value.as<JsonArrayConst>()) if (!item.is<const char *>()) return false;
    return true;
}
static bool hasStrings(JsonVariantConst value, std::initializer_list<const char *> names) {
    if (!value.is<JsonObjectConst>()) return false;
    for (auto name : names) if (!value[name].is<const char *>()) return false;
    return true;
}
bool validEvent(JsonVariantConst event) {
    if (!number(event["at"])) return false;
    std::string kind = text(event["type"]);
    if (kind == "forgot") return hasStrings(event, {"rule_id"}) &&
        (!event.as<JsonObjectConst>().containsKey("rule_text") || event["rule_text"].is<const char *>());
    if (kind != "sent" && kind != "learned") return false;
    if (!hasStrings(event, {"platform"}) || !hasStrings(event["person"], {"id", "name"}) ||
        !event["person"]["new"].is<bool>() || !event["person"].as<JsonObjectConst>().containsKey("circle") ||
        !hasStrings(event["draft"], {"id", "text"})) return false;
    for (auto name : {"circle", "relationship"}) if (!event["person"][name].isNull() &&
        !event["person"][name].is<const char *>()) return false;
    if (event["draft"].as<JsonObjectConst>().containsKey("rule_ids") && !strings(event["draft"]["rule_ids"])) return false;
    if (kind == "learned") {
        if (!hasStrings(event["edit"], {"id", "before", "after"}) || !event["rules"].is<JsonArrayConst>()) return false;
        for (JsonVariantConst rule : event["rules"].as<JsonArrayConst>()) {
            if (!hasStrings(rule, {"id", "kind", "text"}) || !strings(rule["overrides"]) || !strings(rule["supersedes"])) return false;
        }
    }
    if (event.as<JsonObjectConst>().containsKey("trail")) {
        if (!event["trail"].is<JsonArrayConst>()) return false;
        for (JsonVariantConst step : event["trail"].as<JsonArrayConst>()) {
            if (!hasStrings(step, {"agent", "action", "detail"}) || !number(step["n"]) ||
                !number(step["at"]) || !number(step["ms"])) return false;
            for (auto name : {"taught", "flagged"}) if (step.as<JsonObjectConst>().containsKey(name) && !strings(step[name])) return false;
        }
    }
    return true;
}
std::string canonicalJson(JsonVariantConst value) {
    std::string output;
    if (value.is<JsonObjectConst>()) {
        std::vector<std::string> names;
        for (JsonPairConst pair : value.as<JsonObjectConst>()) names.push_back(pair.key().c_str());
        std::sort(names.begin(), names.end());
        output = "{";
        for (const auto &name : names) {
            if (output.size() > 1) output += ',';
            Document key(512 + name.size()); key.set(name);
            serializeJson(key, output);
            output += ':'; output += canonicalJson(value[name]);
        }
        return output + "}";
    }
    if (value.is<JsonArrayConst>()) {
        output = "[";
        for (JsonVariantConst item : value.as<JsonArrayConst>()) {
            if (output.size() > 1) output += ',';
            output += canonicalJson(item);
        }
        return output + "]";
    }
    serializeJson(value, output);
    return output;
}

KeyDevice::KeyDevice(DeviceStorage &storage, std::function<void(const std::string &)> output) : store(storage), send(output) {
    Document seed(128 * 1024);
    if (!deserializeJson(seed, store.snapshot())) keyId = text(seed["key_id"]);
    if (!store.healthy() || !id(keyId)) screen = "error";
}
void KeyDevice::reply(Document &document, const std::string &type, const std::string &requestId, const std::string &requestSession) {
    document.clear(); document["v"] = 1; document["type"] = type;
    if (!requestId.empty()) document["reply_to"] = requestId;
    if (!requestSession.empty()) document["session"] = requestSession;
}
void KeyDevice::emit(Document &document) {
    if (document.overflowed() || measureJson(document) > MAX_LINE) { error("TOO_LARGE"); return; }
    std::string line; serializeJson(document, line); send(line + "\n");
}
void KeyDevice::error(const std::string &code, const std::string &requestId, const std::string &requestSession) {
    Document response(1024);
    reply(response, "ERROR", id(requestId) ? requestId : "",
          !requestSession.empty() && (requestSession == session || requestSession == pendingSession) ? requestSession : "");
    response["code"] = code; response["message"] = "The memory key could not complete this request";
    emit(response);
}
void KeyDevice::disconnect() {
    session.clear(); pendingId.clear(); pendingKind.clear(); pendingSession.clear(); nonce.clear(); laptop.clear();
    pairingFingerprint.clear(); signedPending = false; pulled = false; graph.clear(); graphReceived = false;
    incoming.clear(); frameStarted = false; poisoned = false; displayMessage.clear();
    ruleCount = 0; rulesKnown = false;
    screen = store.healthy() ? "locked" : "error";
}
void KeyDevice::tick(uint32_t now) {
    if (frameStarted && uint32_t(now - frameAt) >= 10000) { error("BAD_REQUEST"); disconnect(); poisoned = true; return; }
    if (!pendingKind.empty() && uint32_t(now - pendingAt) >= (signedPending ? UNLOCK_TIMEOUT_MS : CONSENT_TIMEOUT_MS)) {
        if (!signedPending) {
            Document response(1024); reply(response, "DENIED", pendingId, pendingSession);
            response["reason"] = "timeout"; emit(response);
        }
        disconnect();
    }
    if (!session.empty() && !frameStarted && uint32_t(now - lastActivity) >= 3000) disconnect();
}
void KeyDevice::feed(const char *bytes, size_t size, uint32_t now) {
    tick(now);
    if (poisoned) return;
    for (size_t n = 0; n < size; ++n) {
        if (!frameStarted) { frameStarted = true; frameAt = now; }
        if (bytes[n] == '\n') {
            std::string line; line.swap(incoming); frameStarted = false;
            receive(line, now);
            if (poisoned) return;
        } else {
            if (incoming.size() == MAX_LINE) { error("TOO_LARGE"); disconnect(); poisoned = true; return; }
            incoming += bytes[n];
        }
    }
}
void KeyDevice::approve(uint32_t now) {
    tick(now);
    if (!awaitingConsent() || poisoned) return;
    Document response(2048);
    if (pendingKind == "PAIR_BEGIN") {
        reply(response, "PAIR_OK", pendingId);
        response["key_id"] = keyId; response["pk"] = store.publicKey(); response["fingerprint"] = store.fingerprint();
        emit(response); pendingKind.clear(); pendingId.clear(); screen = "paired";
        return;
    }
    if (!store.advanceCounter()) { error("STORAGE_ERROR", pendingId, pendingSession); disconnect(); return; }
    std::string message = "MEMKEY-UNLOCK-v1\n" + keyId + "\n" + laptop + "\n" + pendingSession + "\n" +
                          nonce + "\n" + std::to_string(store.counter());
    reply(response, "UNLOCK", pendingId, pendingSession);
    response["counter"] = store.counter(); response["sig"] = store.sign(message);
    signedPending = true; screen = "loading"; loadingAt = now;
    emit(response);
}
void KeyDevice::cancel() {
    if (!awaitingConsent()) return;
    Document response(1024); reply(response, "DENIED", pendingId, pendingSession);
    response["reason"] = "cancelled"; emit(response); disconnect();
}
static bool validGraph(JsonVariantConst request) {
    if (!number(request["revision"], 1) || !request["nodes"].is<JsonArrayConst>() ||
        !request["edges"].is<JsonArrayConst>() || !request["truncated"].is<bool>() ||
        request["nodes"].size() > 64 || request["edges"].size() > 128) return false;
    std::set<std::string> ids;
    for (JsonVariantConst node : request["nodes"].as<JsonArrayConst>()) {
        if (!hasStrings(node, {"id", "kind", "label"}) || text(node["id"]).empty() ||
            !ids.insert(text(node["id"])).second || text(node["label"]).size() > 72 ||
            (node.as<JsonObjectConst>().containsKey("detail") && !node["detail"].is<const char *>()) ||
            text(node["detail"]).size() > 512) return false;
    }
    for (JsonVariantConst edge : request["edges"].as<JsonArrayConst>()) if (!hasStrings(edge, {"source", "target", "type"}) ||
        !ids.count(text(edge["source"])) || !ids.count(text(edge["target"]))) return false;
    return true;
}
void KeyDevice::receive(const std::string &line, uint32_t now) {
    Document request(256 * 1024), response(256 * 1024);
    if (!StrictJson(line).object() || deserializeJson(request, line, DeserializationOption::NestingLimit(32))) {
        error("BAD_REQUEST"); disconnect(); poisoned = true; return;
    }
    std::string requestId = text(request["id"]), kind = text(request["type"]), requestSession = text(request["session"]);
    auto fail = [&](const std::string &code) { error(code, requestId, requestSession); };
    if (!number(request["v"]) || request["v"].as<int>() != 1) { fail("UNSUPPORTED_VERSION"); disconnect(); poisoned = true; return; }
    if (!id(requestId) || request.containsKey("reply_to") || kind.empty()) { fail("BAD_REQUEST"); return; }
    if (!store.healthy()) { fail("STORAGE_ERROR"); return; }
    bool protectedRequest = kind == "PULL" || kind == "APPEND" || kind == "STATUS" || kind == "GRAPH_VIEW" || kind == "BYE";
    if (!protectedRequest && kind != "HELLO" && kind != "PAIR_BEGIN" && kind != "CHALLENGE" && kind != "UNLOCKED") {
        fail("UNKNOWN_TYPE"); return;
    }
    if (protectedRequest && (session.empty() || requestSession != session)) { fail("LOCKED"); return; }
    if (!protectedRequest && kind != "UNLOCKED" && (!session.empty() || !pendingKind.empty())) { fail("INVALID_STATE"); return; }
    if (kind == "HELLO") {
        reply(response, "HELLO", requestId); response["key_id"] = keyId; response["fw"] = "tdisplay-0.1.0";
        response["counter"] = store.counter(); response["max_line_bytes"] = MAX_LINE; emit(response); return;
    }
    if (kind == "PAIR_BEGIN" || kind == "CHALLENGE") {
        if (!id(text(request["laptop_id"])) || (kind == "CHALLENGE" &&
            (!hex(requestSession, 32) || !hex(text(request["nonce"]), 64)))) { fail("BAD_REQUEST"); return; }
        pendingKind = kind; pendingId = requestId; pendingAt = now; laptop = text(request["laptop_id"]);
        nonce = text(request["nonce"]); pendingSession = kind == "CHALLENGE" ? requestSession : "";
        pairingFingerprint = store.fingerprint(); screen = kind == "CHALLENGE" ? "unlock" : "pair"; return;
    }
    if (kind == "UNLOCKED") {
        if (!signedPending || pendingKind != "CHALLENGE" || requestSession != pendingSession ||
            uint32_t(now - pendingAt) >= UNLOCK_TIMEOUT_MS) { fail("LOCKED"); return; }
        session = pendingSession; pendingKind.clear(); signedPending = false; lastActivity = now;
        screen = "loading"; reply(response, "ACK", requestId, session); emit(response); return;
    }
    reply(response, "ACK", requestId, session);
    if (kind == "PULL") {
        response["type"] = "SNAPSHOT";
        Document snapshot(256 * 1024);
        if (deserializeJson(snapshot, store.snapshot())) { fail("STORAGE_ERROR"); return; }
        response["snapshot"] = snapshot.as<JsonVariantConst>();
        if (response.overflowed() || measureJson(response) > MAX_LINE) { fail("TOO_LARGE"); return; }
        emit(response);
        size_t seq = 0;
        for (const auto &event : store.events()) {
            snapshot.clear();
            if (deserializeJson(snapshot, event.json)) { fail("STORAGE_ERROR"); return; }
            reply(response, "EVENT", requestId, session); response["seq"] = ++seq; response["event_id"] = event.id;
            response["event"] = snapshot.as<JsonVariantConst>(); emit(response);
        }
        reply(response, "PULL_DONE", requestId, session); response["count"] = seq; pulled = true;
    } else if (kind == "APPEND") {
        std::string eventId = text(request["event_id"]);
        if (!id(eventId) || !validEvent(request["event"])) { fail("BAD_REQUEST"); return; }
        std::string eventJson = canonicalJson(request["event"]);
        bool duplicate = false;
        for (const auto &saved : store.events()) if (saved.id == eventId) {
            if (saved.json != eventJson) { fail("EVENT_CONFLICT"); return; }
            duplicate = true; break;
        }
        Document replay(256 * 1024); reply(replay, "EVENT", std::string(64, 'x'), session);
        replay["seq"] = MAX_COUNTER; replay["event_id"] = eventId; replay["event"] = request["event"];
        if (replay.overflowed() || measureJson(replay) > MAX_LINE) { fail("TOO_LARGE"); return; }
        if (!duplicate && !store.append({eventId, eventJson})) { fail("STORAGE_ERROR"); disconnect(); return; }
        response["event_id"] = eventId; response["count"] = store.events().size();
    } else if (kind == "STATUS") {
        std::string next = text(request["state"]);
        if (next != "loading" && next != "ready" && next != "drafting" && next != "learning" && next != "error") { fail("BAD_REQUEST"); return; }
        if (next == "ready" && !number(request["memories"])) { fail("BAD_REQUEST"); return; }
        if (next == "ready" && (!pulled || request["memories"].as<uint64_t>() != store.events().size())) { fail("INVALID_STATE"); return; }
        if ((request.containsKey("message") && !request["message"].is<const char *>()) || text(request["message"]).size() > 160) { fail("BAD_REQUEST"); return; }
        if (request.containsKey("rules") && !number(request["rules"])) { fail("BAD_REQUEST"); return; }
        if (request.containsKey("rules")) { ruleCount = request["rules"].as<uint64_t>(); rulesKnown = true; }
        screen = next; displayMessage = text(request["message"]);
    } else if (kind == "GRAPH_VIEW") {
        if (!validGraph(request.as<JsonVariantConst>())) { fail("BAD_REQUEST"); return; }
        if (request["revision"].as<uint64_t>() <= graph["revision"].as<uint64_t>()) { fail("STALE_VIEW"); return; }
        Document replacement(128 * 1024);
        for (const char *name : {"revision", "nodes", "edges", "truncated"}) replacement[name] = request[name];
        if (replacement.overflowed()) { fail("TOO_LARGE"); return; }
        graph = std::move(replacement); graphChanged++; graphReceived = true;
        response["revision"] = graph["revision"];
    } else if (kind == "BYE") {
        if (text(request["reason"]) != "eject" && text(request["reason"]) != "shutdown") { fail("BAD_REQUEST"); return; }
        emit(response); disconnect(); return;
    }
    lastActivity = now; emit(response);
}
