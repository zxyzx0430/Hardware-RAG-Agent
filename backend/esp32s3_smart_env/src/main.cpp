/**
 * ESP32-S3 智能环境音乐交互系统 v3.0
 * 
 * 🎯 修复:
 *   1. BME280 同时扫描地址 0x76 和 0x77
 *   2. 音量下限降到 10，步进 10
 *   3. K4 按键按下即触发，不再等释放
 *   4. 恢复变频 PWM（还原音乐性），限制五声音阶 ≤523Hz，柔和不刺耳
 * 
 * 传感器: BME280 (I2C) + DHT11 + 光敏模块 + 4按键 + 2蜂鸣器
 * 
 * 接线:
 *   GPIO1  → BME280 SDA
 *   GPIO2  → BME280 SCL
 *   GPIO4  → DHT11 DATA (需4.7kΩ上拉至3V3)
 *   GPIO5  → 蜂鸣器1 SIG
 *   GPIO6  → 蜂鸣器2 SIG
 *   GPIO10 → 光敏模块 AO (ADC)
 *   GPIO11 → 光敏模块 DO
 *   GPIO12 → 按键 K1
 *   GPIO13 → 按键 K2
 *   GPIO14 → 按键 K3
 *   GPIO15 → 按键 K4
 */

#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <DHT.h>

// ===================== 引脚定义 =====================
#define PIN_SDA         1
#define PIN_SCL         2
#define PIN_DHT11       4
#define PIN_BUZZER1     5
#define PIN_BUZZER2     6
#define PIN_LIGHT_AO    10
#define PIN_LIGHT_DO    11
#define PIN_KEY1        12
#define PIN_KEY2        13
#define PIN_KEY3        14
#define PIN_KEY4        15

// ===================== I2C / BME280 =====================
#define BME_ADDR_1      0x76   // SDO=GND
#define BME_ADDR_2      0x77   // SDO=VCC
TwoWire i2cBus = TwoWire(0);
Adafruit_BME280 bme;
bool bmeOK = false;
uint8_t bmeAddr = 0;

// ===================== DHT11 =====================
DHT dht(PIN_DHT11, DHT11);

// ===================== LEDC PWM =====================
#define LEDC_RES        10     // 10-bit, duty 0~1023 (更精细)
#define BUZZER1_CH      0
#define BUZZER2_CH      1
#define LEDC_FREQ_DEF   2000  // 默认频率

// ===================== 音量控制 =====================
#define VOL_MIN         10     // 下限10，能听到但不刺耳
#define VOL_MAX         200
#define VOL_STEP        10
int volume = 120;

// ===================== 五声音阶 (C4~C5, ≤523Hz) =====================
// 五声音阶比七声音阶更柔和，没有半音冲突
const int pentatonicFreq[] = { 262, 294, 330, 392, 440, 523 };
const int PENTA_NOTES = 6;

// ===================== 工作模式 =====================
enum Mode {
  MODE_WEATHER = 0,
  MODE_ALARM,
  MODE_LIGHT,
  MODE_SILENT
};
Mode currentMode = MODE_WEATHER;
bool modeChanged = true;
const char* modeNames[4] = {"☀️ 天气旋律", "🔔 警报模式", "🎵 光控音乐", "🔇 静音模式"};

// ===================== 系统状态 =====================
float tempBME = 0, humBME = 0, pressure_hPa = 0;
float tempDHT = 0, humDHT = 0;
int lightRaw = 0;
bool lightTrig = false;

// 按键去抖 (按下即触发，不等释放)
unsigned long lastKeyPress[4] = {0,0,0,0};
const unsigned long KEY_COOLDOWN = 300;  // 300ms冷却防抖
const int KEY_PINS[4] = {PIN_KEY1, PIN_KEY2, PIN_KEY3, PIN_KEY4};

// 传感器读取间隔
unsigned long lastSensorRead = 0;
const unsigned long SENSOR_INTERVAL = 3000;

// ===================== 音乐播放状态 =====================
unsigned long noteStart1 = 0, noteStart2 = 0;
int noteDuration1 = 0, noteDuration2 = 0;
int noteFreq1 = 0, noteFreq2 = 0;
int noteVol1 = 0, noteVol2 = 0;
bool noteOn1 = false, noteOn2 = false;

// 旋律序列
struct MelodyNote {
  int freq;     // 频率(Hz)，0=休止
  int vol;      // 音量(0~255)，受全局音量缩放
  int durMs;    // 持续时长(ms)
};

// ===================== 前向声明 =====================
void setupSensors();
void readSensors();
void handleButtons();
void playMelody();
void playNote(int ch, int pin, int freq, int vol, int durMs);
void stopNote(int ch, int pin);
void printSensorData();
String printTime();

// ===================== SETUP =====================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n============================================");
  Serial.println("  ESP32-S3 智能环境音乐交互系统 v3.0");
  Serial.println("  五声音阶 · 柔和不刺耳 🎵");
  Serial.println("============================================\n");

  // IO
  pinMode(PIN_BUZZER1, OUTPUT); digitalWrite(PIN_BUZZER1, LOW);
  pinMode(PIN_BUZZER2, OUTPUT); digitalWrite(PIN_BUZZER2, LOW);
  pinMode(PIN_LIGHT_DO, INPUT);
  for (int i = 0; i < 4; i++) pinMode(KEY_PINS[i], INPUT_PULLUP);

  // LEDC
  ledcSetup(BUZZER1_CH, LEDC_FREQ_DEF, LEDC_RES);
  ledcAttachPin(PIN_BUZZER1, BUZZER1_CH);
  ledcWrite(BUZZER1_CH, 0);
  ledcSetup(BUZZER2_CH, LEDC_FREQ_DEF, LEDC_RES);
  ledcAttachPin(PIN_BUZZER2, BUZZER2_CH);
  ledcWrite(BUZZER2_CH, 0);

  // 传感器
  setupSensors();

  // 启动提示音 - 五声音阶上行
  for (int i = 0; i < PENTA_NOTES; i++) {
    setBuzzerFreq(BUZZER1_CH, pentatonicFreq[i]);
    setBuzzerDuty(BUZZER1_CH, volume / 2);
    delay(100);
    setBuzzerDuty(BUZZER1_CH, 0);
    delay(50);
  }

  Serial.println("系统就绪！");
  Serial.println("K1=切换模式  K2=重新开始  K3=音量+  K4=音量-");
  Serial.printf("当前模式: %s  |  音量: %d\n", modeNames[currentMode], volume);
  Serial.println("----------------------------------------\n");
}

// ===================== 替代 ledcChangeFreq (ESP32 Arduino兼容) =====================
void setBuzzerFreq(int ch, int freq) {
  // 旧版 ESP32 Arduino API: ledcSetup + ledcAttachPin
  // 频率变了就重新 setup
  ledcWrite(ch, 0);  // 先关
  int res = LEDC_RES;
  ledcSetup(ch, freq, res);
  // attach 只需做一次（已做）
}

void setBuzzerDuty(int ch, int duty) {
  ledcWrite(ch, constrain(duty, 0, (1<<LEDC_RES)-1));
}

// ===================== MAIN LOOP =====================
void loop() {
  unsigned long now = millis();

  // 1. 传感器
  if (now - lastSensorRead >= SENSOR_INTERVAL) {
    readSensors();
    printSensorData();
    lastSensorRead = now;
  }

  // 2. 按键 (按下即触发)
  handleButtons();

  // 3. 播放
  playMelody();

  delay(5);
}

// ===================== 传感器初始化 =====================
void setupSensors() {
  Serial.print("初始化 I2C... ");
  i2cBus.begin(PIN_SDA, PIN_SCL, 100000);
  Serial.println("OK");

  // 先试 0x76 (SDO=GND)
  Serial.print("BME280 扫描 0x76... ");
  if (bme.begin(BME_ADDR_1, &i2cBus)) {
    bmeOK = true;
    bmeAddr = BME_ADDR_1;
    Serial.println("OK ✓ (SDO→GND)");
  } else {
    // 再试 0x77 (SDO=VCC)
    Serial.print("失败, 扫描 0x77... ");
    if (bme.begin(BME_ADDR_2, &i2cBus)) {
      bmeOK = true;
      bmeAddr = BME_ADDR_2;
      Serial.println("OK ✓ (SDO→VCC)");
    } else {
      bmeOK = false;
      Serial.println("失败 ✗ (检查接线: SDA→GPIO1, SCL→GPIO2, CSB→3V3)");
    }
  }

  Serial.print("DHT11... ");
  dht.begin();
  Serial.println("OK");
}

// ===================== 读取传感器 =====================
void readSensors() {
  if (bmeOK) {
    tempBME = bme.readTemperature();
    humBME = bme.readHumidity();
    pressure_hPa = bme.readPressure() / 100.0F;
  }
  tempDHT = dht.readTemperature();
  humDHT = dht.readHumidity();
  lightRaw = analogRead(PIN_LIGHT_AO);
  lightTrig = digitalRead(PIN_LIGHT_DO);
}

// ===================== 按键处理 (按下即触发) =====================
void handleButtons() {
  unsigned long now = millis();
  for (int i = 0; i < 4; i++) {
    int val = digitalRead(KEY_PINS[i]);
    // 低电平=按下，且冷却时间已过
    if (val == LOW && (now - lastKeyPress[i] > KEY_COOLDOWN)) {
      lastKeyPress[i] = now;
      onKeyPress(i);
    }
  }
}

void onKeyPress(int key) {
  switch (key) {
    case 0: // K1 - 切换模式
      currentMode = (Mode)(((int)currentMode + 1) % 4);
      modeChanged = true;
      stopNote(BUZZER1_CH, PIN_BUZZER1);
      stopNote(BUZZER2_CH, PIN_BUZZER2);
      Serial.printf("\n>>> 模式: %s  |  音量: %d\n", modeNames[currentMode], volume);
      break;
    case 1: // K2 - 重新开始
      stopNote(BUZZER1_CH, PIN_BUZZER1);
      stopNote(BUZZER2_CH, PIN_BUZZER2);
      Serial.println(">>> K2: 重新开始");
      break;
    case 2: // K3 - 音量+
      volume = min(volume + VOL_STEP, VOL_MAX);
      Serial.printf(">>> 音量: %d\n", volume);
      break;
    case 3: // K4 - 音量-
      volume = max(volume - VOL_STEP, VOL_MIN);
      Serial.printf(">>> 音量: %d\n", volume);
      break;
  }
}

// ===================== 旋律定义 =====================
// 天气模式 - 蜂鸣器1 主旋律
const MelodyNote mel_weather1[] = {
  {262, 140, 300}, {330, 120, 200}, {0, 0, 100},
  {392, 150, 350}, {330, 100, 200}, {0, 0, 150},
  {440, 160, 300}, {392, 120, 200}, {0, 0, 100},
  {523, 180, 400}, {440, 130, 200}, {0, 0, 200},
  {392, 140, 300}, {330, 110, 200}, {0, 0, 150},
  {262, 150, 400}, {0, 0, 300},
};
const int mel_weather1_len = 17;

// 天气模式 - 蜂鸣器2 和声
const MelodyNote mel_weather2[] = {
  {0, 0, 150}, {262, 100, 300}, {0, 0, 150},
  {330, 110, 250}, {0, 0, 100}, {392, 120, 300},
  {0, 0, 150}, {330, 100, 200}, {0, 0, 100},
  {392, 130, 350}, {0, 0, 150}, {440, 120, 250},
  {0, 0, 100}, {330, 100, 300}, {0, 0, 150},
  {262, 110, 400}, {0, 0, 200},
};
const int mel_weather2_len = 17;

// 警报模式 - 两音交替
const MelodyNote mel_alarm1[] = {
  {392, 200, 100}, {0, 0, 80},
  {392, 200, 100}, {0, 0, 80},
  {392, 200, 100}, {0, 0, 200},
  {523, 200, 100}, {0, 0, 80},
  {523, 200, 100}, {0, 0, 80},
  {523, 200, 100}, {0, 0, 400},
};
const int mel_alarm1_len = 12;

// ===================== 旋律播放 =====================
int melPos1 = 0, melPos2 = 0;
unsigned long melTick1 = 0, melTick2 = 0;
const MelodyNote *curMel1 = mel_weather1, *curMel2 = mel_weather2;
int curMelLen1 = mel_weather1_len, curMelLen2 = mel_weather2_len;

void playMelody() {
  unsigned long now = millis();

  switch (currentMode) {
    case MODE_SILENT:
      if (modeChanged) {
        stopNote(BUZZER1_CH, PIN_BUZZER1);
        stopNote(BUZZER2_CH, PIN_BUZZER2);
        modeChanged = false;
      }
      break;

    case MODE_WEATHER: {
      if (modeChanged) {
        melPos1 = melPos2 = 0;
        melTick1 = melTick2 = now;
        // 根据天气选旋律
        curMel1 = mel_weather1; curMelLen1 = mel_weather1_len;
        curMel2 = mel_weather2; curMelLen2 = mel_weather2_len;
        modeChanged = false;
      }

      // 蜂鸣器1
      if (now - melTick1 >= curMel1[melPos1].durMs) {
        melTick1 = now;
        melPos1 = (melPos1 + 1) % curMelLen1;
        const MelodyNote &n = curMel1[melPos1];
        if (n.freq > 0) {
          int v = map(n.vol, 0, 255, 0, volume);
          playNote(BUZZER1_CH, PIN_BUZZER1, n.freq, v, n.durMs);
        } else {
          stopNote(BUZZER1_CH, PIN_BUZZER1);
        }
      }

      // 蜂鸣器2
      if (now - melTick2 >= curMel2[melPos2].durMs) {
        melTick2 = now;
        melPos2 = (melPos2 + 1) % curMelLen2;
        const MelodyNote &n = curMel2[melPos2];
        if (n.freq > 0) {
          int v = map(n.vol, 0, 255, 0, volume);
          playNote(BUZZER2_CH, PIN_BUZZER2, n.freq, v, n.durMs);
        } else {
          stopNote(BUZZER2_CH, PIN_BUZZER2);
        }
      }
      break;
    }

    case MODE_ALARM: {
      if (modeChanged) {
        melPos1 = 0;
        melTick1 = now;
        modeChanged = false;
      }
      bool alarm = false;
      if (bmeOK && (tempBME > 35.0 || tempBME < 5.0)) alarm = true;
      if (tempDHT > 35.0 || tempDHT < 5.0) alarm = true;

      if (!alarm) {
        stopNote(BUZZER1_CH, PIN_BUZZER1);
        stopNote(BUZZER2_CH, PIN_BUZZER2);
        return;
      }

      if (now - melTick1 >= mel_alarm1[melPos1].durMs) {
        melTick1 = now;
        melPos1 = (melPos1 + 1) % mel_alarm1_len;
        const MelodyNote &n = mel_alarm1[melPos1];
        if (n.freq > 0) {
          int v = map(n.vol, 0, 255, 0, volume);
          playNote(BUZZER1_CH, PIN_BUZZER1, n.freq, v, n.durMs);
          playNote(BUZZER2_CH, PIN_BUZZER2, n.freq+100, v, n.durMs);  // 蜂鸣器2错开
        } else {
          stopNote(BUZZER1_CH, PIN_BUZZER1);
          stopNote(BUZZER2_CH, PIN_BUZZER2);
        }
      }
      break;
    }

    case MODE_LIGHT: {
      if (modeChanged) {
        modeChanged = false;
      }
      // 光强 → 五声音阶索引 (0~5)
      int idx = map(lightRaw, 0, 4095, 0, PENTA_NOTES-1);
      idx = constrain(idx, 0, PENTA_NOTES-1);
      int freq = pentatonicFreq[idx];
      int v = map(100, 0, 255, 0, volume);
      playNote(BUZZER1_CH, PIN_BUZZER1, freq, v, 500);
      // 蜂鸣器2 反向映射
      int idx2 = PENTA_NOTES-1 - idx;
      int freq2 = pentatonicFreq[idx2];
      playNote(BUZZER2_CH, PIN_BUZZER2, freq2, v, 500);
      break;
    }
  }
}

// ===================== 控制蜂鸣器 =====================
void playNote(int ch, int pin, int freq, int vol, int durMs) {
  if (freq <= 0 || vol <= 0) {
    stopNote(ch, pin);
    return;
  }
  vol = constrain(vol, 0, (1<<LEDC_RES)-1);
  setBuzzerFreq(ch, freq);
  setBuzzerDuty(ch, vol);
}

void stopNote(int ch, int pin) {
  ledcWrite(ch, 0);
}

// ===================== 串口打印 =====================
void printSensorData() {
  Serial.printf("[%s] ", printTime().c_str());
  if (bmeOK) {
    Serial.printf("BME(%02X): %.1f°C %.0f%% %.0fhPa | ", bmeAddr, tempBME, humBME, pressure_hPa);
  } else {
    Serial.print("BME: ✗ | ");
  }
  Serial.printf("DHT: %.1f°C %.0f%% | 光敏:%4d | 模式:%s 音量:%d\n",
    tempDHT, humDHT, lightRaw, modeNames[currentMode], volume);
}

String printTime() {
  unsigned long ms = millis();
  unsigned long sec = ms / 1000;
  unsigned long min = sec / 60;
  sec %= 60;
  char buf[16];
  sprintf(buf, "%02lu:%02lu", min, sec);
  return String(buf);
}
