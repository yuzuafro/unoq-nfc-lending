// NFC equipment lending terminal - display side.
// Python (Edge) calls Bridge.notify("show", pattern, duration_ms); this sketch draws
// the pattern on the 8x13 LED Matrix and sets RGB LED4 to the matching colour.

#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

#include "patterns.h"

Arduino_LED_Matrix matrix;

// Written by the Bridge handler, consumed in loop().
volatile int requestedPattern = -1;
volatile unsigned long requestedMs = 0;
volatile bool hasRequest = false;

int current = -1;               // -1: nothing drawn yet (leave the boot logo alone)
unsigned long startedAt = 0;
unsigned long durationMs = 0;   // 0: until the next request
uint8_t frame[104];

void show(int pattern, int duration_ms) {
  requestedPattern = pattern;
  requestedMs = duration_ms < 0 ? 0 : duration_ms;
  hasRequest = true;
}

// LED4 is active-low: LOW turns a colour channel on.
void setRgb(bool r, bool g, bool b) {
  digitalWrite(LED4_R, r ? LOW : HIGH);
  digitalWrite(LED4_G, g ? LOW : HIGH);
  digitalWrite(LED4_B, b ? LOW : HIGH);
}

void rgbFor(int pattern, bool on) {
  if (!on) { setRgb(false, false, false); return; }
  switch (pattern) {
    case PAT_IDLE:
    case PAT_USER:     setRgb(false, false, true); break;   // blue
    case PAT_CHECKOUT:
    case PAT_RETURN:
    case PAT_TRANSFER: setRgb(false, true, false); break;   // green
    case PAT_UNKNOWN:  setRgb(true, true, false);  break;   // yellow
    case PAT_CAPTURED: setRgb(false, true, true);  break;   // cyan
    default:           setRgb(true, false, false); break;   // red: ERROR, OFFLINE
  }
}

void drawScaled(const uint8_t *src, uint8_t level) {  // level 0..7
  for (int i = 0; i < 104; i++) frame[i] = (src[i] * level + 6) / 7;
  matrix.draw(frame);
}

void render(unsigned long now) {
  unsigned long t = now - startedAt;
  const uint8_t *src = PATTERN_FRAMES[current];

  switch (current) {
    case PAT_IDLE: {
      // Slow breathing: brightness 2..7 over a 3 s cycle.
      unsigned long phase = t % 3000;
      unsigned long tri = phase < 1500 ? phase : 3000 - phase;
      drawScaled(src, 2 + tri * 5 / 1500);
      rgbFor(current, true);
      break;
    }
    case PAT_USER: {
      // Bottom row is a countdown bar that empties over duration_ms.
      memcpy(frame, src, 104);
      int lit = 13;
      if (durationMs > 0) {
        unsigned long left = t < durationMs ? durationMs - t : 0;
        lit = (left * 13 + durationMs - 1) / durationMs;
      }
      for (int c = 0; c < 13; c++) frame[7 * 13 + c] = c < lit ? 7 : 1;
      matrix.draw(frame);
      rgbFor(current, true);
      break;
    }
    case PAT_OFFLINE: {
      bool on = (t / 300) % 2 == 0;
      drawScaled(src, on ? 7 : 0);
      rgbFor(current, on);
      break;
    }
    default:
      matrix.draw(src);
      rgbFor(current, true);
  }
}

void setup() {
  pinMode(LED4_R, OUTPUT);
  pinMode(LED4_G, OUTPUT);
  pinMode(LED4_B, OUTPUT);
  setRgb(false, false, false);

  matrix.begin();
  matrix.setGrayscaleBits(3);  // frame values are 0..7

  Bridge.begin();
  Bridge.provide("show", show);
}

void loop() {
  unsigned long now = millis();
  if (hasRequest) {
    noInterrupts();
    int p = requestedPattern;
    unsigned long ms = requestedMs;
    hasRequest = false;
    interrupts();
    if (p >= 0 && p < PAT_COUNT) {
      current = p;
      durationMs = ms;
      startedAt = now;
    }
  }
  if (current < 0) {
    delay(20);
    return;  // nothing requested yet: don't touch the matrix during boot
  }
  if (current != PAT_IDLE && durationMs > 0 && now - startedAt >= durationMs) {
    // Fall back to IDLE on our own too, in case Python's follow-up is lost.
    current = PAT_IDLE;
    durationMs = 0;
    startedAt = now;
  }
  render(now);
  delay(30);
}
