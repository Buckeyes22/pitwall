// submeter firmware — HANDOFF §11.6 touch (CALIBRATED, persistent) — TFT_eSPI, shared HSPI bus
//
// Touch shares the display HSPI bus on the 3248S035 (SCLK14/MOSI13/MISO12); only touch CS=33 &
// IRQ=36 are separate, so TFT_eSPI's built-in XPT2046 driver reads it via -DTOUCH_CS=33.
//
// The touch axes are rotated 90° vs the panel, so a calibration transform is required. TOUCH_CAL
// below was captured on THIS board at setRotation(0) via tft.calibrateTouch() (2026-05-28). Baking
// it in with setTouch() means touch works on boot with no recalibration. Re-run the calibration
// build if the panel/rotation ever changes.

#include <TFT_eSPI.h>

TFT_eSPI tft = TFT_eSPI();

// TFT_eSPI touch calibration for this panel @ rotation 0 (xMin, xMax, yMin, yMax, rotation-flag)
static const uint16_t TOUCH_CAL[5] = { 263, 3547, 293, 3586, 4 };

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("submeter bring-up: calibrated touch test (HANDOFF 11.6) — cal baked in");

  tft.init();
  tft.setRotation(0);
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);

  tft.setTouch((uint16_t *)TOUCH_CAL);     // apply baked calibration — no on-boot recalibration

  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.setCursor(8, 20); tft.println("CALIBRATED TOUCH");
  tft.setCursor(8, 50); tft.println("dots track stylus");
  Serial.println("ready — calibration applied; dots should land under the stylus");
}

void loop() {
  uint16_t x, y;
  if (tft.getTouch(&x, &y)) {
    tft.fillCircle(x, y, 4, TFT_GREEN);
    Serial.printf("screen x=%3u y=%3u\n", x, y);
  }
}
