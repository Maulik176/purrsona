#pragma once
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <functional>
#include <string>

// Hardware adapters for running the actual Arduino main loop with a controlled clock.
constexpr size_t MAX_LINE = 65536;
constexpr int INPUT_PULLUP = 1, ARDUINO_HW_CDC_BUS_RESET_EVENT = 1;
using esp_event_base_t = const char *;
inline uint32_t fakeMillis = 0;
inline uint32_t millis() { return fakeMillis; }
inline void delay(uint32_t milliseconds) { fakeMillis += milliseconds; }
inline void pinMode(int, int) {}
inline bool digitalRead(int) { return true; }
inline bool psramFound() { return true; }
inline struct { void restart() { assert(false && "Unexpected restart"); } } ESP;
inline struct { bool format() { return true; } } LittleFS;

inline struct SerialAdapter {
    std::string input, output;
    size_t capacity = 4096;
    void begin(int) {}
    void setTxTimeoutMs(int) {}
    void setRxBufferSize(size_t) {}
    void setTxBufferSize(size_t) {}
    template<class Callback> void onEvent(int, Callback) {}
    int available() { return input.size(); }
    char read() { char byte = input.front(); input.erase(0, 1); return byte; }
    size_t availableForWrite() { return capacity; }
    size_t write(const uint8_t *data, size_t size) {
        size = std::min(capacity, size);
        output.append(reinterpret_cast<const char *>(data), size);
        return size;
    }
} Serial;

struct FlashStorage {
    bool needsFormat = false;
    void begin() {}
};
struct KeyDevice {
    std::function<void(const std::string &)> send;
    std::string screen;
    bool disconnected = false;
    KeyDevice(FlashStorage &, std::function<void(const std::string &)> output) : send(output) {}
    void feed(const char *, size_t, uint32_t) {
        ++fakeMillis; // Decoding a request crosses a millisecond boundary on the ESP32.
        send("reply\n");
    }
    void disconnect() { disconnected = true; }
    bool awaitingConsent() { return false; }
    void approve(uint32_t) {}
    void cancel() {}
    void tick(uint32_t) {}
    void transmissionComplete(uint32_t) {}
};
struct MemoryDisplay {
    bool begin() { return true; }
    void open(KeyDevice &) {}
    void next(KeyDevice &) {}
    void back() {}
    void render(KeyDevice *, uint32_t, bool) {}
};
