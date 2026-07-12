// Shared constants used by multiple Workbench panes.

/** Default Arduino/ESP32 demo code shown in Flash and Preview panes. */
export const CODE = `#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <ThingSpeak.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SENSOR_PIN 34
#define LED_PIN 2

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
WiFiClient client;

const char* ssid = "WiFi-2.4G";
const char* pwd = "password123";
unsigned long channelNumber = 123456;
const char* apiKey = "YOUR_API_KEY";

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(SENSOR_PIN, INPUT);
}`;
