# build_firmware 自动解析第三方库依赖

> Date: 2026-07-05
> Status: Approved

## 背景

`build_firmware` 工具每次编译都在 `.build/tmp/{session}/` 新建临时项目，只写入 `src/main.cpp` + 最小 `platformio.ini`（无 `lib_deps`）。当代码 `#include` 了第三方库（如 `Adafruit_NeoPixel.h`）时，PlatformIO 不会自动下载，导致 `fatal error: xxx.h: No such file or directory`。

Agent 用 `run_command` 在别的目录 `pio lib install` 装的库，临时项目也看不到。

## 目标

让 `build_firmware` 编译时能自动找到并下载代码所需的第三方库，无需用户或 Agent 手动 `pio lib install`。

## 方案

方案一 + 方案二结合：工具支持显式 `lib_deps` 参数 + 自动扫描 `#include` 补全。

### 数据流

```
LLM 调用 build_firmware(code, lib_deps=["adafruit/DHT sensor library"])
    │
    ├─ 1. scan_lib_deps_from_code(code)  →  ["adafruit/Adafruit NeoPixel"]  (扫描 #include)
    │
    ├─ 2. merge(lib_deps显式, 扫描结果)  →  去重合并
    │
    ├─ 3. CompileRequest(lib_deps=merged)
    │
    ├─ 4. _create_temp_project: 写 platformio.ini，含 lib_deps = 字段
    │
    └─ 5. pio run  →  PlatformIO 自动下载缺失库并编译
```

## 改动清单

### 1. `backend/src/hardware/pio_runner.py`

**CompileRequest** 新增字段：
```python
@dataclass(frozen=True)
class CompileRequest:
    code: str
    board: str
    platform: str
    options: dict[str, Any]
    session_id: str
    lib_deps: tuple[str, ...] = ()  # 新增：PlatformIO lib_deps 列表
```

**`_ini_lines`** 追加 lib_deps：
```python
if lib_deps:
    lines.append("lib_deps =")
    for dep in lib_deps:
        lines.append(f"  {dep}")
```

**`_create_temp_project`** 透传 `req.lib_deps` 给 `_generate_platformio_ini`。

### 2. `backend/src/agent/tools/groups/code/build_tool.py`

**BuildCodeArgs** 新增字段：
```python
lib_deps: list[str] = Field(
    default_factory=list,
    description="代码依赖的 PlatformIO 库（格式 'owner/repo' 或 'name@version'）。"
                "常见库可留空，工具会自动扫描 #include 推断。",
)
```

**新增常量 `INCLUDE_TO_LIBDEPS`**：30+ 常用 Arduino/ESP32 库的头文件→库名映射表。

**新增常量 `BUILTIN_HEADERS`**：ESP32 Arduino core / STM32 Arduino core 自带的头文件（不装库）。

**新增函数 `scan_lib_deps_from_code(code: str) -> list[str]`**：
- 用正则 `#include\s*[<"]([^>"]+\.h)[>"]` 提取所有头文件
- **先查 `BUILTIN_HEADERS`**，命中则跳过（内置库不装）
- **再查 `INCLUDE_TO_LIBDEPS`**，命中则收集对应库名
- 未匹配的头文件不报错（可能是项目内头文件或未知库），但记日志

**`BuildTool.execute`** 合并：
```python
explicit_lib_deps = args.get("lib_deps", [])
scanned_lib_deps = scan_lib_deps_from_code(args["code"])
merged = list(dict.fromkeys(explicit_lib_deps + scanned_lib_deps))  # 去重保序
```

**`BuildTool.description`** 追加说明：
> "如果代码用了第三方库（如 Adafruit_NeoPixel、DHT、FastLED 等），可传 lib_deps 参数显式声明；不传时工具会自动扫描 #include 推断常见库。"

**编译失败提示**：当错误包含 `No such file or directory` 且未传 `lib_deps` 时，hint 里提醒"可在 lib_deps 参数中声明依赖库名"。

### 3. 内置映射表（部分）

```python
INCLUDE_TO_LIBDEPS = {
    "Adafruit_NeoPixel.h": "adafruit/Adafruit NeoPixel",
    "DHT.h": "adafruit/DHT sensor library",
    "DHT_U.h": "adafruit/DHT sensor library",
    "Adafruit_Sensor.h": "adafruit/Adafruit Unified Sensor",
    "FastLED.h": "fastled/FastLED",
    "ArduinoJson.h": "bblanchon/ArduinoJson",
    "PubSubClient.h": "knolleary/PubSubClient",
    "OneWire.h": "paulstoffregen/OneWire",
    "DallasTemperature.h": "milesburton/DallasTemperature",
    "Adafruit_SSD1306.h": "adafruit/Adafruit SSD1306",
    "Adafruit_GFX.h": "adafruit/Adafruit GFX Library",
    "Adafruit_BMP280.h": "adafruit/Adafruit BMP280 Library",
    "Adafruit_BME280.h": "adafruit/Adafruit BME280 Library",
    "Adafruit_MPU6050.h": "adafruit/Adafruit MPU6050",
    "Adafruit_AHTX0.h": "adafruit/Adafruit AHTX0",
    "Adafruit_SHT31.h": "adafruit/Adafruit SHT31 Library",
    "U8g2lib.h": "olikraus/U8g2",
    "LiquidCrystal_I2C.h": "marcoschwartz/LiquidCrystal_I2C",
    "ESPAsyncWebServer.h": "me-no-dev/ESPAsyncWebServer",
    "AsyncTCP.h": "me-no-dev/AsyncTCP",
    "IRremoteESP8266.h": "crankyoldgit/IRremoteESP8266",
    "IRremote.h": "z3t0/IRremote",
    "Servo.h": "arduino-libraries/Servo",
    "Stepper.h": "arduino-libraries/Stepper",
    "SD.h": "arduino-libraries/SD",  # 实际 ESP32 core 自带，BUILTIN 优先
    "RF24.h": "nRF24/RF24",
    "MFRC522.h": "miguelbalboa/MFRC522",
    "PCF8574.h": "xreef/PCF8574 library",
    "ESP32Servo.h": "madhephaestus/ESP32Servo",
    "TFT_eSPI.h": "bodmer/TFT_eSPI",
    "LovyanGFX.hpp": "lovyan03/LovyanGFX",
    "Wire.h": None,  # builtin
    # ...
}
```

```python
BUILTIN_HEADERS = {
    "Arduino.h", "Wire.h", "SPI.h", "WiFi.h", "WiFiClient.h", "WiFiServer.h",
    "WiFiAP.h", "HTTPClient.h", "WebServer.h", "EEPROM.h", "Preferences.h",
    "FS.h", "SD.h", "SPIFFS.h", "LittleFS.h", "Update.h", "BLEDevice.h",
    "BLEServer.h", "BLEUtils.h", "BLEScan.h", "BLEAdvertisedDevice.h",
    "HardwareSerial.h", "Serial.h", "Print.h", "Stream.h", "String.h",
    "Esp.h", "esp_system.h", "esp_sleep.h", "esp_timer.h", "esp_log.h",
    "freertos/FreeRTOS.h", "freertos/task.h", "freertos/queue.h",
    "driver/gpio.h", "driver/uart.h", "driver/i2c.h", "driver/spi.h",
    "driver/ledc.h", "driver/adc.h", "driver/pwm.h",
    "Hal.h", "stm32f1xx_hal.h", "stm32f4xx_hal.h",  # STM32 HAL
    "Adafruit_TinyUSB.h",  # ESP32 core 自带
}
```

> 注：`SD.h` 在 ESP32 core 自带，但在 STM32 上需要库。当前先按 builtin 处理（ESP32 是主要场景）。

## 测试要点

1. **自动扫描**：传一段 `#include <Adafruit_NeoPixel.h>` 的代码，不传 `lib_deps`，编译时应自动下载 Adafruit NeoPixel
2. **显式 + 扫描合并**：传 `lib_deps=["adafruit/DHT sensor library"]`，代码里同时有 `Adafruit_NeoPixel.h` 和 `DHT.h`，两个库都应下载
3. **去重**：显式传的库名和扫描出来的重复时，只下载一次
4. **内置库不误装**：`#include <WiFi.h>` 不会触发下载
5. **未知头文件不报错**：`#include <MyCustomLib.h>` 不在映射表里，不报错，只记日志
6. **lib_deps 写进 ini**：临时项目的 `platformio.ini` 应包含 `lib_deps =` 字段

## 非目标

- 不做头文件→库名的模糊匹配（如 `MyLib.h` 不在表里就跳过）
- 不支持从代码里解析 `// requires: owner/repo` 注释
- 不做库版本锁定（用 PlatformIO 默认最新版）
- 不改 `run_command` 工具的行为（Agent 仍可手动 `pio lib install`，只是不再必要）
