// Desktop adapter for the SAME device state machine and Ed25519 implementation as the board.
#include "key_device.h"
#include "navigation.h"
#include "display.h"
#include <Ed25519.h>
#include <RNG.h>
#include <SHA256.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <cstdlib>
#include <filesystem>
#include <unistd.h>

// Tests use a fixed, public test seed. Accidentally asking this adapter for randomness is an error.
RNGClass::RNGClass() {}
RNGClass::~RNGClass() {}
void RNGClass::rand(uint8_t *, size_t) { std::abort(); }
RNGClass RNG;

static std::string base64(const uint8_t *data, size_t size) {
    const char *alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string result;
    for (size_t i = 0; i < size; i += 3) {
        unsigned value = unsigned(data[i]) << 16;
        if (i + 1 < size) value |= unsigned(data[i + 1]) << 8;
        if (i + 2 < size) value |= data[i + 2];
        result += alphabet[(value >> 18) & 63]; result += alphabet[(value >> 12) & 63];
        result += i + 1 < size ? alphabet[(value >> 6) & 63] : '=';
        result += i + 2 < size ? alphabet[value & 63] : '=';
    }
    return result;
}
class NativeStorage : public DeviceStorage {
    std::string seed;
    std::vector<MemoryEvent> records;
    uint8_t privateBytes[32], publicBytes[32];
    uint64_t savedCounter = 0;
    std::string statePath;
    bool persist() {
        if (statePath.empty()) return true;
        Document state(4 * 1024 * 1024);
        state["counter"] = savedCounter;
        auto events = state.createNestedArray("events");
        for (const auto &record : records) {
            auto value = events.createNestedObject(); value["id"] = record.id; value["json"] = record.json;
        }
        std::string bytes; serializeJson(state, bytes);
        std::string temporary = statePath + ".tmp";
        FILE *file = fopen(temporary.c_str(), "wb");
        if (!file) return false;
        bool success = fwrite(bytes.data(), 1, bytes.size(), file) == bytes.size();
        success = fflush(file) == 0 && success;
        success = fsync(fileno(file)) == 0 && success;
        success = fclose(file) == 0 && success;
        return success && rename(temporary.c_str(), statePath.c_str()) == 0;
    }
public:
    bool failed = false;
    NativeStorage(const char *path, const char *directory) {
        std::ifstream input(path); seed.assign(std::istreambuf_iterator<char>(input), {});
        for (unsigned n = 0; n < 32; ++n) privateBytes[n] = n;
        Ed25519::derivePublicKey(publicBytes, privateBytes);
        if (directory) {
            std::filesystem::create_directories(directory);
            statePath = std::string(directory) + "/mock-board.json";
            if (std::filesystem::exists(statePath)) {
                std::ifstream file(statePath);
                std::string bytes(std::istreambuf_iterator<char>(file), {});
                Document state(4 * 1024 * 1024);
                if (deserializeJson(state, bytes) || !state["counter"].is<uint64_t>() ||
                    !state["events"].is<JsonArray>()) { failed = true; return; }
                savedCounter = state["counter"];
                for (JsonVariant record : state["events"].as<JsonArray>()) {
                    if (!record["id"].is<const char *>() || !record["json"].is<const char *>()) { failed = true; return; }
                    records.push_back({record["id"].as<std::string>(), record["json"].as<std::string>()});
                }
            }
        }
    }
    bool healthy() const override { return !failed; }
    const std::string &snapshot() const override { return seed; }
    const std::vector<MemoryEvent> &events() const override { return records; }
    std::string publicKey() const override { return base64(publicBytes, 32); }
    std::string fingerprint() const override {
        SHA256 hash; uint8_t digest[32]; char value[7];
        hash.update(publicBytes, 32); hash.finalize(digest, 32);
        snprintf(value, sizeof(value), "%02X%02X%02X", digest[0], digest[1], digest[2]); return value;
    }
    uint64_t counter() const override { return savedCounter; }
    bool advanceCounter() override { if (failed) return false; ++savedCounter; failed = !persist(); return !failed; }
    std::string sign(const std::string &message) const override {
        uint8_t signature[64]; Ed25519::sign(signature, privateBytes, publicBytes, message.data(), message.size());
        return base64(signature, 64);
    }
    bool append(const MemoryEvent &event) override {
        if (failed) return false;
        records.push_back(event); failed = !persist(); return !failed;
    }
};

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) return 2;
    NativeStorage storage(argv[1], argc == 3 ? argv[2] : nullptr);
    KeyDevice device(storage, [](const std::string &message) { std::cout << message << std::flush; });
    Navigation navigation;
    MemoryDisplay display; display.begin();
    uint32_t now = 80;
    display.render(&device, now);
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line == "!approve") device.approve(now);
        else if (line == "!cancel") device.cancel();
        else if (line == "!disconnect") { device.disconnect(); navigation.reset(); }
        else if (line == "!fail-storage") storage.failed = true;
        else if (line.rfind("!tick ", 0) == 0) { now += std::stoul(line.substr(6)); device.tick(now); }
        else if (line == "!next") navigation.next(device.graph.as<JsonVariantConst>());
        else if (line == "!open") navigation.open(device.graph.as<JsonVariantConst>());
        else if (line == "!back") navigation.back();
        else if (line == "!button-a") {
            if (device.awaitingConsent()) device.cancel(); else display.next(device);
        }
        else if (line == "!button-b") {
            if (device.awaitingConsent()) device.approve(now); else display.open(device);
        }
        else if (line == "!hold-a") display.back();
        else if (line == "!frame") {
            Document result(512 * 1024);
            result["simulator"] = "frame"; result["screen"] = device.screen;
            result["events"] = storage.events().size(); result["nodes"] = device.graph["nodes"].size();
            auto texts = result.createNestedArray("texts");
            for (const auto &text : displayedFrame.texts) {
                auto item = texts.createNestedObject(); item["text"] = text.value;
                item["x"] = text.x; item["y"] = text.y; item["width"] = text.width; item["height"] = text.height;
            }
            const char *hex = "0123456789abcdef";
            std::string pixels; pixels.reserve(320 * 170 * 4);
            for (uint16_t value : displayedFrame.pixels)
                for (int shift = 12; shift >= 0; shift -= 4) pixels += hex[(value >> shift) & 15];
            result["pixels"] = pixels;
            std::string value; serializeJson(result, value); std::cout << value << '\n' << std::flush;
        }
        else if (line == "!inspect") {
            Document result(1024);
            result["session"] = device.session; result["screen"] = device.screen;
            result["counter"] = storage.counter(); result["events"] = storage.events().size();
            result["nodes"] = device.graph["nodes"].size(); result["selected"] = navigation.selected;
            result["center"] = navigation.center; result["details"] = navigation.details;
            std::string value; serializeJson(result, value); std::cout << value << '\n' << std::flush;
        } else {
            line += '\n';
            for (size_t offset = 0; offset < line.size(); offset += 17)
                device.feed(line.data() + offset, std::min(size_t(17), line.size() - offset), now);
        }
        display.render(&device, now);
    }
}
