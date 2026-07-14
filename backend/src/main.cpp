/**
 * BME280 诊断程序 — I2C 扫描 + 地址测试
 * 
 * 硬件接线（请保持你现在的接法）：
 *   GPIO1 → BME280 SDA (SDI)
 *   GPIO2 → BME280 SCL (SCK)
 *   GPIO8 → SSD1306 SDA
 *   GPIO9 → SSD1306 SCL
 *   GPIO4 → DHT11 DATA (暂时没用)
 * 
 * 烧录后打开串口监视器 115200 看输出
 */
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <Adafruit_SSD1306.h>

Adafruit_SSD1306 display(128, 64, &Wire1);

// I2C0 — BME280 (GPIO1=SDA, GPIO2=SCL)
TwoWire I2C_BME = TwoWire(0);
// I2C1 — SSD1306 (GPIO8=SDA, GPIO9=SCL)
TwoWire I2C_OLED = TwoWire(1);

void scanI2C(TwoWire &wire, const char *busName) {
  byte error, address;
  int nDevices = 0;

  Serial.print("Scanning ");
  Serial.print(busName);
  Serial.println("...");

  for (address = 1; address < 127; address++) {
    wire.beginTransmission(address);
    error = wire.endTransmission();

    if (error == 0) {
      Serial.print("  [FOUND] 0x");
      if (address < 16) Serial.print("0");
      Serial.print(address, HEX);
      Serial.print(" (");
      Serial.print(address, DEC);
      Serial.println(")");

      if (address == 0x3C || address == 0x3D) Serial.println("    -> Likely SSD1306 OLED");
      if (address == 0x76) Serial.println("    -> BME280 (SDO=GND)");
      if (address == 0x77) Serial.println("    -> BME280 (SDO=VCC)");

      nDevices++;
    } else if (error == 2) {
      Serial.print("  [NAK]  0x");
      if (address < 16) Serial.print("0");
      Serial.println(address, HEX);
    }
  }

  if (nDevices == 0)
    Serial.println("  No devices found!\n");
  else
    Serial.printf("  Total: %d device(s)\n\n", nDevices);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n====================================");
  Serial.println("  BME280 DIAGNOSTIC TOOL v1.0");
  Serial.println("====================================\n");

  // 初始化两个 I2C 总线
  I2C_BME.begin(1, 2);   // SDA=GPIO1, SCL=GPIO2
  I2C_OLED.begin(8, 9);  // SDA=GPIO8, SCL=GPIO9

  // 扫描 I2C0 (BME280 总线)
  Serial.println("--- I2C Bus 0 (GPIO1=SDA, GPIO2=SCL) ---");
  scanI2C(I2C_BME, "I2C0");

  // 扫描 I2C1 (OLED 总线)
  Serial.println("--- I2C Bus 1 (GPIO8=SDA, GPIO9=SCL) ---");
  scanI2C(I2C_OLED, "I2C1");

  // 尝试两种地址初始化 BME280
  Serial.println("--- Testing BME280 at 0x76 ---");
  Adafruit_BME280 bme;
  if (bme.begin(0x76, &I2C_BME)) {
    Serial.println("  [OK] BME280 found at 0x76 (SDO=GND)!\n");
    float t = bme.readTemperature();
    float h = bme.readHumidity();
    float p = bme.readPressure() / 100.0F;
    Serial.printf("  Temp=%.1fC  Hum=%.1f%%  Press=%.1fhPa\n", t, h, p);
  } else {
    Serial.println("  [FAIL] BME280 not found at 0x76\n");
  }

  Serial.println("--- Testing BME280 at 0x77 ---");
  if (bme.begin(0x77, &I2C_BME)) {
    Serial.println("  [OK] BME280 found at 0x77 (SDO=VCC)!\n");
    float t = bme.readTemperature();
    float h = bme.readHumidity();
    float p = bme.readPressure() / 100.0F;
    Serial.printf("  Temp=%.1fC  Hum=%.1f%%  Press=%.1fhPa\n", t, h, p);
  } else {
    Serial.println("  [FAIL] BME280 not found at 0x77\n");
  }

  // 尝试初始化 OLED
  Serial.println("--- Testing SSD1306 ---");
  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("  [OK] SSD1306 found at 0x3C\n");
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println("I2C Scan Done!");
    display.display();
  } else {
    Serial.println("  [FAIL] SSD1306 not found at 0x3C\n");
  }

  Serial.println("====================================");
  Serial.println("Diagnostic complete. Check wiring if");
  Serial.println("devices are missing from the scan.");
  Serial.println("====================================");
}

void loop() {
  // 什么都不做，重启看结果
  delay(10000);
}
