/**
 * ESP32-S3 WiFi 温湿度监测站
 * 
 * 功能：
 * - DHT22 读取温湿度
 * - OLED 显示实时数据
 * - WiFi AP + WebServer 远程查看
 * 
 * 硬件接线：
 *   DHT22 数据脚 → GPIO4
 *   OLED SDA    → GPIO5
 *   OLED SCL    → GPIO6
 *   OLED VCC    → 3.3V
 *   OLED GND    → GND
 *   DHT22 VCC   → 3.3V
 *   DHT22 GND   → GND
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_GFX.h>

// ============ 引脚定义 ============
#define DHTPIN          4
#define DHTTYPE         DHT22
#define OLED_SDA        5
#define OLED_SCL        6
#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT   64

// ============ WiFi 配置 ============
const char* ssid = "ESP32-S3-Weather";
const char* password = "12345678";

// ============ 全局对象 ============
DHT dht(DHTPIN, DHTTYPE);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
WebServer server(80);

// ============ 数据变量 ============
float temperature = 0.0f;
float humidity = 0.0f;
unsigned long lastRead = 0;
const unsigned long readInterval = 2000;  // 2s 读一次

// ============ HTML 页面 ============
const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" name="viewport" content="width=device-width,initial-scale=1">
  <title>ESP32-S3 温湿度监测</title>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#f0f2f5;display:flex;justify-content:center;align-items:center;
         min-height:100vh;padding:20px}
    .card{background:#fff;border-radius:16px;padding:32px;box-shadow:0 4px 24px rgba(0,0,0,.1);
          max-width:400px;width:100%;text-align:center}
    h1{color:#1a1a2e;font-size:24px;margin-bottom:24px}
    .sensor-row{display:flex;justify-content:space-around;gap:16px}
    .sensor-box{flex:1;background:#f8f9fa;border-radius:12px;padding:20px}
    .sensor-box.temp{border-top:4px solid #ff6b6b}
    .sensor-box.humi{border-top:4px solid #4dabf7}
    .value{font-size:36px;font-weight:700;color:#1a1a2e}
    .unit{font-size:16px;color:#868e96;margin-top:4px}
    .label{font-size:14px;color:#adb5bd;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}
    .ip{color:#868e96;font-size:13px;margin-top:20px}
    .footer{margin-top:20px;padding-top:16px;border-top:1px solid #eee;
            font-size:12px;color:#adb5bd}
  </style>
</head>
<body>
  <div class="card">
    <h1>🌡️ 温湿度监测站</h1>
    <div class="sensor-row">
      <div class="sensor-box temp">
        <div class="label">温度</div>
        <div class="value" id="temp">--</div>
        <div class="unit">°C</div>
      </div>
      <div class="sensor-box humi">
        <div class="label">湿度</div>
        <div class="value" id="humi">--</div>
        <div class="unit">%</div>
      </div>
    </div>
    <div class="ip" id="ip"></div>
    <div class="footer">ESP32-S3 Weather Station</div>
  </div>
  <script>
    function fetchData(){
      fetch("/api").then(r=>r.json()).then(d=>{
        document.getElementById("temp").textContent = d.temp.toFixed(1);
        document.getElementById("humi").textContent = d.humi.toFixed(1);
      }).catch(()=>{});
    }
    document.getElementById("ip").textContent = "IP: " + window.location.hostname;
    fetchData();
    setInterval(fetchData, 3000);
  </script>
</body>
</html>
)rawliteral";

// ============ 读取传感器 ============
void readSensor() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();

  if (isnan(h) || isnan(t)) {
    Serial.println("[DHT] 读取失败");
    return;
  }

  temperature = t;
  humidity = h;

  Serial.printf("[DHT] %.1f°C  %.1f%%\n", temperature, humidity);
}

// ============ 更新 OLED ============
void updateDisplay() {
  display.clearDisplay();

  // 标题
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(20, 0);
  display.println("Weather Station");

  // 分隔线
  display.drawLine(0, 12, 127, 12, SSD1306_WHITE);

  // 温度
  display.setTextSize(2);
  display.setCursor(0, 20);
  display.print("Temp:");
  display.setCursor(60, 20);
  display.printf("%.1f", temperature);
  display.setTextSize(1);
  display.setCursor(114, 24);
  display.print("C");

  // 湿度
  display.setTextSize(2);
  display.setCursor(0, 42);
  display.print("Humi:");
  display.setCursor(60, 42);
  display.printf("%.1f", humidity);
  display.setTextSize(1);
  display.setCursor(114, 46);
  display.print("%");

  // WiFi 图标
  if (WiFi.getMode() == WIFI_AP) {
    display.fillCircle(122, 6, 3, SSD1306_WHITE);
  }

  display.display();
}

// ============ API 路由 ============
void handleAPI() {
  String json = "{\"temp\":" + String(temperature, 1) +
                ",\"humi\":" + String(humidity, 1) + "}";
  server.send(200, "application/json", json);
}

void handleRoot() {
  server.send(200, "text/html", index_html);
}

void handleNotFound() {
  server.send(404, "text/plain", "404 Not Found");
}

// ============ 初始化 ============
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n===== ESP32-S3 温湿度监测站 =====\n");

  // ---- OLED ----
  Wire.begin(OLED_SDA, OLED_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("[OLED] 初始化失败");
  } else {
    Serial.println("[OLED] 初始化成功");
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(10, 24);
    display.println("Starting...");
    display.display();
  }

  // ---- DHT22 ----
  dht.begin();
  Serial.println("[DHT] 传感器启动");

  // ---- WiFi AP 模式 ----
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ssid, password);
  Serial.printf("[WiFi] AP: %s | IP: %s\n", ssid, WiFi.softAPIP().toString().c_str());

  // ---- WebServer ----
  server.on("/", handleRoot);
  server.on("/api", handleAPI);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("[HTTP] Server 启动");

  // 首次读取
  delay(1000);
  readSensor();
  updateDisplay();

  Serial.println("===== 系统就绪 =====\n");
}

// ============ 主循环 ============
void loop() {
  server.handleClient();

  unsigned long now = millis();
  if (now - lastRead >= readInterval) {
    lastRead = now;
    readSensor();
    updateDisplay();
  }
}
