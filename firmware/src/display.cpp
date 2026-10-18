#include "display.h"
#include <cmath>

static uint16_t rgb(unsigned value) { return ((value >> 8) & 0xf800) | ((value >> 5) & 0x07e0) | ((value >> 3) & 0x001f); }
static const uint16_t BG = rgb(0x121318), TEXT = rgb(0xeceef3), MUTED = rgb(0x8b95a7),
    AMBER = rgb(0xff9f43), TEAL = rgb(0x2dd4bf), EDGE = rgb(0x344056);

bool MemoryDisplay::begin() {
    pinMode(15, OUTPUT); digitalWrite(15, HIGH); delay(20);
    tft.init(); tft.setRotation(1); tft.fillScreen(BG);
    pinMode(38, OUTPUT); digitalWrite(38, HIGH);
    canvas.setColorDepth(16);
    bool ready = canvas.createSprite(320, 170) != nullptr;
    if (!ready) { tft.setTextColor(AMBER, BG); tft.drawString("Display memory unavailable", 12, 70, 2); }
    return ready;
}
void MemoryDisplay::text(const std::string &value, int x, int y, uint16_t color, int font) {
    canvas.setTextColor(color, BG); canvas.setTextDatum(TL_DATUM);
    std::string visible = value;
    if (canvas.textWidth(visible.c_str(), font) > 308 - x) {
        while (!visible.empty() && canvas.textWidth((visible + "~").c_str(), font) > 308 - x) visible.pop_back();
        visible += "~";
    }
    canvas.drawString(visible.c_str(), x, y, font);
}
void MemoryDisplay::title(const std::string &subtitle) {
    text("PURRSONA", 12, 8, AMBER); text(subtitle, 170, 8, MUTED);
    canvas.drawFastHLine(12, 23, 296, EDGE);
}
void MemoryDisplay::brain(uint32_t now) {
    // A tiny shaded ASCII surface: two lobes, folded ridges, and a slow turning highlight.
    const char *ramp = " .:-=+*#%@";
    float angle = (now % 12000) * 0.0005236f;
    for (int row = 0; row < 10; ++row) {
        std::string line;
        for (int col = 0; col < 27; ++col) {
            float x = (col - 13) / 12.0f, y = (row - 4.5f) / 4.5f;
            float width = 0.92f - 0.11f * std::cos(y * 5.0f);
            float radius = x*x/(width*width) + y*y;
            if (radius > 1 || (fabsf(x) < 0.045f && y < -0.2f)) { line += ' '; continue; }
            float z = sqrtf(1 - radius);
            float folds = 0.18f * sinf(x * 23 + y * 13 + angle) * cosf(y * 17 - angle);
            float light = 0.4f + 0.35f*z + 0.22f*x*sinf(angle) + folds;
            int index = std::max(1, std::min(9, int(light * 9)));
            line += ramp[index];
        }
        text(line, 79, 32 + row * 8, row % 3 == 0 ? AMBER : TEAL);
    }
}
static std::string label(JsonVariantConst node, size_t width = 13) {
    std::string value = node["label"].as<std::string>();
    return value.size() > width ? value.substr(0, width - 1) + "~" : value;
}
void MemoryDisplay::graph(KeyDevice &device) {
    auto nodes = device.graph["nodes"].as<JsonArrayConst>();
    title(device.screen == "drafting" ? "DRAFTING" : device.screen == "learning" ? "SAVING" : "MEMORY CONNECTED");
    if (device.graph["truncated"].as<bool>()) text("DEMO", 85, 8, MUTED);
    if (nodes.size() == 0) { text("Your graph is empty", 75, 78, MUTED, 2); return; }
    if (navigation.details) {
        JsonVariantConst node = nodes[navigation.center];
        text(node["kind"].as<std::string>(), 12, 32, TEAL);
        std::string detail = node["detail"] | node["label"].as<const char *>();
        size_t page = navigation.detailPage % std::max(size_t(1), (detail.size() + 251) / 252);
        for (size_t row = 0; row < 6; ++row) {
            size_t start = page * 252 + row * 42;
            if (start < detail.size()) text(detail.substr(start, 42), 12, 48 + row * 14, TEXT);
        }
        text("A More   B Graph   Hold A Back", 12, 154, MUTED); return;
    }
    static const int xs[6] = {70, 250, 275, 250, 70, 45};
    static const int ys[6] = {46, 46, 84, 122, 122, 84};
    std::vector<int> visible;
    int selected = navigation.selected;
    if (navigation.center < 0) {
        int start = selected / 6 * 6;
        for (int n = start; n < std::min(start + 6, int(nodes.size())); ++n) visible.push_back(n);
        for (JsonVariantConst edge : device.graph["edges"].as<JsonArrayConst>()) {
            int source = -1, target = -1;
            for (size_t n = 0; n < visible.size(); ++n) {
                std::string nodeId = nodes[visible[n]]["id"].as<std::string>();
                if (nodeId == edge["source"].as<std::string>()) source = n;
                if (nodeId == edge["target"].as<std::string>()) target = n;
            }
            if (source >= 0 && target >= 0) canvas.drawLine(xs[source], ys[source], xs[target], ys[target], EDGE);
        }
        text(std::to_string(start / 6 + 1) + "/" + std::to_string((nodes.size() + 5) / 6), 144, 77, MUTED);
    } else {
        auto options = navigation.neighbors(device.graph.as<JsonVariantConst>());
        selected = options[navigation.choice];
        int page = navigation.choice ? (navigation.choice - 1) / 5 : 0;
        for (int n = 1 + page * 5; n < std::min(6 + page * 5, int(options.size())); ++n) visible.push_back(options[n]);
        for (size_t n = 0; n < visible.size(); ++n) canvas.drawLine(160, 82, xs[n], ys[n], EDGE);
        canvas.fillRoundRect(110, 65, 100, 35, 6, BG);
        canvas.drawRoundRect(110, 65, 100, 35, 6, selected == navigation.center ? AMBER : TEAL);
        text(label(nodes[navigation.center], 15), 116, 78, TEXT);
    }
    for (size_t n = 0; n < visible.size(); ++n) {
        uint16_t color = visible[n] == selected ? AMBER : TEAL;
        canvas.fillCircle(xs[n], ys[n], visible[n] == selected ? 6 : 4, color);
        if (visible[n] == selected) canvas.drawCircle(xs[n], ys[n], 9, color);
        auto name = label(nodes[visible[n]], 12);
        text(name, std::max(6, xs[n] - int(name.size() * 3)), ys[n] + 12, color);
    }
    text("A Next   B Open   Hold A Back", 12, 154, MUTED);
}
void MemoryDisplay::render(KeyDevice *device, uint32_t now, bool needsFormat) {
    if (uint32_t(now - lastFrame) < 80) return;
    lastFrame = now; canvas.fillSprite(BG);
    if (!device || needsFormat) {
        title("SETUP");
        text(needsFormat ? "Initialize memory" : "Memory unavailable", 30, 56, AMBER, 2);
        text(needsFormat ? "Hold BOTH buttons for 3 seconds" : "Check PSRAM and restart the board", 30, 90, TEXT);
        if (needsFormat) text("This erases the memory filesystem", 30, 112, MUTED);
    } else {
        if (!device->graphReceived) { navigation.reset(); viewingGraph = false; readyAt = 0; }
        if (lastGraph != device->graphChanged) { navigation.reset(); lastGraph = device->graphChanged; }
        bool ready = device->graphReceived && (device->screen == "ready" ||
                     device->screen == "drafting" || device->screen == "learning");
        if (ready && !readyAt) readyAt = now;
        if (ready && uint32_t(now - device->loadingAt) >= 1800 && uint32_t(now - readyAt) >= 700) viewingGraph = true;
        if (viewingGraph && device->screen != "error") graph(*device);
        else if (device->screen == "pair" || device->screen == "unlock" || device->screen == "paired") {
            title("YOUR APPROVAL");
            text(device->screen == "pair" ? "Pair this laptop?" : device->screen == "paired" ? "Compare fingerprint" : "Unlock your memory?", 20, 42, TEXT, 2);
            text(device->screen == "unlock" ? device->laptop : device->pairingFingerprint, 20, 78, AMBER, 2);
            if (device->screen != "paired") text("Press B within 2 minutes", 20, 110, MUTED);
            text(device->screen == "paired" ? "Enter this code on your laptop" : "A Cancel                 B Approve", 20, 137, MUTED);
        } else if (device->screen == "loading" || ready) {
            title("MEMORY KEY"); brain(now);
            text(ready ? "Memory connected" : "Loading your memories", 78, 120, TEXT, 2);
            text(ready ? "Opening your graph..." : "Keep the cable connected", 78, 148, MUTED);
        } else if (device->screen == "initialized") {
            title("SETUP COMPLETE"); text("Memory initialized", 20, 62, TEAL, 2);
            text("Release both buttons to restart", 20, 106, MUTED);
        } else if (device->screen == "error") {
            title("MEMORY KEY"); text("Memory needs attention", 20, 62, AMBER, 2);
            text("Check the laptop; reconnect to retry", 20, 106, MUTED);
        } else {
            title("MEMORY KEY"); brain(now);
            text("Plug in. Make it yours.", 63, 120, TEXT, 2);
            text("Waiting for your laptop", 85, 148, MUTED);
        }
    }
    canvas.pushSprite(0, 0);
}
void MemoryDisplay::next(KeyDevice &device) { if (viewingGraph) navigation.next(device.graph.as<JsonVariantConst>()); }
void MemoryDisplay::open(KeyDevice &device) { if (viewingGraph) navigation.open(device.graph.as<JsonVariantConst>()); }
void MemoryDisplay::back() { if (viewingGraph) navigation.back(); }
