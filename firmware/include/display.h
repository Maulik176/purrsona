#pragma once
#include "key_device.h"
#include "navigation.h"
#include <TFT_eSPI.h>

class MemoryDisplay {
    TFT_eSPI tft;
    TFT_eSprite canvas{&tft};
    Navigation navigation;
    uint32_t lastFrame = 0, lastGraph = 0, readyAt = 0;
    bool viewingGraph = false;
    void text(const std::string &value, int x, int y, uint16_t color, int font = 1);
    void title(const std::string &subtitle);
    void brain(uint32_t now);
    void graph(KeyDevice &device);
public:
    bool begin();
    void render(KeyDevice *device, uint32_t now, bool needsFormat = false);
    void next(KeyDevice &device);
    void open(KeyDevice &device);
    void back();
};
