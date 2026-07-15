#include <Arduino.h>
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n\n=== HELLO FROM ESP32-S3 ===");
  Serial.println("If you see this, serial works!");
  Serial.println("============================\n");
}
void loop() {
  Serial.println("Tick...");
  delay(1000);
}
