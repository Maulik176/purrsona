#pragma once
// Desktop drawing adapter. The screen layout and fonts come from the board firmware.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>
#define PROGMEM
#include <Fonts/glcdfont.c>
#include <Fonts/Font16.c>

constexpr int OUTPUT = 1, HIGH = 1, TL_DATUM = 0;
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline void delay(int) {}

struct ScreenText { std::string value; int x, y, width, height; };
struct ScreenFrame {
    std::vector<uint16_t> pixels = std::vector<uint16_t>(320 * 170);
    std::vector<ScreenText> texts;
};
inline ScreenFrame displayedFrame;

class TFT_eSPI {
protected:
    ScreenFrame frame;
    uint16_t foreground = 0xffff, background = 0;
    void pixel(int x, int y, uint16_t color) {
        if (x >= 0 && x < 320 && y >= 0 && y < 170) frame.pixels[y * 320 + x] = color;
    }
public:
    void init() {}
    void setRotation(int) {}
    void setTextDatum(int) {}
    void setTextColor(uint16_t fg, uint16_t bg) { foreground = fg; background = bg; }
    void fillScreen(uint16_t color) { std::fill(frame.pixels.begin(), frame.pixels.end(), color); frame.texts.clear(); }
    int textWidth(const char *value, int fontNumber) {
        int width = 0;
        for (const unsigned char *c = reinterpret_cast<const unsigned char *>(value); *c; ++c) {
            unsigned ch = *c >= 32 && *c < 128 ? *c : '?';
            width += fontNumber == 2 ? widtbl_f16[ch - 32] : 6;
        }
        return width;
    }
    int drawString(const char *value, int x, int y, int fontNumber) {
        int cursor = x;
        for (const unsigned char *c = reinterpret_cast<const unsigned char *>(value); *c; ++c) {
            unsigned ch = *c >= 32 && *c < 128 ? *c : '?';
            int width = fontNumber == 2 ? widtbl_f16[ch - 32] : 6;
            int height = fontNumber == 2 ? 16 : 8;
            for (int row = 0; row < height; ++row) for (int col = 0; col < width; ++col) {
                bool ink = fontNumber == 2
                    ? (col < ((width + 6) / 8) * 8 &&
                       (chrtbl_f16[ch - 32][row * ((width + 6) / 8) + col / 8] & (0x80 >> (col % 8))))
                    : (col < 5 && (font[ch * 5 + col] & (1 << row)));
                pixel(cursor + col, y + row, ink ? foreground : background);
            }
            cursor += width;
        }
        frame.texts.push_back({value, x, y, cursor - x, fontNumber == 2 ? 16 : 8});
        return cursor - x;
    }
    void drawLine(int x, int y, int endX, int endY, uint16_t color) {
        int dx = abs(endX - x), sx = x < endX ? 1 : -1;
        int dy = -abs(endY - y), sy = y < endY ? 1 : -1, error = dx + dy;
        for (;;) {
            pixel(x, y, color);
            if (x == endX && y == endY) break;
            int twice = 2 * error;
            if (twice >= dy) { error += dy; x += sx; }
            if (twice <= dx) { error += dx; y += sy; }
        }
    }
    void drawFastHLine(int x, int y, int width, uint16_t color) { drawLine(x, y, x + width - 1, y, color); }
    void fillCircle(int x, int y, int radius, uint16_t color) {
        for (int row = -radius; row <= radius; ++row) for (int col = -radius; col <= radius; ++col)
            if (col * col + row * row <= radius * radius) pixel(x + col, y + row, color);
    }
    void drawCircle(int x, int y, int radius, uint16_t color) {
        for (int row = -radius; row <= radius; ++row) for (int col = -radius; col <= radius; ++col)
            if (std::abs(std::sqrt(float(col * col + row * row)) - radius) < 0.6f) pixel(x + col, y + row, color);
    }
    void roundRect(int x, int y, int width, int height, int radius, uint16_t color, bool fill) {
        auto inside = [=](int col, int row) {
            if (col < 0 || row < 0 || col >= width || row >= height) return false;
            int dx = std::max({radius - col, col - (width - radius - 1), 0});
            int dy = std::max({radius - row, row - (height - radius - 1), 0});
            return dx * dx + dy * dy <= radius * radius;
        };
        for (int row = 0; row < height; ++row) for (int col = 0; col < width; ++col)
            if (inside(col, row) && (fill || !inside(col-1, row) || !inside(col+1, row) ||
                                    !inside(col, row-1) || !inside(col, row+1))) pixel(x+col, y+row, color);
    }
    void fillRoundRect(int x, int y, int w, int h, int r, uint16_t color) { roundRect(x,y,w,h,r,color,true); }
    void drawRoundRect(int x, int y, int w, int h, int r, uint16_t color) { roundRect(x,y,w,h,r,color,false); }
};
class TFT_eSprite : public TFT_eSPI {
public:
    explicit TFT_eSprite(TFT_eSPI *) {}
    void setColorDepth(int) {}
    void *createSprite(int, int) { return frame.pixels.data(); }
    void fillSprite(uint16_t color) { fillScreen(color); }
    void pushSprite(int, int) { displayedFrame = frame; }
};
