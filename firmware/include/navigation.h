#pragma once
#include "key_device.h"
#include <algorithm>

class Navigation {
public:
    int selected = 0, center = -1, choice = 0, detailPage = 0;
    bool details = false;
    std::vector<int> history;
    void reset() { selected = 0; center = -1; choice = 0; details = false; detailPage = 0; history.clear(); }
    std::vector<int> neighbors(JsonVariantConst graph) const {
        std::vector<int> result;
        auto nodes = graph["nodes"].as<JsonArrayConst>();
        if (center < 0 || center >= int(nodes.size())) return result;
        result.push_back(center);
        std::string current = nodes[center]["id"].as<std::string>();
        for (JsonVariantConst edge : graph["edges"].as<JsonArrayConst>()) {
            std::string source = edge["source"].as<std::string>(), target = edge["target"].as<std::string>();
            std::string other = source == current ? target : target == current ? source : "";
            for (size_t n = 0; !other.empty() && n < nodes.size(); ++n) {
                if (nodes[n]["id"].as<std::string>() == other &&
                    std::find(result.begin(), result.end(), int(n)) == result.end()) result.push_back(n);
            }
        }
        return result;
    }
    void next(JsonVariantConst graph) {
        if (details) { ++detailPage; return; }
        if (center < 0) { if (graph["nodes"].size()) selected = (selected + 1) % graph["nodes"].size(); }
        else { auto options = neighbors(graph); if (!options.empty()) choice = (choice + 1) % options.size(); }
    }
    void open(JsonVariantConst graph) {
        if (!graph["nodes"].size()) return;
        if (details) { details = false; return; }
        if (center < 0) { center = selected; choice = 0; return; }
        auto options = neighbors(graph);
        if (choice == 0) { details = true; detailPage = 0; }
        else if (choice < int(options.size())) {
            if (history.size() == 64) history.erase(history.begin());
            history.push_back(center); center = options[choice]; choice = 0;
        }
    }
    void back() {
        if (details) { details = false; return; }
        if (history.empty()) center = -1;
        else { center = history.back(); history.pop_back(); }
        choice = 0;
    }
};
