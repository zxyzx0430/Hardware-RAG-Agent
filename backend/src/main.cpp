/**
 * BME280 寄存器级诊断 v2
 * - 开启 GPIO 内部上拉
 * - 50kHz 低速 I2C
 * - 总线恢复序列
 * - 多次重试
 */
#include <Arduino.h>
#include <Wire.h>

#define BME280_ADDR 0x76

// 手动 I2C 总线恢复（SCL 时钟 9 个脉冲释放 SDA）
void recoverI2C() {
  pinMode(1, OUTPUT_OPEN_DRAIN);
  pinMode(2, OUTPUT_OPEN_DRAIN);
  digitalWrite(1, HIGH);
  digitalWrite(2, HIGH);
  delay(10);
  for (int i = 0; i < 9; i++) {
    digitalWrite(2, LOW);
    delayMicroseconds(5);
    digitalWrite(2, HIGH);
    delayMicroseconds(5);
  }
  // 产生 STOP 条件
  digitalWrite(1, LOW);
  delayMicroseconds(5);
  digitalWrite(2, LOW);
  delayMicroseconds(5);
  digitalWrite(2, HIGH);
  delayMicroseconds(5);
  digitalWrite(1, HIGH);
  delayMicroseconds(5);
}

bool readBME280Register(uint8_t reg, uint8_t *value) {
  Wire.beginTransmission(BME280_ADDR);
  Wire.write(reg);
  uint8_t err = Wire.endTransmission(false);
  if (err != 0) {
    Serial.printf("  TX err=%d\n", err);
    return false;
  }
  delay(5);
  if (Wire.requestFrom((uint16_t)BME280_ADDR, (uint8_t)1) != 1) {
    Serial.printf("  RX failed (avail=%d)\n", Wire.available());
    return false;
  }
  *value = Wire.read();
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(3000);
  Serial.println("\n\n==========================================");
  Serial.println("  BME280 寄存器级诊断 v2");
  Serial.println("==========================================");

  // Step 1: 总线恢复
  Serial.println("\n[1] I2C 总线恢复...");
  recoverI2C();
  Serial.println("  ✅ 总线恢复完成");

  // Step 2: 初始化 I2C0 (GPIO1=SDA, GPIO2=SCL) + 内部上拉
  Serial.println("\n[2] 初始化 I2C0 (GPIO1=SDA, GPIO2=SCL) @ 50kHz");
  Wire.begin(1, 2, 50000);  // SDA=GPIO1, SCL=GPIO2, 50kHz
  // 开启内部上拉
  pinMode(1, INPUT_PULLUP);
  pinMode(2, INPUT_PULLUP);
  delay(100);

  // Step 3: 扫描地址 0x76
  Serial.println("\n[3] 测试 0x76 地址 ACK...");
  Wire.beginTransmission(BME280_ADDR);
  uint8_t err = Wire.endTransmission();
  Serial.printf("  结果: %s (err=%d)\n", (err == 0) ? "✅ ACK" : "❌ NAK", err);

  // Step 4: 尝试读取 Chip ID
  Serial.println("\n[4] 读取寄存器 0xD0 (Chip ID)...");
  uint8_t id = 0;
  bool ok = false;
  for (int attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      Serial.printf("  重试 #%d...\n", attempt + 1);
      delay(50);
    }
    ok = readBME280Register(0xD0, &id);
    if (ok) break;
  }

  if (ok) {
    Serial.printf("  Chip ID = 0x%02X\n", id);
    if (id == 0x60) {
      Serial.println("  ✅ 确认是 BME280!");
    } else if (id == 0x58) {
      Serial.println("  ⚠️ 是 BMP280 (不是 BME280)");
    } else {
      Serial.printf("  ⚠️ 未知设备 ID=0x%02X (BME280 应为 0x60)\n", id);
    }
  } else {
    Serial.println("  ❌ 读取失败！");
  }

  // Step 5: 尝试不同时钟速度
  Serial.println("\n[5] 尝试不同时钟速度...");
  int speeds[] = {10000, 50000, 100000, 200000};
  for (int s : speeds) {
    Wire.begin(1, 2, s);
    delay(20);
    Wire.beginTransmission(BME280_ADDR);
    uint8_t e = Wire.endTransmission();
    if (e == 0) {
      Wire.beginTransmission(BME280_ADDR);
      Wire.write(0xD0);
      Wire.endTransmission(false);
      delay(5);
      Wire.requestFrom((uint16_t)BME280_ADDR, (uint8_t)1);
      if (Wire.available()) {
        uint8_t val = Wire.read();
        Serial.printf("  %6d Hz: ACK=✅  ID=0x%02X\n", s, val);
      } else {
        Serial.printf("  %6d Hz: ACK=✅  RX=❌\n", s);
      }
    } else {
      Serial.printf("  %6d Hz: ACK=❌\n", s);
    }
  }

  // Step 6: 尝试 I2C 地址 0x77
  Serial.println("\n[6] 检查地址 0x77 (SDO=VCC)...");
  Wire.beginTransmission(0x77);
  err = Wire.endTransmission();
  if (err == 0) {
    Serial.println("  0x77: ✅ ACK - 试试读取 ID");
    Wire.beginTransmission(0x77);
    Wire.write(0xD0);
    Wire.endTransmission(false);
    delay(5);
    Wire.requestFrom((uint16_t)0x77, (uint8_t)1);
    if (Wire.available()) {
      uint8_t id77 = Wire.read();
      Serial.printf("  0x77 ID = 0x%02X\n", id77);
    }
  } else {
    Serial.println("  0x77: ❌ NAK");
  }

  Serial.println("\n==========================================");
  Serial.println("  诊断结束 - 请查看以上结果");
  Serial.println("==========================================\n");
}

void loop() {}
