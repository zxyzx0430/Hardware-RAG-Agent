/**
 * ============================================================
 *  项目名称: ESP32-S3 智能环境监测与控制系统 v1.0
 *  硬件平台: ESP32-S3 (ESP32-S3-DevKitC-1)
 *  开发框架: Arduino (PlatformIO)
 *  日    期: 2026-07-15
 *  
 *  硬件清单:
 *    - MCU:      ESP32-S3
 *    - 传感器:   BME280 (温度+湿度+气压)
 *               DHT11  (温度+湿度)
 *               光敏模块 (模拟量AO + 数字量DO)
 *    - 执行器:   有源蜂鸣器 × 2
 *    - 输入:     四独立按键模块 (K1~K4)
 *    - 显示:     SSD1306 OLED 128×64 (I2C)
 *  
 *  功能概览:
 *    - OLED中文菜单显示 (6个页面)
 *    - 多传感器数据采集与对比
 *    - 按键导航与控制
 *    - 蜂鸣器报警 (自动/手动)
 *    - 报警阈值设置
 * ============================================================
 */

#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <DHT.h>
#include <U8g2lib.h>

// ==================== 引脚定义 ====================
// I2C总线1: BME280 (GPIO1=SDA, GPIO2=SCL)
#define PIN_BME_SDA      1
#define PIN_BME_SCL      2
// I2C总线2: SSD1306 (GPIO8=SDA, GPIO9=SCL)
#define PIN_OLED_SDA     8
#define PIN_OLED_SCL     9

// 蜂鸣器
#define PIN_BUZZER1      5
#define PIN_BUZZER2      6

// 按键 (模块低电平有效)
#define PIN_KEY_K1       12
#define PIN_KEY_K2       13
#define PIN_KEY_K3       14
#define PIN_KEY_K4       15

// DHT11
#define PIN_DHT          4
#define DHT_TYPE         DHT11

// 光敏模块
#define PIN_LIGHT_AO     10   // 模拟输出 (ADC)
#define PIN_LIGHT_DO     11   // 数字输出 (阈值比较)

// ==================== I2C 地址 ====================
#define BME280_ADDR      0x76   // 7位地址 (SDO接GND)
#define SSD1306_ADDR     0x3C   // 7位地址 (常见)

// ==================== 全局对象 ====================
TwoWire I2C_BME(1);   // 使用Wire1 (GPIO1/GPIO2)
TwoWire I2C_OLED(0);  // 使用Wire0 (GPIO8/GPIO9)
Adafruit_BME280 bme;
DHT dht(PIN_DHT, DHT_TYPE);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);

// ==================== 传感器数据缓存 ====================
struct {
  float temperature = NAN;
  float humidity    = NAN;
  float pressure    = NAN;   // hPa
  float altitude    = NAN;   // 米
  bool  valid       = false;
} bme_data;

struct {
  float temperature = NAN;
  float humidity    = NAN;
  bool  valid       = false;
} dht_data;

struct {
  int   analog     = 0;     // 0-4095
  bool  digital    = false; // 阈值比较结果
  int   percent    = 0;     // 0-100%
} light_data;

// ==================== 按键状态 ====================
struct Button {
  uint8_t pin;
  bool    last_state;
  bool    current_state;
  bool    rising_edge;   // 按键释放瞬间 (由HIGH→LOW为按下)
  bool    falling_edge;  // 按键按下瞬间
  unsigned long last_debounce_time;
  unsigned long press_start_time;
  bool    long_press_triggered;
};
Button btn_k1, btn_k2, btn_k3, btn_k4;
const unsigned long DEBOUNCE_MS = 30;
const unsigned long LONG_PRESS_MS = 800;

// ==================== 菜单系统 ====================
enum MenuPage : uint8_t {
  PAGE_MAIN     = 0,    // 主页 - 综合概览
  PAGE_BME280   = 1,    // BME280 详细数据
  PAGE_DHT11    = 2,    // DHT11 详细数据
  PAGE_LIGHT    = 3,    // 光敏数据
  PAGE_SETTINGS = 4,    // 报警阈值设置
  PAGE_BUZZER   = 5,    // 蜂鸣器控制
  PAGE_COUNT    = 6
};
MenuPage current_page = PAGE_MAIN;
MenuPage prev_page = PAGE_MAIN;

// ==================== 蜂鸣器模式 ====================
enum BuzzerMode : uint8_t {
  BUZZER_AUTO   = 0,    // 自动报警模式
  BUZZER_MANUAL = 1     // 手动控制模式
};
BuzzerMode buzzer_mode = BUZZER_AUTO;
bool buzzer1_on = false;
bool buzzer2_on = false;

// ==================== 报警阈值 ====================
float temp_alarm_high  = 35.0;  // 高温报警 °C
float temp_alarm_low   = 2.0;   // 低温报警 °C
float hum_alarm_high   = 80.0;  // 高湿报警 %
int   light_alarm_low  = 20;    // 低光照报警 %
bool  alarm_enable     = true;

// 报警状态
bool alarm_temp_high  = false;
bool alarm_temp_low   = false;
bool alarm_hum_high   = false;
bool alarm_light_low  = false;

// ==================== 设置页面状态 ====================
int   settings_cursor = 0;        // 当前选中项
const int SETTINGS_COUNT = 6;     // 可设置项数量
bool  setting_editing = false;    // 是否正在编辑值
int   settings_page_offset = 0;   // 设置页面滚动

// ==================== 定时器 ====================
unsigned long last_sensor_ms  = 0;
const unsigned long SENSOR_INTERVAL_MS = 2000;  // 2秒读传感器
unsigned long last_display_ms = 0;
const unsigned long DISPLAY_INTERVAL_MS = 200;  // 200ms刷新显示
unsigned long last_buzzer_ms  = 0;
const unsigned long BUZZER_BLINK_MS = 300;      // 蜂鸣器报警闪烁

// ==================== 系统状态 ====================
unsigned long boot_ms = 0;
char uptime_str[20];

// ==================== 函数声明 ====================
void setupPins();
void initSensors();
void initDisplay();
void readSensors();
void processButtons();
void updateDisplay();
void updateBuzzer();
void checkAlarms();

void drawPageMain();
void drawPageBME280();
void drawPageDHT11();
void drawPageLight();
void drawPageSettings();
void drawPageBuzzer();

void handleButtonK1(bool long_press);
void handleButtonK2(bool long_press);
void handleButtonK3(bool long_press);
void handleButtonK4(bool long_press);
void playBuzzerTone(int buzzer_pin, int duration_ms);
void getUptimeString(char* buf, size_t len);

// ============================================================
//  SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println();
  Serial.println(F("=========================================="));
  Serial.println(F("  ESP32-S3 智能环境监测与控制系统 v1.0"));
  Serial.println(F("=========================================="));

  boot_ms = millis();

  setupPins();
  initDisplay();
  initSensors();

  // 开机提示音
  playBuzzerTone(PIN_BUZZER1, 150);
  delay(100);
  playBuzzerTone(PIN_BUZZER2, 150);

  Serial.println(F("系统启动完成!"));
}

// ============================================================
//  LOOP
// ============================================================
void loop() {
  unsigned long now = millis();

  // 1. 定时读取传感器
  if (now - last_sensor_ms >= SENSOR_INTERVAL_MS) {
    readSensors();
    checkAlarms();
    last_sensor_ms = now;
  }

  // 2. 按键扫描
  processButtons();

  // 3. 蜂鸣器更新
  updateBuzzer();

  // 4. 刷新OLED
  if (now - last_display_ms >= DISPLAY_INTERVAL_MS) {
    updateDisplay();
    last_display_ms = now;
  }

  // 5. 串口日志输出 (每5秒)
  static unsigned long last_log_ms = 0;
  if (now - last_log_ms >= 5000) {
    printSerialLog();
    last_log_ms = now;
  }
}
}

// ============================================================
//  初始化函数
// ============================================================

/**
 * 配置所有GPIO引脚
 */
void setupPins() {
  // 蜂鸣器 - 输出
  pinMode(PIN_BUZZER1, OUTPUT);
  pinMode(PIN_BUZZER2, OUTPUT);
  digitalWrite(PIN_BUZZER1, LOW);
  digitalWrite(PIN_BUZZER2, LOW);

  // 按键 - 输入上拉 (模块为低电平有效)
  pinMode(PIN_KEY_K1, INPUT_PULLUP);
  pinMode(PIN_KEY_K2, INPUT_PULLUP);
  pinMode(PIN_KEY_K3, INPUT_PULLUP);
  pinMode(PIN_KEY_K4, INPUT_PULLUP);

  // DHT11 数据线
  pinMode(PIN_DHT, INPUT);

  // 光敏模块
  pinMode(PIN_LIGHT_AO, INPUT);
  pinMode(PIN_LIGHT_DO, INPUT);

  // 初始化按键结构体
  btn_k1 = {PIN_KEY_K1, HIGH, HIGH, false, false, 0, 0, false};
  btn_k2 = {PIN_KEY_K2, HIGH, HIGH, false, false, 0, 0, false};
  btn_k3 = {PIN_KEY_K3, HIGH, HIGH, false, false, 0, 0, false};
  btn_k4 = {PIN_KEY_K4, HIGH, HIGH, false, false, 0, 0, false};

  Serial.println(F("[OK] 引脚初始化完成"));
}

/**
 * 初始化OLED显示
 */
void initDisplay() {
  I2C_OLED.begin(PIN_OLED_SDA, PIN_OLED_SCL, 400000);
  u8g2.setBusClock(400000);
  u8g2.begin();
  
  // 显示开机画面
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  u8g2.drawStr(4, 20, "智能环境系统");
  u8g2.setFont(u8g2_font_6x10_tf);
  u8g2.drawStr(4, 38, "Initializing...");
  u8g2.sendBuffer();

  Serial.println(F("[OK] OLED 初始化完成 (I2C addr 0x3C)"));
}

/**
 * 初始化传感器
 */
void initSensors() {
  // --- BME280 (I2C1: GPIO1/GPIO2) ---
  I2C_BME.begin(PIN_BME_SDA, PIN_BME_SCL, 100000);
  delay(10);
  if (bme.begin(BME280_ADDR, &I2C_BME)) {
    bme.setSampling(Adafruit_BME280::MODE_FORCED,
                    Adafruit_BME280::SAMPLING_X1,  // 温度
                    Adafruit_BME280::SAMPLING_X1,  // 气压
                    Adafruit_BME280::SAMPLING_X1,  // 湿度
                    Adafruit_BME280::FILTER_OFF);
    bme_data.valid = true;
    Serial.println(F("[OK] BME280 初始化成功 (addr 0x76, SDA=GPIO1, SCL=GPIO2)"));
  } else {
    Serial.println(F("[ERR] BME280 初始化失败! 请检查接线"));
  }

  // --- DHT11 (GPIO4) ---
  dht.begin();
  // DHT11 需要稳定时间
  dht_data.valid = true;
  Serial.println(F("[OK] DHT11 初始化完成 (DATA=GPIO4)"));

  // --- OLED 显示就绪 ---
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  u8g2.drawStr(4, 20, "系统就绪!");
  char buf[30];
  snprintf(buf, sizeof(buf), "BME280: %s", bme_data.valid ? "OK" : "FAIL");
  u8g2.drawStr(4, 38, buf);
  u8g2.setFont(u8g2_font_6x10_tf);
  u8g2.drawStr(4, 56, "4按键操控 | 双蜂鸣器");
  u8g2.sendBuffer();
  delay(1500);
}

// ============================================================
//  传感器读取
// ============================================================

void readSensors() {
  static int fail_count = 0;

  // --- BME280 ---
  if (bme_data.valid) {
    // Forced 模式下每次读取前需要触发测量
    bme.takeForcedMeasurement();
    
    bme_data.temperature = bme.readTemperature();
    bme_data.humidity    = bme.readHumidity();
    bme_data.pressure    = bme.readPressure() / 100.0F;  // Pa → hPa
    bme_data.altitude    = bme.readAltitude(1013.25F);

    // 校验数据有效性
    if (isnan(bme_data.temperature) || isnan(bme_data.humidity)) {
      fail_count++;
      if (fail_count > 5) bme_data.valid = false;
    } else {
      fail_count = 0;
    }
  } else {
    // 尝试重连
    static unsigned long last_reconnect = 0;
    if (millis() - last_reconnect > 30000) {
      if (bme.begin(BME280_ADDR, &I2C_BME)) {
        bme_data.valid = true;
        Serial.println(F("[INFO] BME280 重新连接成功"));
      }
      last_reconnect = millis();
    }
  }

  // --- DHT11 ---
  if (dht_data.valid) {
    dht_data.temperature = dht.readTemperature();
    dht_data.humidity    = dht.readHumidity();
    
    if (isnan(dht_data.temperature) || isnan(dht_data.humidity)) {
      // DHT11 偶尔失败是正常的
      static bool dht_warned = false;
      if (!dht_warned) {
        Serial.println(F("[WARN] DHT11 读取失败 (将自动重试)"));
        dht_warned = true;
      }
    }
  }

  // --- 光敏模块 ---
  light_data.analog  = analogRead(PIN_LIGHT_AO);
  light_data.digital = digitalRead(PIN_LIGHT_DO);
  // 将ADC值映射到百分比 (ESP32-S3 ADC 12位 0-4095)
  light_data.percent = map(constrain(light_data.analog, 0, 4095), 0, 4095, 0, 100);
  // AO值越高=光照越强 (常见模块)，如果反了可以反转
}

/**
 * 检查报警条件
 */
void checkAlarms() {
  if (!alarm_enable) {
    alarm_temp_high = alarm_temp_low = alarm_hum_high = alarm_light_low = false;
    return;
  }

  // 温度报警 (使用BME280数据，更精确)
  if (!isnan(bme_data.temperature)) {
    alarm_temp_high = (bme_data.temperature >= temp_alarm_high);
    alarm_temp_low  = (bme_data.temperature <= temp_alarm_low);
  } else if (!isnan(dht_data.temperature)) {
    // 备用使用DHT11
    alarm_temp_high = (dht_data.temperature >= temp_alarm_high);
    alarm_temp_low  = (dht_data.temperature <= temp_alarm_low);
  }

  // 湿度报警
  if (!isnan(bme_data.humidity)) {
    alarm_hum_high = (bme_data.humidity >= hum_alarm_high);
  }

  // 光照报警
  alarm_light_low = (light_data.percent < light_alarm_low);
}

/**
 * 打印串口日志 (每5秒输出一次)
 */
void printSerialLog() {
  getUptimeString(uptime_str, sizeof(uptime_str));
  
  Serial.println();
  Serial.println(F("┌─────────────────────────────────────────────────┐"));
  Serial.print  (F("│ ⏱ "));
  Serial.print(uptime_str);
  Serial.println(F("                                    │"));
  Serial.println(F("├───────────────┬──────────┬──────────────────────┤"));
  Serial.println(F("│  传感器       │  数值    │  状态                │"));
  Serial.println(F("├───────────────┼──────────┼──────────────────────┤"));

  // BME280
  Serial.print(F("│ BME280 温度   │  "));
  if (!isnan(bme_data.temperature)) {
    Serial.print(bme_data.temperature, 1); Serial.print(" °C");
  } else { Serial.print(F("N/A      ")); }
  Serial.print(F("  │  "));
  Serial.print(bme_data.valid ? F("在线") : F("离线"));
  Serial.println(F("                  │"));

  Serial.print(F("│ BME280 湿度   │  "));
  if (!isnan(bme_data.humidity)) {
    Serial.print(bme_data.humidity, 1); Serial.print(" %");
  } else { Serial.print(F("N/A      ")); }
  Serial.println(F("  │                     │"));

  Serial.print(F("│ BME280 气压   │  "));
  if (!isnan(bme_data.pressure)) {
    Serial.print(bme_data.pressure, 2); Serial.print(" hPa");
  } else { Serial.print(F("N/A        ")); }
  Serial.println(F("  │                     │"));

  // DHT11
  Serial.print(F("│ DHT11 温度    │  "));
  if (!isnan(dht_data.temperature)) {
    Serial.print(dht_data.temperature, 1); Serial.print(" °C");
  } else { Serial.print(F("N/A      ")); }
  Serial.println(F("  │                     │"));

  Serial.print(F("│ DHT11 湿度    │  "));
  if (!isnan(dht_data.humidity)) {
    Serial.print(dht_data.humidity, 1); Serial.print(" %");
  } else { Serial.print(F("N/A      ")); }
  Serial.println(F("  │                     │"));

  // 光敏
  Serial.print(F("│ 光照强度      │  "));
  Serial.print(light_data.percent); Serial.print(" %  ");
  Serial.print(F(" (ADC:"));
  Serial.print(light_data.analog);
  Serial.print(F(")"));
  Serial.print(F("  │  "));
  Serial.print(light_data.digital ? F("亮") : F("暗"));
  Serial.println(F("                  │"));

  Serial.println(F("├───────────────┴──────────┴──────────────────────┤"));
  Serial.print(F("│ 蜂鸣器模式: "));
  Serial.print(buzzer_mode == BUZZER_AUTO ? F("自动报警") : F("手动控制"));
  Serial.print(F("  报警: "));
  if (alarm_temp_high || alarm_temp_low || alarm_hum_high || alarm_light_low) {
    Serial.print(F("⚠ 有报警! "));
    if (alarm_temp_high) Serial.print(F("[高温]"));
    if (alarm_temp_low)  Serial.print(F("[低温]"));
    if (alarm_hum_high)  Serial.print(F("[高湿]"));
    if (alarm_light_low) Serial.print(F("[低光照]"));
  } else {
    Serial.print(F("正常 ✓"));
  }
  Serial.println(F("              │"));
  Serial.println(F("└─────────────────────────────────────────────────┘"));
}

// ============================================================
//  按键处理
// ============================================================

void processButtons() {
  unsigned long now = millis();

  // 处理每个按键
  processSingleButton(&btn_k1, now);
  processSingleButton(&btn_k2, now);
  processSingleButton(&btn_k3, now);
  processSingleButton(&btn_k4, now);

  // 检测到按键动作后执行对应功能
  if (btn_k1.falling_edge) handleButtonK1(false);
  if (btn_k1.rising_edge && btn_k1.long_press_triggered) handleButtonK1(true);
  btn_k1.falling_edge = false;
  btn_k1.rising_edge = false;

  if (btn_k2.falling_edge) handleButtonK2(false);
  if (btn_k2.rising_edge && btn_k2.long_press_triggered) handleButtonK2(true);
  btn_k2.falling_edge = false;
  btn_k2.rising_edge = false;

  if (btn_k3.falling_edge) handleButtonK3(false);
  if (btn_k3.rising_edge) { /* K3无长按功能 */ }
  btn_k3.falling_edge = false;
  btn_k3.rising_edge = false;

  if (btn_k4.falling_edge) handleButtonK4(false);
  if (btn_k4.rising_edge && btn_k4.long_press_triggered) handleButtonK4(true);
  btn_k4.falling_edge = false;
  btn_k4.rising_edge = false;
}

void processSingleButton(Button* btn, unsigned long now) {
  bool reading = (digitalRead(btn->pin) == LOW);  // 按下列为true
  
  // 去抖
  if (reading != btn->last_state) {
    btn->last_debounce_time = now;
  }
  
  if ((now - btn->last_debounce_time) > DEBOUNCE_MS) {
    if (reading != btn->current_state) {
      btn->current_state = reading;
      
      if (reading) {
        // 按下瞬间
        btn->falling_edge = true;
        btn->press_start_time = now;
        btn->long_press_triggered = false;
      } else {
        // 释放瞬间
        btn->rising_edge = true;
        if (now - btn->press_start_time < LONG_PRESS_MS) {
          btn->long_press_triggered = false; // 短按
        }
      }
    }
  }
  
  btn->last_state = reading;
  
  // 检测长按 (持续按住超过阈值)
  if (btn->current_state && !btn->long_press_triggered) {
    if (now - btn->press_start_time >= LONG_PRESS_MS) {
      btn->long_press_triggered = true;
      btn->rising_edge = true;  // 触发长按事件
    }
  }
}

// ==================== 按键事件处理 ====================

// K1: 上一页 / 设置值减小
void handleButtonK1(bool long_press) {
  (void)long_press;
  playBuzzerTone(PIN_BUZZER2, 50);
  
  if (current_page == PAGE_SETTINGS && setting_editing) {
    // 正在编辑设置：减小数值
    adjustSetting(-1);
  } else {
    // 普通模式：上一页
    if (current_page > 0) {
      prev_page = current_page;
      current_page = (MenuPage)((uint8_t)current_page - 1);
    } else {
      current_page = (MenuPage)(PAGE_COUNT - 1);
    }
  }
}

// K2: 确认/进入 / 切换蜂鸣器
void handleButtonK2(bool long_press) {
  if (long_press) {
    // 长按：切换蜂鸣器模式 (自动/手动)
    buzzer_mode = (buzzer_mode == BUZZER_AUTO) ? BUZZER_MANUAL : BUZZER_AUTO;
    playBuzzerTone(PIN_BUZZER1, 100);
    Serial.print(F("[按键] 切换蜂鸣器模式 → "));
    Serial.println(buzzer_mode == BUZZER_AUTO ? "自动报警" : "手动控制");
    return;
  }
  
  playBuzzerTone(PIN_BUZZER2, 50);
  
  if (current_page == PAGE_SETTINGS) {
    // 设置页面：切换编辑模式
    setting_editing = !setting_editing;
    Serial.println(setting_editing ? "[设置] 进入编辑" : "[设置] 退出编辑");
  } else if (current_page == PAGE_BUZZER) {
    // 蜂鸣器控制页：手动开关蜂鸣器1
    if (buzzer_mode == BUZZER_MANUAL) {
      buzzer1_on = !buzzer1_on;
      digitalWrite(PIN_BUZZER1, buzzer1_on ? HIGH : LOW);
      Serial.print(F("[蜂鸣器1] "));
      Serial.println(buzzer1_on ? "开" : "关");
    }
  }
}

// K3: 下一页 / 设置值增大
void handleButtonK3(bool long_press) {
  (void)long_press;
  playBuzzerTone(PIN_BUZZER2, 50);
  
  if (current_page == PAGE_SETTINGS && setting_editing) {
    // 编辑设置：增大数值
    adjustSetting(1);
  } else {
    // 普通模式：下一页
    if (current_page < PAGE_COUNT - 1) {
      prev_page = current_page;
      current_page = (MenuPage)((uint8_t)current_page + 1);
    } else {
      current_page = (MenuPage)0;
    }
  }
}

// K4: 返回上一页 / 长按=返回主页
void handleButtonK4(bool long_press) {
  if (long_press) {
    // 长按：直接返回主页
    if (current_page == PAGE_SETTINGS) setting_editing = false;
    current_page = PAGE_MAIN;
    playBuzzerTone(PIN_BUZZER1, 80);
    Serial.println(F("[按键] 长按K4 → 返回主页"));
    return;
  }
  
  playBuzzerTone(PIN_BUZZER2, 50);
  
  if (current_page == PAGE_SETTINGS && setting_editing) {
    // 退出编辑
    setting_editing = false;
    Serial.println(F("[设置] 退出编辑"));
  } else if (current_page != PAGE_MAIN) {
    // 返回上一页
    MenuPage temp = current_page;
    current_page = prev_page;
    prev_page = temp;
  }
  // 在主页按K4无操作
}

/**
 * 调整设置值 (步进)
 */
void adjustSetting(int direction) {
  float* target = NULL;
  float step = 0.5;
  
  switch (settings_cursor) {
    case 0: target = &temp_alarm_high; step = 1.0; break;
    case 1: target = &temp_alarm_low;  step = 1.0; break;
    case 2: target = &hum_alarm_high;  step = 5.0; break;
    case 3: target = (float*)&light_alarm_low; step = 5.0; break;
    case 4: // 蜂鸣器模式
      buzzer_mode = (buzzer_mode == BUZZER_AUTO) ? BUZZER_MANUAL : BUZZER_AUTO;
      return;
    case 5: // 启用报警
      alarm_enable = !alarm_enable;
      return;
  }
  
  if (target) {
    *target += direction * step;
    // 限幅
    if (target == &temp_alarm_high) *target = constrain(*target, 10.0, 60.0);
    if (target == &temp_alarm_low)  *target = constrain(*target, -10.0, 20.0);
    if (target == &hum_alarm_high)  *target = constrain(*target, 30.0, 100.0);
    if (target == (float*)&light_alarm_low) {
      int* p = (int*)target;
      *p = constrain(*p, 0, 100);
    }
  }
}

// ============================================================
//  蜂鸣器控制
// ============================================================

/**
 * 蜂鸣器短促鸣响 (用于按键反馈)
 */
void playBuzzerTone(int buzzer_pin, int duration_ms) {
  digitalWrite(buzzer_pin, HIGH);
  delay(duration_ms);
  digitalWrite(buzzer_pin, LOW);
}

/**
 * 蜂鸣器状态更新 (在loop中周期调用)
 */
void updateBuzzer() {
  if (buzzer_mode == BUZZER_AUTO) {
    // 自动报警模式
    bool should_alarm = alarm_temp_high || alarm_temp_low || alarm_hum_high || alarm_light_low;
    
    if (should_alarm && alarm_enable) {
      // 报警：交替鸣响
      unsigned long now = ms();
      bool blink_state = ((now / BUZZER_BLINK_MS) % 2 == 0);
      digitalWrite(PIN_BUZZER1, blink_state ? HIGH : LOW);
      
      // 蜂鸣器2不同频率
      bool blink2_state = ((now / (BUZZER_BLINK_MS * 2)) % 2 == 0);
      digitalWrite(PIN_BUZZER2, blink2_state ? HIGH : LOW);
    } else {
      digitalWrite(PIN_BUZZER1, LOW);
      digitalWrite(PIN_BUZZER2, LOW);
    }
  }
  // 手动模式：状态由按键控制
}

// ============================================================
//  OLED 显示
// ============================================================

void updateDisplay() {
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);

  switch (current_page) {
    case PAGE_MAIN:     drawPageMain();    break;
    case PAGE_BME280:   drawPageBME280();  break;
    case PAGE_DHT11:    drawPageDHT11();   break;
    case PAGE_LIGHT:    drawPageLight();   break;
    case PAGE_SETTINGS: drawPageSettings(); break;
    case PAGE_BUZZER:   drawPageBuzzer();  break;
    default:            drawPageMain();    break;
  }

  u8g2.sendBuffer();
}

/**
 * 绘制进度条
 */
void drawProgressBar(int x, int y, int w, int h, int percent) {
  if (percent < 0) percent = 0;
  if (percent > 100) percent = 100;
  u8g2.drawFrame(x, y, w, h);
  int fill_w = (w - 2) * percent / 100;
  if (fill_w > 0) {
    u8g2.drawBox(x + 1, y + 1, fill_w, h - 2);
  }
}

// ========== 主页面 ==========
void drawPageMain() {
  // 标题栏
  u8g2.drawBox(0, 0, 128, 13);
  u8g2.setDrawColor(0);
  u8g2.drawStr(2, 10, " 智能环境监测系统");
  u8g2.setDrawColor(1);

  // 报警状态图标
  u8g2.setFont(u8g2_font_6x10_tf);
  if (alarm_temp_high || alarm_temp_low || alarm_hum_high || alarm_light_low) {
    u8g2.drawStr(100, 10, "!!");
  }

  // 第1行: BME280 温度
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  char buf[32];
  if (!isnan(bme_data.temperature)) {
    snprintf(buf, sizeof(buf), "温度: %.1f C", bme_data.temperature);
    u8g2.drawStr(2, 28, buf);
  } else {
    u8g2.drawStr(2, 28, "温度: --.- C");
  }

  // 第2行: 湿度
  if (!isnan(bme_data.humidity)) {
    snprintf(buf, sizeof(buf), "湿度: %.1f %%", bme_data.humidity);
    u8g2.drawStr(2, 42, buf);
  } else {
    u8g2.drawStr(2, 42, "湿度: --.- %");
  }

  // 第3行: 光照 + 气压
  snprintf(buf, sizeof(buf), "光照: %d%%", light_data.percent);
  u8g2.drawStr(2, 56, buf);
  
  if (!isnan(bme_data.pressure)) {
    snprintf(buf, sizeof(buf), "%.1f hPa", bme_data.pressure);
    u8g2.drawStr(70, 56, buf);
  }
  
  // 右下角小字显示页数
  u8g2.setFont(u8g2_font_4x6_tf);
  snprintf(buf, sizeof(buf), "1/%d", PAGE_COUNT);
  u8g2.drawStr(112, 63, buf);
}

// ========== BME280 详情页 ==========
void drawPageBME280() {
  u8g2.drawBox(0, 0, 128, 13);
  u8g2.setDrawColor(0);
  u8g2.drawStr(2, 10, " BME280 传感器详情");
  u8g2.setDrawColor(1);
  
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  char buf[32];
  
  if (!isnan(bme_data.temperature)) {
    snprintf(buf, sizeof(buf), "温度: %.2f C", bme_data.temperature);
    u8g2.drawStr(2, 26, buf);
  } else { u8g2.drawStr(2, 26, "温度: ---"); }

  if (!isnan(bme_data.humidity)) {
    snprintf(buf, sizeof(buf), "湿度: %.2f %%", bme_data.humidity);
    u8g2.drawStr(2, 39, buf);
  } else { u8g2.drawStr(2, 39, "湿度: ---"); }

  if (!isnan(bme_data.pressure)) {
    snprintf(buf, sizeof(buf), "气压: %.2f hPa", bme_data.pressure);
    u8g2.drawStr(2, 52, buf);
  } else { u8g2.drawStr(2, 52, "气压: ---"); }

  if (!isnan(bme_data.altitude)) {
    snprintf(buf, sizeof(buf), "海拔: %.1f m", bme_data.altitude);
    u8g2.drawStr(2, 63, buf);
  }
}

// ========== DHT11 详情页 ==========
void drawPageDHT11() {
  u8g2.drawBox(0, 0, 128, 13);
  u8g2.setDrawColor(0);
  u8g2.drawStr(2, 10, " DHT11 传感器详情");
  u8g2.setDrawColor(1);
  
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  char buf[32];
  
  if (!isnan(dht_data.temperature)) {
    snprintf(buf, sizeof(buf), "温度: %.1f C", dht_data.temperature);
    u8g2.drawStr(2, 28, buf);
  } else { u8g2.drawStr(2, 28, "温度: --.- C"); }

  if (!isnan(dht_data.humidity)) {
    snprintf(buf, sizeof(buf), "湿度: %.1f %%", dht_data.humidity);
    u8g2.drawStr(2, 42, buf);
  } else { u8g2.drawStr(2, 42, "湿度: --.- %"); }

  // 与BME280对比
  u8g2.setFont(u8g2_font_6x10_tf);
  if (!isnan(bme_data.temperature) && !isnan(dht_data.temperature)) {
    float diff_t = bme_data.temperature - dht_data.temperature;
    snprintf(buf, sizeof(buf), "温差(BME-DHT): %.1f C", diff_t);
    u8g2.drawStr(2, 58, buf);
  }
}

// ========== 光敏详情页 ==========
void drawPageLight() {
  u8g2.drawBox(0, 0, 128, 13);
  u8g2.setDrawColor(0);
  u8g2.drawStr(2, 10, " 光照传感器详情");
  u8g2.setDrawColor(1);
  
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  char buf[32];
  
  snprintf(buf, sizeof(buf), "光照强度: %d %%", light_data.percent);
  u8g2.drawStr(2, 26, buf);
  
  snprintf(buf, sizeof(buf), "ADC原始值: %d", light_data.analog);
  u8g2.drawStr(2, 39, buf);
  
  // 进度条显示光照
  drawProgressBar(2, 45, 124, 10, light_data.percent);
  
  // 数字阈值输出
  u8g2.drawStr(2, 62, "阈值输出: ");
  u8g2.drawStr(60, 62, light_data.digital ? "亮(L)" : "暗(D)");
}

// ========== 设置页面 ==========
void drawPageSettings() {
  u8g2.drawBox(0, 0, 128, 13);
  u8g2.setDrawColor(0);
  u8g2.drawStr(2, 10, " 报警阈值设置");
  u8g2.setDrawColor(1);
  
  u8g2.setFont(u8g2_font_6x10_tf);
  char buf[32];
  
  const char* labels[] = {
    "高温报警",
    "低温报警",
    "高湿报警",
    "低光照报警",
    "蜂鸣器模式",
    "启用报警"
  };
  
  int start_y = 22;
  int line_h = 10;
  int max_visible = 4;  // 一屏最多显示4项
  
  // 计算滚动偏移
  if (settings_cursor < settings_page_offset) {
    settings_page_offset = settings_cursor;
  } else if (settings_cursor >= settings_page_offset + max_visible) {
    settings_page_offset = settings_cursor - max_visible + 1;
  }
  
  for (int i = 0; i < max_visible; i++) {
    int idx = settings_page_offset + i;
    if (idx >= SETTINGS_COUNT) break;
    
    int y = start_y + i * line_h;
    
    // 光标指示
    if (idx == settings_cursor) {
      u8g2.drawBox(0, y - 8, 128, line_h);
      u8g2.setDrawColor(0);
      if (setting_editing) {
        u8g2.drawStr(1, y, ">");
      } else {
        u8g2.drawStr(1, y, "*");
      }
    } else {
      u8g2.setDrawColor(1);
    }
    
    // 显示标签和值
    u8g2.drawStr(10, y, labels[idx]);
    
    switch (idx) {
      case 0: snprintf(buf, sizeof(buf), "%.0f C", temp_alarm_high); break;
      case 1: snprintf(buf, sizeof(buf), "%.0f C", temp_alarm_low); break;
      case 2: snprintf(buf, sizeof(buf), "%.0f %%", hum_alarm_high); break;
      case 3: snprintf(buf, sizeof(buf), "%d %%", light_alarm_low); break;
      case 4: snprintf(buf, sizeof(buf), "%s", buzzer_mode == BUZZER_AUTO ? "自动" : "手动"); break;
      case 5: snprintf(buf, sizeof(buf), "%s", alarm_enable ? "开" : "关"); break;
    }
    u8g2.drawStr(100, y, buf);
    
    u8g2.setDrawColor(1);
  }
  
  // 底部小字提示
  u8g2.setFont(u8g2_font_4x6_tf);
  if (setting_editing) {
    u8g2.drawStr(2, 63, "K1/K3调整 K2确认 K4返回");
  } else {
    u8g2.drawStr(2, 63, "K1/K3切换 K2编辑 K4返回");
  }
  
  // 设置页面滚轮指示
  if (settings_cursor > 0) u8g2.drawTriangle(64, 14, 60, 18, 68, 18);
  if (settings_cursor < SETTINGS_COUNT - 1) u8g2.drawTriangle(64, 63, 60, 59, 68, 59);
}

// ========== 蜂鸣器控制页 ==========
void drawPageBuzzer() {
  u8g2.drawBox(0, 0, 128, 13);
  u8g2.setDrawColor(0);
  u8g2.drawStr(2, 10, " 蜂鸣器控制");
  u8g2.setDrawColor(1);
  
  u8g2.setFont(u8g2_font_wqy12_t_chinese3);
  char buf[32];
  
  // 模式显示
  snprintf(buf, sizeof(buf), "模式: %s", buzzer_mode == BUZZER_AUTO ? "自动报警" : "手动控制");
  u8g2.drawStr(2, 26, buf);
  
  // 蜂鸣器1状态
  snprintf(buf, sizeof(buf), "蜂鸣器1: %s", buzzer1_on ? "开" : "关");
  u8g2.drawStr(2, 39, buf);
  
  // 蜂鸣器2状态
  snprintf(buf, sizeof(buf), "蜂鸣器2: %s", buzzer2_on ? "开" : "关");
  u8g2.drawStr(2, 52, buf);
  
  // 报警状态
  if (alarm_temp_high || alarm_temp_low || alarm_hum_high || alarm_light_low) {
    u8g2.setFont(u8g2_font_6x10_tf);
    u8g2.drawStr(2, 63, "!! 报警触发中 !!");
  }
  
  // 操作提示
  u8g2.setFont(u8g2_font_4x6_tf);
  u8g2.drawStr(70, 63, "K2:切换蜂鸣器1");
}

// ============================================================
//  工具函数
// ============================================================

void getUptimeString(char* buf, size_t len) {
  unsigned long t = (ms() - boot_ms) / 1000;
  int days = t / 86400;
  int hours = (t % 86400) / 3600;
  int mins = (t % 3600) / 60;
  int secs = t % 60;
  
  if (days > 0) {
    snprintf(buf, len, "%d天%02d:%02d:%02d", days, hours, mins, secs);
  } else {
    snprintf(buf, len, "%02d:%02d:%02d", hours, mins, secs);
  }
}
