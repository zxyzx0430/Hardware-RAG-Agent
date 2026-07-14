/**
 * BME280 底层寄存器读取测试
 * 不依赖 Adafruit 库，直接 Wire 读取 Chip ID
 */
#include <Wire.h>

#define BME280_ADDR 0x76

void setup() {
  Serial.begin(115200);
  delay(2000);  // 等串口连上

  Serial.println("\n\n========================================");
  Serial.println("  BME280 RAW REGISTER TEST");
  Serial.println("========================================");

  // 初始化 I2C0: GPIO1=SDA, GPIO2=SCL
  Wire.begin(1, 2);
  Wire.setClock(100000);  // 标准 100kHz
  delay(100);

  // 1. 先试地址扫描确认设备在不在
  Serial.println("\n[1] 扫描 0x76 地址...");
  Wire.beginTransmission(BME280_ADDR);
  uint8_t err = Wire.endTransmission();
  if (err == 0) {
    Serial.println("  ✅ 0x76 地址 ACK 成功");
  } else {
    Serial.printf("  ❌ 0x76 地址 NAK (错误码: %d)\n", err);
  }

  // 2. 读 Chip ID (寄存器 0xD0)
  Serial.println("\n[2] 读取 Chip ID (reg 0xD0)...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xD0);
  err = Wire.endTransmission();
  if (err != 0) {
    Serial.printf("  ❌ 写寄存器地址失败: %d\n", err);
  } else {
    delay(10);
    Wire.requestFrom(BME280_ADDR, (uint8_t)1);
    if (Wire.available()) {
      uint8_t id = Wire.read();
      Serial.printf("  Chip ID = 0x%02X\n", id);
      if (id == 0x60) {
        Serial.println("  ✅ 确认是 BME280/BMP280!");
      } else {
        Serial.println("  ⚠️ ID 不是 0x60，可能是其他设备");
      }
    } else {
      Serial.println("  ❌ requestFrom 无数据返回");
    }
  }

  // 3. 读复位状态 (寄存器 0xE0)
  Serial.println("\n[3] 读取复位状态 (reg 0xE0)...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xE0);
  Wire.endTransmission();
  delay(10);
  Wire.requestFrom(BME280_ADDR, (uint8_t)1);
  if (Wire.available()) {
    uint8_t rst = Wire.read();
    Serial.printf("  Reset status = 0x%02X\n", rst);
  }

  // 4. 读控制湿度寄存器 (reg 0xF2)
  Serial.println("\n[4] 读取 ctrl_hum (reg 0xF2)...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xF2);
  Wire.endTransmission();
  delay(10);
  Wire.requestFrom(BME280_ADDR, (uint8_t)1);
  if (Wire.available()) {
    uint8_t ctrl_hum = Wire.read();
    Serial.printf("  ctrl_hum = 0x%02X\n", ctrl_hum);
  }

  // 5. 读控制测量寄存器 (reg 0xF4)
  Serial.println("\n[5] 读取 ctrl_meas (reg 0xF4)...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xF4);
  Wire.endTransmission();
  delay(10);
  Wire.requestFrom(BME280_ADDR, (uint8_t)1);
  if (Wire.available()) {
    uint8_t ctrl_meas = Wire.read();
    Serial.printf("  ctrl_meas = 0x%02X\n", ctrl_meas);
  }

  // 6. 尝试软复位 (写 0xB6 到 reg 0xE0)
  Serial.println("\n[6] 执行软复位...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xE0);
  Wire.write(0xB6);
  err = Wire.endTransmission();
  Serial.printf("  复位指令发送: %s\n", (err == 0) ? "✅ OK" : "❌ FAIL");
  delay(100);  // 等复位完成

  // 7. 复位后再读 Chip ID
  Serial.println("\n[7] 复位后重读 Chip ID...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xD0);
  Wire.endTransmission();
  delay(10);
  Wire.requestFrom(BME280_ADDR, (uint8_t)1);
  if (Wire.available()) {
    uint8_t id2 = Wire.read();
    Serial.printf("  Chip ID = 0x%02X\n", id2);
    if (id2 == 0x60) {
      Serial.println("  ✅ 复位后确认是 BME280!");
    } else {
      Serial.println("  ⚠️ 复位后 ID 仍然不是 0x60");
    }
  }

  // 8. 读温度原始值 (regs 0xFA, 0xFB, 0xFC)
  Serial.println("\n[8] 读取温度原始值 (regs 0xFA-0xFC)...");
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(0xFA);
  Wire.endTransmission();
  delay(10);
  Wire.requestFrom(BME280_ADDR, (uint8_t)3);
  if (Wire.available() >= 3) {
    uint32_t raw_temp = ((uint32_t)Wire.read() << 12) | ((uint32_t)Wire.read() << 4) | (Wire.read() >> 4);
    Serial.printf("  原始温度值: %lu\n", raw_temp);
    if (raw_temp > 0 && raw_temp < 0xFFFFF) {
      Serial.println("  ✅ 温度数据有效!");
    } else {
      Serial.println("  ⚠️ 温度数据异常");
    }
  }

  Serial.println("\n========================================");
  Serial.println("  测试完成");
  Serial.println("========================================\n");
}

void loop() {}
