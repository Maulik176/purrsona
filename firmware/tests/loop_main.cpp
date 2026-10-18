#include "../src/main.cpp"

int main() {
    setup();
    // A newly queued reply must survive both a normal clock tick and millis() wraparound.
    for (uint32_t start : {1000U, UINT32_MAX}) {
        fakeMillis = start;
        Serial.input = "request\n";
        Serial.output.clear();
        loop();
        assert(Serial.output == "reply\n");
        assert(!device->disconnected && outgoing.empty());
    }
    // Backpressure preserves a pending transfer until its real ten-second deadline.
    Serial.capacity = 0;
    fakeMillis = 0xfffffff0U;
    device->send("pending\n");
    fakeMillis = 0x10U;
    loop();
    assert(!outgoing.empty() && !device->disconnected);
    fakeMillis = sendingAt + 10000U;
    loop();
    assert(outgoing.empty() && device->disconnected);
    delete device;
    delete storage;
}
