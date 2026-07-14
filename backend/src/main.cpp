/**
 * =====================================================
 * ESP32-S3 智能环境监控与报警控制台
 * Smart Environment Monitor & Alarm Console
 * 
 * 硬件接线（请按此表连接，BME280 的 SDA/SCL 请勿接反）：
 *   GPIO1 (I2C_SDA) → BME280 SDA (SDI)
 *   GPIO2 (I2C_SCL) → BME280 SCL (SCK)
 *   GPIO4  → DHT11 DATA
 *   GPIO5  → 蜂鸣器1 (有源, Warning)
 *   GPIO6  → 蜂鸣器2 (有源, Critical)
 *   GPIO8  → SSD1306 SDA
 *   GPIO9  → SSD1306 SCL
 *   GPIO10 → 光敏模块 AO
 *   GPIO11 → 光敏模块 DO
 *   GPIO12 → 按键 K1 (Prev)
 *   GPIO13 → 按键 K2 (Next)
 *   GPIO14 → 按键 K3 (Mute)
 *   GPIO15 → 按键 K4 (Reset)
 * 
 * BME280: CSB→VCC(I2C模式), SDO→GND(地址0x76)
 * 所有模块 VCC→3.3V, GND→GND
 * =====================================================
 */

#include <Wire.h>
#include <Adafruit_BME280.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>

// ==================== Pin Definitions ====================
#define PIN_BME280_SDA    1
#define PIN_BME280_SCL    2
#define PIN_DHT11         4
#define PIN_BUZZER1       5   // Warning 蜂鸣器
#define PIN_BUZZER2       6   // Critical 蜂鸣器
#define PIN_SSD1306_SDA   8
#define PIN_SSD1306_SCL   9
#define PIN_LIGHT_AO     10   // ADC 输入
#define PIN_LIGHT_DO     11   // 数字阈值输出
#define PIN_K1           12   // 前翻页
#define PIN_K2           13   // 后翻页
#define PIN_K3           14   // 静音
#define PIN_K4           15   // 复位报警

// ==================== I2C & Sensor Objects ====================
Adafruit_BME280 bme;              // 挂在 Wire (I2C0)
Adafruit_SSD1306 display(128, 64, &Wire1);  // 挂在 Wire1 (I2C1)
DHT dht(PIN_DHT11, DHT11);

// ==================== Display Pages ====================
#define PAGE_COUNT  4
static int currentPage = 0;       // 0~3

// ==================== Sensor Data ====================
typedef struct {
  float bme_temp;
  float bme_hum;
  float bme_press;   // hPa
  float dht_temp;
  float dht_hum;
  int   light_ao;    // ADC 原始值 0~4095
  bool  light_do;    // true=暗, false=亮
  unsigned long lastReadMs;
} SensorData;
static SensorData sd = {0};

// ==================== Alarm State ====================
typedef struct {
  bool warning;        // 警戒状态
  bool critical;       // 严重状态
  bool muted;          // 用户静音
} AlarmState;
static AlarmState alarm = {false, false, false};

// ==================== Alarm Thresholds ====================
static const float TEMP_WARN   = 30.0;   // °C
static const float TEMP_CRIT   = 38.0;   // °C
static const float HUM_WARN    = 75.0;   // %
static const float PRESS_WARN  = 985.0;  // hPa

// ==================== Button Debounce ====================
typedef struct {
  uint8_t pin;
  bool    lastState;
  bool    pressed;    // 边沿触发标志
  unsigned long lastChangeMs;
} Button;
static Button btns[4] = {
  {PIN_K1, HIGH, false, 0},
  {PIN_K2, HIGH, false, 0},
  {PIN_K3, HIGH, false, 0},
  {PIN_K4, HIGH, false, 0},
};

// ==================== Forward Declarations ====================
void readSensors(void);
void updateAlarm(void);
void updateBuzzer(void);
void scanButtons(void);
void updateDisplay(void);
void drawPage0(void);
void drawPage1(void);
void drawPage2(void);
void drawPage3(void);

// ==================== Setup ====================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n\n=== ESP32-S3 智能环境监控 v1.0 ===");

  // --- I2C 初始化 ---
  // BME280 接 I2C0: GPIO1=SDA, GPIO2=SCL
  Wire.begin(PIN_BME280_SDA, PIN_BME280_SCL);
  // SSD1306 接 I2C1: GPIO8=SDA, GPIO9=SCL
  Wire1.begin(PIN_SSD1306_SDA, PIN_SSD1306_SCL);

  // --- BME280 (地址 0x76: SDO→GND) ---
  if (!bme.begin(0x76, &Wire)) {
    Serial.println("[ERR] BME280 not found at 0x76! Check wiring.");
  } else {
    Serial.println("[OK] BME280 detected");
    bme.setSampling(Adafruit_BME280::MODE_FORCED,
                    Adafruit_BME280::SAMPLING_X1,  // temp
                    Adafruit_BME280::SAMPLING_X1,  // pressure
                    Adafruit_BME280::SAMPLING_X1,  // humidity
                    Adafruit_BME280::FILTER_OFF);
  }

  // --- SSD1306 OLED ---
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("[ERR] SSD1306 not found at 0x3C! Check wiring.");
  } else {
    Serial.println("[OK] SSD1306 detected");
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 0);
    display.println("System Starting...");
    display.display();
    delay(1000);
  }

  // --- DHT11 ---
  dht.begin();
  Serial.println("[OK] DHT11 initialized");

  // --- GPIO ---
  pinMode(PIN_BUZZER1, OUTPUT);
  pinMode(PIN_BUZZER2, OUTPUT);
  pinMode(PIN_LIGHT_DO, INPUT);
  for (int i = 0; i < 4; i++) {
    pinMode(btns[i].pin, INPUT_PULLUP);
  }
  digitalWrite(PIN_BUZZER1, LOW);
  digitalWrite(PIN_BUZZER2, LOW);

  // --- ADC 分辨率 (ESP32-S3 默认 12bit) ---
  analogReadResolution(12);

  // --- 上电自检：蜂鸣器响一声 ---
  digitalWrite(PIN_BUZZER1, HIGH);
  delay(150);
  digitalWrite(PIN_BUZZER1, LOW);

  Serial.println("[OK] System ready!");
  sd.lastReadMs = 0;
}

// ==================== Main Loop ====================
void loop() {
  unsigned long now = millis();

  // 每 2 秒读取一次传感器
  if (now - sd.lastReadMs >= 2000) {
    readSensors();
    updateAlarm();
    sd.lastReadMs = now;
  }

  scanButtons();
  updateBuzzer();
  updateDisplay();

  delay(20);  // 防抖 + 降低功耗
}

// ==================== Read Sensors ====================
void readSensors() {
  // --- BME280 (Forced 模式：每次读之前触发测量) ---
  bme.takeForcedMeasurement();
  sd.bme_temp  = bme.readTemperature();
  sd.bme_hum   = bme.readHumidity();
  sd.bme_press = bme.readPressure() / 100.0F;  // Pa → hPa

  // --- DHT11 ---
  sd.dht_temp = dht.readTemperature();
  sd.dht_hum  = dht.readHumidity();

  // --- 光敏 ---
  sd.light_ao = analogRead(PIN_LIGHT_AO);
  sd.light_do = digitalRead(PIN_LIGHT_DO);  // HIGH=暗, LOW=亮

  // --- Debug ---
  Serial.printf("[DATA] BME:%.1fC %.1f%% %.1fhPa | DHT:%.1fC %.1f%% | LUX:ADC=%d DO=%d\n",
    sd.bme_temp, sd.bme_hum, sd.bme_press,
    sd.dht_temp, sd.dht_hum,
    sd.light_ao, sd.light_do);
}

// ==================== Alarm Logic ====================
void updateAlarm() {
  bool warn  = false;
  bool crit  = false;

  // BME280 温度报警
  if (!isnan(sd.bme_temp)) {
    if (sd.bme_temp >= TEMP_CRIT) {
      crit = true;
    } else if (sd.bme_temp >= TEMP_WARN) {
      warn = true;
    }
  }
  // DHT11 温度辅助报警
  if (!isnan(sd.dht_temp) && sd.dht_temp >= TEMP_CRIT) {
    crit = true;
  }
  // 湿度报警
  if (!isnan(sd.bme_hum) && sd.bme_hum >= HUM_WARN) {
    warn = true;
  }
  // 气压报警
  if (!isnan(sd.bme_press) && sd.bme_press <= PRESS_WARN) {
    warn = true;
  }

  alarm.warning  = warn;
  alarm.critical = crit;
}

// ==================== Buzzer Control ====================
void updateBuzzer() {
  if (alarm.muted) {
    digitalWrite(PIN_BUZZER1, LOW);
    digitalWrite(PIN_BUZZER2, LOW);
    return;
  }

  unsigned long t = millis();

  if (alarm.critical) {
    // 严重报警：双蜂鸣器 200ms 周期 (100ms on/off)
    bool on = (t % 400) < 200;
    digitalWrite(PIN_BUZZER1, on ? HIGH : LOW);
    digitalWrite(PIN_BUZZER2, on ? HIGH : LOW);
  } else if (alarm.warning) {
    // 警戒报警：单蜂鸣器 2s 周期 (1s on/off)
    bool on = (t % 2000) < 1000;
    digitalWrite(PIN_BUZZER1, on ? HIGH : LOW);
    digitalWrite(PIN_BUZZER2, LOW);
  } else {
    digitalWrite(PIN_BUZZER1, LOW);
    digitalWrite(PIN_BUZZER2, LOW);
  }
}

// ==================== Button Scanner ====================
void scanButtons() {
  unsigned long now = millis();

  for (int i = 0; i < 4; i++) {
    bool reading = digitalRead(btns[i].pin);

    // 检测电平变化
    if (reading != btns[i].lastState) {
      btns[i].lastChangeMs = now;
    }

    // 去抖 50ms
    if ((now - btns[i].lastChangeMs) > 50) {
      // 按下 (LOW) 且之前未触发
      if (reading == LOW && !btns[i].pressed) {
        btns[i].pressed = true;

        switch (i) {
          case 0:  // K1: 上一页
            currentPage = (currentPage - 1 + PAGE_COUNT) % PAGE_COUNT;
            Serial.printf("[BTN] K1 -> Page %d\n", currentPage);
            break;
          case 1:  // K2: 下一页
            currentPage = (currentPage + 1) % PAGE_COUNT;
            Serial.printf("[BTN] K2 -> Page %d\n", currentPage);
            break;
          case 2:  // K3: 静音切换
            alarm.muted = !alarm.muted;
            Serial.printf("[BTN] K3 -> Mute=%d\n", alarm.muted);
            break;
          case 3:  // K4: 复位报警
            alarm.warning  = false;
            alarm.critical = false;
            alarm.muted    = false;
            Serial.println("[BTN] K4 -> Alarm Reset");
            break;
        }
      }
      // 松开 (HIGH) 清除标志
      if (reading == HIGH) {
        btns[i].pressed = false;
      }
    }

    btns[i].lastState = reading;
  }
}

// ==================== Display Manager ====================
void updateDisplay() {
  display.clearDisplay();

  // ---- 顶部状态栏 ----
  display.setTextSize(1);
  display.setCursor(0, 0);

  // 报警状态图标
  if (alarm.critical) {
    display.print("!CRIT!");
  } else if (alarm.warning) {
    display.print(" WARN ");
  } else {
    display.print("  OK  ");
  }

  if (alarm.muted) {
    display.print(" MUTED");
  }

  // 时间指示
  display.printf(" |P%d/%d", currentPage + 1, PAGE_COUNT);

  // 分隔线
  display.drawFastHLine(0, 10, 128, SSD1306_WHITE);

  // ---- 页面内容 ----
  switch (currentPage) {
    case 0: drawPage0(); break;
    case 1: drawPage1(); break;
    case 2: drawPage2(); break;
    case 3: drawPage3(); break;
  }

  display.display();
}

// ==================== Page 0: BME280 详细数据 ====================
void drawPage0() {
  display.setCursor(0, 13);
  display.setTextSize(1);
  display.println(" [BME280 Sensor]");

  if (isnan(sd.bme_temp)) {
    display.println(" Sensor Error!");
    display.println(" Check I2C wiring");
    return;
  }

  display.print(" Temp: ");
  display.print(sd.bme_temp, 1);
  display.println(" C");

  display.print(" Hum:  ");
  display.print(sd.bme_hum, 1);
  display.println(" %");

  display.print(" Pres: ");
  display.print(sd.bme_press, 1);
  display.println(" hPa");

  // 小提示
  display.setCursor(0, 55);
  display.setTextSize(1);
  display.print("K1/K2:Page  K3:Mute");
}

// ==================== Page 1: DHT11 + 温差对比 ====================
void drawPage1() {
  display.setCursor(0, 13);
  display.setTextSize(1);
  display.println(" [DHT11 & Compare]");

  if (isnan(sd.dht_temp)) {
    display.println(" DHT11 Error!");
  } else {
    display.print(" Temp: ");
    display.print(sd.dht_temp, 1);
    display.println(" C");
    display.print(" Hum:  ");
    display.print(sd.dht_hum, 1);
    display.println(" %");
  }

  // 温差对比
  display.println(" ---");
  if (!isnan(sd.bme_temp) && !isnan(sd.dht_temp)) {
    float diff = sd.bme_temp - sd.dht_temp;
    display.print(" BME-DHT: ");
    if (diff > 0) display.print("+");
    display.print(diff, 1);
    display.println(" C");
    if (diff > 3.0) {
      display.println(" Large diff! Check");
      display.println(" sensor placement.");
    }
  } else {
    display.println(" BME-DHT: N/A");
  }
}

// ==================== Page 2: 光敏 + 环境光柱状图 ====================
void drawPage2() {
  display.setCursor(0, 13);
  display.setTextSize(1);
  display.println(" [Light Sensor]");

  // ADC 值
  display.print(" ADC: ");
  display.print(sd.light_ao);
  display.print("  (");
  display.print(map(sd.light_ao, 0, 4095, 0, 100));
  display.println("%)");

  // DO 状态
  display.print(" DO:  ");
  display.println(sd.light_do ? "DARK " : "LIGHT");

  // ---- 柱状图 (128px 宽, 8px 高) ----
  int barW = map(constrain(sd.light_ao, 0, 4095), 0, 4095, 0, 120);
  display.drawRect(4, 36, 120, 10, SSD1306_WHITE);
  display.fillRect(4, 36, barW, 10, SSD1306_WHITE);

  // 刻度
  display.setCursor(4, 48);
  display.print("Dark");
  display.setCursor(88, 48);
  display.print("Bright");

  // DHT11 湿度辅助显示
  if (!isnan(sd.dht_hum)) {
    display.setCursor(0, 56);
    display.print("Humidity condition: ");
    display.print(sd.dht_hum, 0);
    display.println("%");
  }
}

// ==================== Page 3: 系统信息 + 按键说明 ====================
void drawPage3() {
  display.setCursor(0, 13);
  display.setTextSize(1);
  display.println(" [System Status]");

  display.print(" BME280: ");
  display.println(isnan(sd.bme_temp) ? "ERR" : "OK");
  display.print(" DHT11:  ");
  display.println(isnan(sd.dht_temp) ? "ERR" : "OK");
  display.print(" OLED:   ");
  display.println("OK");

  // 报警状态
  display.println(" ---");
  display.print(" Alarm: ");
  if (alarm.critical)   display.println("CRITICAL!");
  else if (alarm.warning) display.println("Warning");
  else                    display.println("None");
  display.print(" Mute:  ");
  display.println(alarm.muted ? "ON " : "OFF");

  // Uptime
  unsigned long sec = millis() / 1000;
  display.print(" Uptime: ");
  display.print(sec / 3600);
  display.print("h");
  display.print((sec % 3600) / 60);
  display.print("m");
  display.println(sec % 60);
}
