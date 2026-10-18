#pragma once
#include "key_device.h"
#include <Preferences.h>

class FlashStorage : public DeviceStorage {
    Preferences preferences;
    bool ready = false;
    uint8_t privateKeyBytes[32]{}, publicKeyBytes[32]{};
    uint64_t savedCounter = 0;
    size_t logBytes = 0;
    std::string snapshotJson;
    std::vector<MemoryEvent> savedEvents;
    bool recover();
public:
    bool needsFormat = false;
    bool begin();
    bool healthy() const override { return ready; }
    const std::string &snapshot() const override { return snapshotJson; }
    const std::vector<MemoryEvent> &events() const override { return savedEvents; }
    std::string publicKey() const override;
    std::string fingerprint() const override;
    uint64_t counter() const override { return savedCounter; }
    bool advanceCounter() override;
    std::string sign(const std::string &message) const override;
    bool append(const MemoryEvent &event) override;
};
