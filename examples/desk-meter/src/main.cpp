// submeter firmware — HANDOFF §11.4 "prove the display works"
//
// Cycles the panel through red/green/blue, then prints text. Success criterion (§11.4):
// screen cycles R/G/B then shows the text. If the screen stays black, walk §11.5's decision
// tree (wrong TFT_BL -> try 21; backlight on but black -> recheck TFT_DC/TFT_CS/USE_HSPI_PORT;
// colors inverted -> add -DTFT_INVERSION_ON; garbled -> lower SPI_FREQUENCY to 27000000).
//
// This stage is display-only; touch is proven separately in §11.6.

#include <TFT_eSPI.h>

TFT_eSPI tft = TFT_eSPI();

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println();
  Serial.println("submeter bring-up: ST7796 320x480 display test (HANDOFF 11.4)");

  tft.init();
  tft.setRotation(0);  // 0 = 320x480 portrait

  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);  // backlight on (TFT_BL=27 per §9 — verify)

  tft.fillScreen(TFT_RED);   delay(800);
  tft.fillScreen(TFT_GREEN); delay(800);
  tft.fillScreen(TFT_BLUE);  delay(800);

  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.setCursor(10, 20);
  tft.println("ST7796 OK 320x480");
  tft.setCursor(10, 50);
  tft.println("submeter bring-up");

  Serial.println("display test complete — expect R/G/B sweep then text");
}

void loop() {}
