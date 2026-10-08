// submeter — HANDOFF §9a RGB-LED pin sweep v2 (clearer, longer holds).
// Phases, each 4s lit with a 2.5s dark gap between, looping:
//   1) GPIO4 only   2) GPIO16 only   3) GPIO17 only   4) ALL THREE (expect white if one RGB LED)
// Active-low (LOW = on). Watch the back-of-board RGB lamp; report the color of each of the 4 phases.

#include <Arduino.h>

static const int R = 4, G = 16, B = 17;

static void allOff() {
  pinMode(R, OUTPUT); pinMode(G, OUTPUT); pinMode(B, OUTPUT);
  digitalWrite(R, HIGH); digitalWrite(G, HIGH); digitalWrite(B, HIGH); // active-low off
}

static void phase(const char *label, bool r, bool g, bool b) {
  allOff();
  delay(2500);
  Serial.printf("NOW: %s\n", label);
  if (r) digitalWrite(R, LOW);
  if (g) digitalWrite(G, LOW);
  if (b) digitalWrite(B, LOW);
  delay(4000);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\nLED SWEEP v2 (9a): phases GPIO4, GPIO16, GPIO17, ALL — 4s each, 2.5s gaps");
  allOff();
}

void loop() {
  phase("GPIO4 only", true, false, false);
  phase("GPIO16 only", false, true, false);
  phase("GPIO17 only", false, false, true);
  phase("ALL THREE", true, true, true);
}
