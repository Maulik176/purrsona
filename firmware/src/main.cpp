#include <Arduino.h>
#include <LittleFS.h>
#include <deque>
#include "display.h"
#include "flash_storage.h"

MemoryDisplay display;
FlashStorage *storage = nullptr;
KeyDevice *device = nullptr;
std::deque<std::string> outgoing;
size_t sentBytes = 0;
uint32_t sendingAt = 0, initializeAt = 0;
volatile bool usbReset = false;
bool formatted = false, displayReady = false;

struct Button {
    int pin; bool raw = true, stable = true, longPress = false;
    uint32_t changedAt = 0, pressedAt = 0;
    explicit Button(int gpio) : pin(gpio) {}
    void begin() { pinMode(pin, INPUT_PULLUP); raw = stable = digitalRead(pin); }
    void update(uint32_t now) {
        bool current = digitalRead(pin);
        if (current != raw) { raw = current; changedAt = now; }
        if (stable != raw && uint32_t(now - changedAt) >= 30) {
            stable = raw;
            if (!stable) {
                pressedAt = now; longPress = false;
                if (device && device->awaitingConsent()) {
                    if (pin == 14) device->approve(now); else device->cancel();
                    longPress = true;
                } else if (pin == 14 && device) display.open(*device);
            } else if (pin == 0 && !longPress && device) display.next(*device);
        }
        if (!stable && pin == 0 && !longPress && uint32_t(now - pressedAt) >= 700) {
            longPress = true; display.back();
        }
    }
} buttonA(0), buttonB(14);

void setup() {
    displayReady = display.begin(); buttonA.begin(); buttonB.begin();
    if (!displayReady) return;
    if (!psramFound()) return;
    storage = new FlashStorage(); storage->begin();
    // Start USB after boot/storage logs so its FIFO belongs to the protocol.
    Serial.setRxBufferSize(MAX_LINE + 1);
    Serial.setTxBufferSize(4096);
    Serial.begin(115200);
    Serial.setTxTimeoutMs(0);
    Serial.onEvent(ARDUINO_HW_CDC_BUS_RESET_EVENT, [](void *, esp_event_base_t, int32_t, void *) { usbReset = true; });
    device = new KeyDevice(*storage, [](const std::string &line) {
        if (outgoing.empty()) sendingAt = millis();
        outgoing.push_back(line);
    });
}
void loop() {
    if (!displayReady) { delay(100); return; }
    uint32_t now = millis();
    if (usbReset && device) {
        usbReset = false; device->disconnect(); outgoing.clear(); sentBytes = 0;
    }
    buttonA.update(now); buttonB.update(now);
    if (formatted && buttonA.stable && buttonB.stable) ESP.restart();
    if (storage && storage->needsFormat && !formatted && !buttonA.stable && !buttonB.stable) {
        if (!initializeAt) initializeAt = now;
        if (uint32_t(now - initializeAt) >= 3000 && LittleFS.format()) {
            formatted = true; device->screen = "initialized";
        }
    } else initializeAt = 0;
    if (device) {
        char input[2048]; size_t size = 0;
        while (Serial.available() && size < sizeof(input)) input[size++] = Serial.read();
        if (size) device->feed(input, size, now);
        // Transfers have their own deadline; screen and button updates continue between writes.
        // feed()/button approval can queue a reply after this loop's original timestamp.
        now = millis();
        if (!outgoing.empty()) {
            if (uint32_t(now - sendingAt) >= 10000) { outgoing.clear(); sentBytes = 0; device->disconnect(); }
            else {
                size_t available = Serial.availableForWrite();
                const std::string &line = outgoing.front();
                size_t size = std::min(available, std::min(size_t(1024), line.size() - sentBytes));
                if (size) sentBytes += Serial.write(reinterpret_cast<const uint8_t *>(line.data() + sentBytes), size);
                if (sentBytes == line.size()) {
                    outgoing.pop_front(); sentBytes = 0;
                    if (outgoing.empty()) device->transmissionComplete(now);
                }
            }
        } else device->tick(now);
    }
    display.render(device, now, storage && storage->needsFormat && !formatted);
    delay(1);
}
