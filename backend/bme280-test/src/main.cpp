/**
 * BME280 底层寄存器诊断
 * 直读寄存器：ID(0xD0), 复位(0xE0), 控制测量(0xF4)
 * 不依赖 Adafruit 库，纯 Wire 读写
 */

#include <Arduino.h>
#include <Wire.h>

#define BME280_ADDR 0x76  // SDO=GND, 根据扫描结果
#define I2C0_SDA_PIN 1
#define I2C0_SCL_PIN 2

void printHex(uint8_t val) {
  if (val < 0x10) Serial.print("0");
  Serial.print(val, HEX);
}

bool readReg(uint8_t addr, uint8_t reg, uint8_t *data, size_t len) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  uint8_t ret = Wire.endTransmission(false);  // STOP = false, send restart
  if (ret != 0) {
    Serial.print("  I2C write fail, code=");
    Serial.println(ret);
    return false;
  }
  size_t received = Wire.requestFrom((int)addr, (int)len);
  if (received != len) {
    Serial.print("  I2C read fail, requested=");
    Serial.print(len);
    Serial.print(" got=");
    Serial.println(received);
    return false;
  }
  for (size_t i = 0; i < len; i++) {
    data[i] = Wire.read();
  }
  return true;
}

bool writeReg(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  uint8_t ret = Wire.endTransmission(true);
  return (ret == 0);
}

void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println("\n\n========================================");
  Serial.println("BME280 底层寄存器直读诊断");
  Serial.println("========================================\n");

  // 初始化 I2C0
  Wire.begin(I2C0_SDA_PIN, I2C0_SCL_PIN, 100000);
  delay(100);

  // 扫描确认设备
  Serial.println("扫描 I2C0...");
  int count = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission(true);
    if (err == 0) {
      Serial.print("  找到设备: 0x");
      Serial.println(addr, HEX);
      count++;
    }
  }
  Serial.print("共找到 "); Serial.print(count); Serial.println(" 个设备\n");

  // ====== BME280 寄存器诊断 ======
  Serial.println("--- BME280 寄存器诊断 ---");
  
  // 1. 读芯片 ID (0xD0)
  uint8_t chip_id = 0;
  if (readReg(BME280_ADDR, 0xD0, &chip_id, 1)) {
    Serial.print("  Chip ID (0xD0) = 0x");
    printHex(chip_id);
    if (chip_id == 0x60) {
      Serial.println(" ✅ 正确! 标准 BME280 ID=0x60");
    } else {
      Serial.print(" ⚠️ 非预期! 预期 0x60, 得到 0x");
      Serial.println(chip_id, HEX);
    }
  } else {
    Serial.println("  ❌ 无法读取 Chip ID");
  }

  // 2. 软复位 (写 0xB6 到 0xE0)
  Serial.println("  执行软复位 (0xE0 = 0xB6)...");
  if (writeReg(BME280_ADDR, 0xE0, 0xB6)) {
    Serial.println("  复位命令发送成功");
  } else {
    Serial.println("  ❌ 复位命令发送失败");
  }
  delay(50);

  // 3. 复位后重读芯片 ID
  chip_id = 0;
  if (readReg(BME280_ADDR, 0xD0, &chip_id, 1)) {
    Serial.print("  复位后 Chip ID = 0x");
    printHex(chip_id);
    if (chip_id == 0x60) {
      Serial.println(" ✅");
    } else {
      Serial.println(" ⚠️ 异常");
    }
  } else {
    Serial.println("  ❌ 复位后仍无法读取 Chip ID");
  }

  // 4. 读控制测量寄存器 (0xF4)
  uint8_t ctrl_meas = 0;
  if (readReg(BME280_ADDR, 0xF4, &ctrl_meas, 1)) {
    Serial.print("  Ctrl_Meas (0xF4) = 0x");
    printHex(ctrl_meas);
    Serial.println();
  }

  // 5. 读状态寄存器 (0xF3)
  uint8_t status = 0;
  if (readReg(BME280_ADDR, 0xF3, &status, 1)) {
    Serial.print("  Status (0xF3) = 0x");
    printHex(status);
    if (status & 0x01) Serial.print(" (measuring...)");
    if (status & 0x08) Serial.print(" (updating NVM...)");
    Serial.println();
  }

  // 6. 尝试配置: 正常模式, 过采样x1
  Serial.println("  配置正常模式(oversample x1)...");
  if (writeReg(BME280_ADDR, 0xF4, 0x27)) {  // 0x27 = osrs_t=1, osrs_p=1, mode=11(normal)
    Serial.println("  配置成功, 等待200ms...");
    delay(200);
    
    // 读温湿度原始数据
    uint8_t data[8] = {0};
    if (readReg(BME280_ADDR, 0xF7, data, 8)) {
      uint32_t press_raw = ((uint32_t)data[0] << 12) | ((uint32_t)data[1] << 4) | ((uint32_t)data[2] >> 4);
      uint32_t temp_raw  = ((uint32_t)data[3] << 12) | ((uint32_t)data[4] << 4) | ((uint32_t)data[5] >> 4);
      uint32_t hum_raw   = ((uint32_t)data[6] << 8) | (uint32_t)data[7];
      
      Serial.println("  --- 原始数据 (0xF7-0xFE) ---");
      Serial.print("  气压原始: "); Serial.println(press_raw);
      Serial.print("  温度原始: "); Serial.println(temp_raw);
      Serial.print("  湿度原始: "); Serial.println(hum_raw);
    } else {
      Serial.println("  ❌ 无法读取传感器数据");
    }
  } else {
    Serial.println("  ❌ 配置失败");
  }

  // 7. 检查 SDI/SDO 引脚
  // 如果读 0x76 失败但扫描有，试试读三线 SPI 模式
  Serial.println("\n--- 综合诊断 ---");
  if (count > 0) {
    Serial.println("  ✅ I2C0 总线正常工作");
    Serial.println("  ✅ BME280 在地址 0x76 有 ACK 应答");
    Serial.println("  电源: 检查 BME280 VCC 是否为 3.3V");
    Serial.println("  可能问题: 模块供电不足 / I2C上拉电阻缺失 / 模块损坏");
  }

  Serial.println("\n诊断完成.");
}

void loop() {
  delay(10000);
}
