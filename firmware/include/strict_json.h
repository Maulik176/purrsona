#pragma once
#include <cmath>
#include <cstdlib>
#include <set>
#include <string>

// ArduinoJson accepts duplicate keys and a few non-JSON forms. Reject them before parsing.
class StrictJson {
    const std::string &s;
    size_t at = 0;
    void space() { while (at < s.size() && (s[at] == ' ' || s[at] == '\t' || s[at] == '\r' || s[at] == '\n')) ++at; }
    bool take(char c) { if (at < s.size() && s[at] == c) { ++at; return true; } return false; }
    static void utf8(std::string &out, unsigned cp) {
        if (cp < 0x80) out += char(cp);
        else if (cp < 0x800) { out += char(0xc0 | (cp >> 6)); out += char(0x80 | (cp & 63)); }
        else if (cp < 0x10000) {
            out += char(0xe0 | (cp >> 12)); out += char(0x80 | ((cp >> 6) & 63)); out += char(0x80 | (cp & 63));
        } else {
            out += char(0xf0 | (cp >> 18)); out += char(0x80 | ((cp >> 12) & 63));
            out += char(0x80 | ((cp >> 6) & 63)); out += char(0x80 | (cp & 63));
        }
    }
    bool hex4(unsigned &value) {
        value = 0;
        for (int n = 0; n < 4; ++n) {
            if (at == s.size()) return false;
            unsigned char c = s[at++];
            int digit = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
                        c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
            if (digit < 0) return false;
            value = value * 16 + digit;
        }
        return true;
    }
    bool string(std::string &out) {
        if (!take('"')) return false;
        while (at < s.size()) {
            unsigned char c = s[at++];
            if (c == '"') return true;
            if (c < 32) return false;
            if (c == '\\') {
                if (at == s.size()) return false;
                char escape = s[at++];
                if (escape == '"' || escape == '/' || escape == '\\') out += escape;
                else if (escape == 'b') out += '\b';
                else if (escape == 'f') out += '\f';
                else if (escape == 'n') out += '\n';
                else if (escape == 'r') out += '\r';
                else if (escape == 't') out += '\t';
                else if (escape == 'u') {
                    unsigned cp, low;
                    if (!hex4(cp)) return false;
                    if (cp >= 0xd800 && cp <= 0xdbff) {
                        if (!take('\\') || !take('u') || !hex4(low) || low < 0xdc00 || low > 0xdfff) return false;
                        cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00;
                    } else if (cp >= 0xdc00 && cp <= 0xdfff) return false;
                    if (cp == 0) return false;
                    utf8(out, cp);
                } else return false;
            } else if (c < 0x80) out += c;
            else {
                unsigned cp = 0, minimum = 0; int remaining = 0;
                if (c >= 0xc2 && c <= 0xdf) { cp = c & 31; remaining = 1; minimum = 0x80; }
                else if (c >= 0xe0 && c <= 0xef) { cp = c & 15; remaining = 2; minimum = 0x800; }
                else if (c >= 0xf0 && c <= 0xf4) { cp = c & 7; remaining = 3; minimum = 0x10000; }
                else return false;
                while (remaining--) {
                    if (at == s.size()) return false;
                    unsigned char next = s[at++];
                    if ((next & 0xc0) != 0x80) return false;
                    cp = (cp << 6) | (next & 63);
                }
                if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return false;
                utf8(out, cp);
            }
        }
        return false;
    }
    bool number() {
        size_t begin = at;
        take('-');
        if (!take('0')) {
            if (at == s.size() || s[at] < '1' || s[at] > '9') return false;
            while (at < s.size() && s[at] >= '0' && s[at] <= '9') ++at;
        }
        if (take('.')) {
            size_t start = at;
            while (at < s.size() && s[at] >= '0' && s[at] <= '9') ++at;
            if (at == start) return false;
        }
        if (take('e') || take('E')) {
            if (!take('+')) take('-');
            size_t start = at;
            while (at < s.size() && s[at] >= '0' && s[at] <= '9') ++at;
            if (at == start) return false;
        }
        return std::isfinite(std::strtod(s.substr(begin, at - begin).c_str(), nullptr));
    }
    bool value(unsigned depth) {
        if (depth > 32) return false;
        space();
        if (at == s.size()) return false;
        if (s[at] == '"') { std::string ignored; return string(ignored); }
        if (take('{')) {
            std::set<std::string> names;
            space(); if (take('}')) return true;
            do {
                space(); std::string name;
                if (!string(name) || !names.insert(name).second) return false;
                space(); if (!take(':') || !value(depth + 1)) return false;
                space(); if (take('}')) return true;
            } while (take(','));
            return false;
        }
        if (take('[')) {
            space(); if (take(']')) return true;
            do { if (!value(depth + 1)) return false; space(); if (take(']')) return true; } while (take(','));
            return false;
        }
        for (const char *literal : {"true", "false", "null"}) {
            size_t length = std::char_traits<char>::length(literal);
            if (s.compare(at, length, literal) == 0) { at += length; return true; }
        }
        return number();
    }
public:
    explicit StrictJson(const std::string &input) : s(input) {}
    bool object() { space(); if (at == s.size() || s[at] != '{' || !value(0)) return false; space(); return at == s.size(); }
};
