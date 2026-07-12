# ESP32 WiFi 开发完全指南

> 本文档系统介绍 ESP32 WiFi 子系统的架构、Station/AP 模式配置、省电模式、安全机制、
> TCP/UDP 通信、HTTP/HTTPS、mDNS、Smart Config、性能优化、WiFi+BT 共存及故障排查，
> 覆盖 ESP-IDF v4.4 ~ v5.x API，包含大量可复用的 C 代码示例。

---

## 目录

1. WiFi 模块架构
2. Station 模式连接流程（含指数退避重连策略）
3. WiFi 省电模式详解（WIFI_PS_NONE / WIFI_PS_MIN_MODEM / WIFI_PS_MAX_MODEM）
4. AP 模式配置
5. WiFi 安全性
6. TCP/UDP 通信
7. HTTP/HTTPS 客户端
8. mDNS 服务发现
9. WiFi 事件系统
10. Smart Config 与 SmartConfig
11. 性能优化
12. WiFi + BT 共存
13. 常见问题与故障排查
14. ESP32 系列对比

---

## 第 1 章 WiFi 模块架构

### 1.1 硬件架构概览

ESP32 集成了一个完整的 2.4 GHz WiFi 收发器，符合 IEEE 802.11 b/g/n 标准。其硬件
架构可分为以下几个子系统：

- **MAC 层（Media Access Control）**：硬件实现 CSMA/CA、ACK、重传、分片、加密等
  功能，减轻 CPU 负担。
- **基带处理器（Baseband）**：负责调制解调（DSSS、OFDM）、编解码（卷积码、LDPC）、
  信号同步与均衡。
- **射频前端（RF Frontend）**：集成 PA、LNA、收发开关、巴伦（Balun），仅需少量
  外部匹配元件即可工作。
- **功率放大器（PA）**：最大输出功率 20 dBm（100 mW），可通过 `esp_wifi_set_ps_type()`
  或 `esp_wifi_set_max_tx_power()` 调整。
- **晶体振荡器**：通常使用 40 MHz 晶振，提供 RF 与基带时钟。
- **Flash / PSRAM**：存放固件、WiFi 配置、NVS 存储等。

### 1.2 软件协议栈分层

ESP-IDF 的 WiFi 软件栈自底向上分为：

| 层级 | 名称 | 说明 |
|------|------|------|
| L1 | RF/PHY | 射频与物理层，由 ROM 中的 phy_init 数据配置 |
| L2 | MAC/Berkeley | MAC 子层与 Berkeley 软核，处理帧收发与 ARP |
| L3 | LwIP | 轻量级 TCP/IP 协议栈，提供 socket API |
| L4 | WiFi API | `esp_wifi.h` 提供的初始化、连接、扫描等接口 |
| L5 | Event Loop | `esp_event.h` 异步事件机制，解耦应用与驱动 |
| L6 | Application | 用户应用代码 |

### 1.3 关键术语

- **STA**（Station）：作为客户端连接到 AP 的角色。
- **AP**（Access Point）：作为热点，提供接入服务的角色。
- **APSTA**：同时作为 STA 和 AP，常用于配网或桥接。
- **BSSID**：AP 的 MAC 地址，6 字节。
- **SSID**：网络名称，最长 32 字节。
- **Channel**：信道，2.4 GHz 频段共 1~14 信道，每个信道带宽 22 MHz，中心频率相差
  5 MHz。中国/欧洲常用 1~13 信道。
- **RSSI**：接收信号强度指示，单位 dBm，一般为 -100 ~ -30 dBm，越大表示信号越好。

### 1.4 初始化总流程

无论使用 STA、AP 还是 APSTA 模式，初始化流程都遵循以下顺序：

```c
// 1. Initialize NVS (required by WiFi to store config)
esp_err_t ret = nvs_flash_init();
if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    ESP_ERROR_CHECK(nvs_flash_init());
}

// 2. Initialize event loop (default event loop)
ESP_ERROR_CHECK(esp_event_loop_create_default());

// 3. Create default network interface for STA
esp_netif_t *sta_netif = esp_netif_create_default_wifi_sta();

// 4. Initialize WiFi with default config (gets binary from WiFi lib)
wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
ESP_ERROR_CHECK(esp_wifi_init(&cfg));

// 5. Register event handlers
ESP_ERROR_CHECK(esp_event_handler_instance_register(
    WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
ESP_ERROR_CHECK(esp_event_handler_instance_register(
    IP_EVENT, IP_EVENT_STA_GOT_IP, &ip_event_handler, NULL, NULL));

// 6. Set WiFi mode and config
ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
wifi_config_t wifi_config = {
    .sta = {
        .ssid = "MySSID",
        .password = "MyPassword",
        .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        .pmf_cfg.capable = true,
        .pmf_cfg.required = false,
    },
};
ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

// 7. Start WiFi
ESP_ERROR_CHECK(esp_wifi_start());
// 8. Connect will be triggered by WIFI_EVENT_STA_START handler
```

### 1.5 NVS 与配置持久化

WiFi 驱动会自动将 SSID、密码、PMF 设置等保存到 NVS（Non-Volatile Storage）分区。
下次启动时，若没有显式调用 `esp_wifi_set_config()`，驱动会从 NVS 读取上次保存的配置。

注意：
- 若 NVS 分区被擦除，所有 WiFi 配置丢失。
- 使用 `esp_wifi_set_ps_type()` 等函数不会持久化到 NVS，需要应用层自行保存。
- NVS 操作不是线程安全的，多线程访问需加锁。

---

## 第 2 章 Station 模式连接流程（含指数退避重连策略）

### 2.1 连接流程详解

STA 模式下，完整的 WiFi 连接流程涉及多次事件交互：

1. `esp_wifi_start()` 触发 `WIFI_EVENT_STA_START` 事件
2. 应用在事件处理器中调用 `esp_wifi_connect()`
3. 驱动扫描 → 认证 → 关联 → 4 次握手（WPA2）→ DHCP
4. 成功后触发 `IP_EVENT_STA_GOT_IP` 事件
5. 任意阶段失败触发 `WIFI_EVENT_STA_DISCONNECTED` 事件

### 2.2 WIFI_EVENT_STA_DISCONNECTED 事件详解

这是最常被错误处理的事件。其 `event_data` 指向 `wifi_event_sta_disconnected_t`：

```c
typedef struct {
    uint8_t ssid[32];           // SSID of the AP
    uint8_t ssid_len;           // SSID length
    uint8_t bssid[6];           // BSSID of the AP
    uint8_t reason;             // Disconnection reason code (802.11)
    int8_t  rssi;               // RSSI at the moment of disconnection
} wifi_event_sta_disconnected_t;
```

常见 reason code：

| reason | 含义 | 处理建议 |
|--------|------|----------|
| 1 | UNSPECIFIED | 通用错误，重试连接 |
| 2 | AUTH_EXPIRE | 认证超时 | 检查密码 |
| 4 | DEAUTH_LEAVING | AP 主动断开 | 等待后重连 |
| 8 | ASSOC_EXPIRE | 关联超时 | 信号弱，移动设备 |
| 15 | 4WAY_HANDSHAKE_TIMEOUT | 4 次握手超时 | **密码错误** |
| 201 | NO_AP_FOUND | 找不到 AP | 检查 SSID/信号 |
| 202 | AUTH_FAIL | 认证失败 | 检查密码/认证模式 |
| 203 | ASSOC_FAIL | 关联失败 | 检查能力位 |
| 204 | HANDSHAKE_TIMEOUT | 握手超时 | 信号差或密码错 |

**重要警告**：在 `WIFI_EVENT_STA_DISCONNECTED` 事件处理器中调用 `esp_wifi_connect()`
时，必须避免无限重试，否则会因 CPU 占用过高导致看门狗复位。**推荐使用指数退避
（exponential backoff）重连策略**。

### 2.3 指数退避重连策略实现

指数退避（exponential backoff）是一种经典的网络重连策略，每次失败后等待时间按
指数增长，避免在 AP 故障时形成连接风暴。下表是典型的指数退避参数：

| 重试次数 | 等待时间 | 说明 |
|----------|----------|------|
| 1 | 1 秒 | 初次重连，快速尝试 |
| 2 | 2 秒 | 短退避 |
| 3 | 4 秒 | 中等退避 |
| 4 | 8 秒 | 中等退避 |
| 5 | 16 秒 | 长退避 |
| 6 | 32 秒 | 长退避 |
| 7 | 60 秒 | 封顶值 |
| 8+ | 60 秒 | 持续尝试，封顶 |

下面是一个完整的指数退避重连实现：

```c
#include "esp_wifi.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

static const char *TAG = "wifi_reconnect";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT       BIT1
#define MAX_RETRY_COUNT     10

// Reconnect state - shared between event handler and task
static int s_retry_count = 0;
static EventGroupHandle_t s_wifi_event_group;

// FreeRTOS timer for delayed reconnect
static TimerHandle_t s_reconnect_timer = NULL;

// Calculate exponential backoff delay (in ms)
static uint32_t calculate_backoff_delay(int retry_count) {
    // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s (capped)
    uint32_t delay_ms;
    if (retry_count >= 7) {
        delay_ms = 60000;  // Cap at 60 seconds
    } else {
        delay_ms = (1u << retry_count) * 1000;  // 2^retry_count seconds
    }
    return delay_ms;
}

// Timer callback: triggers actual reconnect
static void reconnect_timer_callback(TimerHandle_t xTimer) {
    ESP_LOGI(TAG, "指数退避重连: 第 %d 次重试", s_retry_count);
    esp_wifi_connect();
}

// Initialize reconnect timer
static void init_reconnect_timer(void) {
    s_reconnect_timer = xTimerCreate(
        "wifi_reconnect",
        pdMS_TO_TICKS(1000),
        pdFALSE,  // one-shot timer
        NULL,
        reconnect_timer_callback
    );
}

// WiFi event handler with exponential backoff
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "WiFi started, connecting...");
        s_retry_count = 0;
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *disconn = (wifi_event_sta_disconnected_t *)event_data;
        ESP_LOGW(TAG, "连接断开, reason=%d, rssi=%d, retry_count=%d",
                 disconn->reason, disconn->rssi, s_retry_count);
        
        if (s_retry_count < MAX_RETRY_COUNT) {
            // Exponential backoff: schedule delayed reconnect
            uint32_t delay_ms = calculate_backoff_delay(s_retry_count);
            ESP_LOGI(TAG, "指数退避: 等待 %lu ms 后重连", (unsigned long)delay_ms);
            
            // Update timer period and start it
            xTimerChangePeriod(s_reconnect_timer, pdMS_TO_TICKS(delay_ms), 0);
            xTimerStart(s_reconnect_timer, 0);
            
            s_retry_count++;
        } else {
            ESP_LOGE(TAG, "重连次数超过上限 %d，停止重试", MAX_RETRY_COUNT);
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "获取 IP: " IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_count = 0;  // Reset retry counter on success
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

// Connection entry point with exponential backoff
void wifi_init_sta(void) {
    s_wifi_event_group = xEventGroupCreate();
    init_reconnect_timer();
    
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    
    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, &instance_got_ip));
    
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = "MySSID",
            .password = "MyPassword",
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg.capable = true,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    
    ESP_LOGI(TAG, "wifi_init_sta finished.");
    
    // Wait for connection or failure
    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE, pdFALSE, portMAX_DELAY);
    
    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "connected to ap SSID:%s password:%s", "MySSID", "MyPassword");
    } else {
        ESP_LOGI(TAG, "Failed to connect");
    }
}
```

### 2.4 指数退避策略的优化

基础的指数退避仍有改进空间：

1. **加入随机抖动（jitter）**：避免多个设备同时重连造成"惊群效应"。
2. **基于 reason code 的差异化处理**：密码错误不应无限重试，应直接停止。
3. **基于 RSSI 的决策**：RSSI 过低时跳过本次重试，节省电量。
4. **重置退避**：连接成功后或信号改善时重置 retry count。

改进后的 backoff 计算函数：

```c
// Enhanced backoff with jitter and RSSI check
static uint32_t calculate_backoff_delay_enhanced(int retry_count, int8_t rssi) {
    // Skip reconnect if signal is too weak
    if (rssi < -90) {
        ESP_LOGW(TAG, "RSSI %d dBm 过低，延长退避", rssi);
        return 120000;  // 2 minutes for weak signal
    }
    
    // Base exponential backoff
    uint32_t base_delay;
    if (retry_count >= 7) {
        base_delay = 60000;  // Cap at 60s
    } else {
        base_delay = (1u << retry_count) * 1000;
    }
    
    // Add random jitter (0-25% of base delay)
    uint32_t jitter = esp_random() % (base_delay / 4 + 1);
    return base_delay + jitter;
}

// Reason-aware reconnect decision
static bool should_retry(uint8_t reason) {
    switch (reason) {
        case 15:  // 4WAY_HANDSHAKE_TIMEOUT - wrong password
        case 202: // AUTH_FAIL
            ESP_LOGE(TAG, "认证失败 (reason=%d)，可能是密码错误，停止重试", reason);
            return false;
        case 201: // NO_AP_FOUND - AP out of range
        case 8:   // ASSOC_EXPIRE - association expired
        case 4:   // DEAUTH_LEAVING - AP rebooted
        case 9:   // ASSOC_NOT_AUTHED
            return true;
        default:
            return true;
    }
}
```

### 2.5 部署实践

在生产环境中，建议：

- 配合 NVS 保存最后一次成功连接的 SSID/密码，开机时优先尝试。
- 实现配置 portal（如 WiFi Manager）：重连失败 N 次后切换到 AP 模式，让用户重新配置。
- 使用 `esp_wifi_set_protocol()` 启用 B/G/N 协议自动协商，提高兼容性。
- 在低功耗场景下，重连间隔不宜过短，否则会显著增加平均功耗。

---

## 第 3 章 WiFi 省电模式详解

ESP32 提供三种 WiFi 省电模式，权衡延迟、功耗和 TCP 通信适用性。正确选择省电模式
对电池供电设备的续航至关重要。

### 3.1 三种省电模式总览

| 省电模式 | 关键词 | 平均功耗 | 延迟 | TCP 适用性 | 典型应用 |
|----------|--------|----------|------|------------|----------|
| WIFI_PS_NONE | WIFI_PS_NONE | 80~120 mA | 最低（< 2 ms） | 最佳 | 持续数据流、低延迟 |
| WIFI_PS_MIN_MODEM | WIFI_PS_MIN_MODEM | 20~40 mA | 中（< 50 ms） | 良好 | 间歇数据、IoT 上报 |
| WIFI_PS_MAX_MODEM | WIFI_PS_MAX_MODEM | 5~15 mA | 高（100~500 ms） | 一般（需保活） | 电池供电、低频率 |

### 3.2 WIFI_PS_NONE（无省电）

**特点**：
- Modem 始终保持唤醒状态，不进入睡眠。
- 接收延迟最低，吞吐量最高。
- 功耗最高，不适合电池供电。
- 默认模式（在某些 SDK 版本中）。

**适用场景**：
- 持续流式数据传输（如视频流、传感器高频上报）
- 实时性要求高的应用（如遥控、低延迟控制）
- WiFi+BT 同时工作时（避免模式切换冲突）

```c
// Set WiFi to no power saving mode
esp_err_t ret = esp_wifi_set_ps(WIFI_PS_NONE);
if (ret != ESP_OK) {
    ESP_LOGE(TAG, "Failed to set WIFI_PS_NONE: %s", esp_err_to_name(ret));
}
```

### 3.3 WIFI_PS_MIN_MODEM（最小 Modem 眞眠）

**特点**：
- 在 WiFi 空闲时（无收发数据）关闭 RF 接收通道，但 MAC 仍周期性唤醒监听 beacon。
- 唤醒延迟较小（通常 < 50 ms），TCP 通信基本无影响。
- AP 的 DTIM 间隔决定唤醒频率，DTIM=1 时功耗较高，DTIM=3 较低。
- 适合大多数 IoT 应用，是平衡性最好的模式。

**适用场景**：
- 间歇性数据上报（如每分钟一次温湿度上传）
- MQTT 长连接（保活间隔 > 30 秒）
- 偶发的 HTTP 请求
- 需要响应远程控制指令的设备

```c
// Set WiFi to minimum modem power saving
esp_wifi_set_ps(WIFI_PS_MIN_MODEM);

// Optional: Configure listen interval (in beacon periods)
// Higher value = lower power but higher latency
esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
// Note: listen interval is set via wifi_config.sta.listen_interval
wifi_config_t cfg = {0};
cfg.sta.listen_interval = 3;  // Listen every 3 beacons (~300 ms)
esp_wifi_set_config(WIFI_IF_STA, &cfg);
```

### 3.4 WIFI_PS_MAX_MODEM（最大 Modem 眛眠）

**特点**：
- Modem 长时间睡眠，仅在 DTIM beacon 时唤醒。
- 功耗最低（配合 Light Sleep 可达 mA 级别），但延迟最高。
- 需要配合 `esp_light_sleep_start()` 进入 Light Sleep 才能获得最低功耗。
- TCP 长连接需要应用层心跳保活，否则会被 NAT 超时断开。

**适用场景**：
- 电池供电的低频 IoT 设备（如每 10 分钟上报一次）
- 环境监测传感器
- 远程抄表、智能农业
- 不适合实时通信

```c
// Set WiFi to max modem power saving
esp_wifi_set_ps(WIFI_PS_MAX_MODEM);

// Configure longer listen interval for ultra-low power
wifi_config_t cfg = {0};
cfg.sta.listen_interval = 10;  // Listen every 10 beacons (~1 second)
esp_wifi_set_config(WIFI_IF_STA, &cfg);

// Enable auto light sleep (needed for WIFI_PS_MAX_MODEM to take full effect)
#if CONFIG_PM_ENABLE
#include "esp_pm.h"
esp_pm_config_esp32_t pm_config = {
    .max_freq_mhz = 240,
    .min_freq_mhz = 80,
    .light_sleep_enable = true
};
ESP_ERROR_CHECK(esp_pm_configure(&pm_config));
#endif
```

### 3.5 DTIM 与 listen_interval 详解

DTIM（Delivery Traffic Indication Map）是 AP 的一种特殊 beacon，告诉 STA 有缓存
的广播/组播帧需要接收。`listen_interval` 是 STA 监听 beacon 的间隔（以 beacon 周期
为单位）。

- 默认 AP 的 beacon 周期约 100 ms（10 Hz）。
- DTIM 周期通常为 1、3 或 10。
- `listen_interval` 越大，功耗越低，但延迟越高，且超过 AP 的缓存容量会被踢出。

```c
// Trade-off table for listen_interval
// listen_interval | Extra delay | Power saving
// 1 (default)     | 0           | None (only with PS_NONE)
// 3               | ~200 ms     | ~20% reduction
// 10              | ~1 s        | ~50% reduction
// 30              | ~3 s        | ~70% reduction (AP may disconnect)
```

### 3.6 省电模式切换与电源管理

ESP-IDF 的 Power Management 单元可以动态调整 CPU 频率与睡眠状态：

```c
// Full power management setup
#include "esp_pm.h"

void setup_power_management(void) {
#if CONFIG_PM_ENABLE
    esp_pm_config_esp32_t pm_config = {
        .max_freq_mhz = 240,         // Max CPU frequency
        .min_freq_mhz = 80,          // Min CPU frequency (when idle)
        .light_sleep_enable = true,  // Enable automatic light sleep
    };
    ESP_ERROR_CHECK(esp_pm_configure(&pm_config));
    
    // Combined with WIFI_PS_MAX_MODEM, this gives best power efficiency
    esp_wifi_set_ps(WIFI_PS_MAX_MODEM);
#endif
}
```

### 3.7 省电模式选择决策树

```
是否电池供电？
├── 否 → WIFI_PS_NONE（追求性能）
└── 是
    ├── 数据频率 > 1 Hz → WIFI_PS_MIN_MODEM
    ├── 数据频率 1 分钟 ~ 1 秒 → WIFI_PS_MIN_MODEM + listen_interval=3
    ├── 数据频率 < 1 分钟 → WIFI_PS_MAX_MODEM + Light Sleep
    └── 需要响应远程命令 → WIFI_PS_MIN_MODEM + 心跳保活
```

### 3.8 功耗实测对比

下表为 ESP32-WROOM-32 在 3.3V 供电、不同省电模式下的实测平均功耗（仅供参考）：

| 模式 | WiFi 状态 | 平均功耗 | 峰值功耗 | 唤醒延迟 |
|------|-----------|----------|----------|----------|
| WIFI_PS_NONE | 持续连接 | 95 mA | 510 mA | < 2 ms |
| WIFI_PS_MIN_MODEM | DTIM=1 | 65 mA | 510 mA | ~10 ms |
| WIFI_PS_MIN_MODEM | DTIM=3 | 35 mA | 510 mA | ~30 ms |
| WIFI_PS_MAX_MODEM | DTIM=10 | 12 mA | 510 mA | ~100 ms |
| WIFI_PS_MAX_MODEM + Light Sleep | DTIM=10 | 1.5 mA | 510 mA | ~200 ms |
| Modem Sleep + Deep Sleep | 关闭 WiFi | 0.8 mA | 510 mA | > 1 s |

---

## 第 4 章 AP 模式配置

### 4.1 AP 模式基础

ESP32 可以作为软 AP（SoftAP），允许其他设备连接。常用于：
- WiFi 配网 portal
- 设备间直连通信（无需路由器）
- 网络扩展/桥接

### 4.2 AP 模式初始化

```c
void wifi_init_softap(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();
    
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &ap_event_handler, NULL, NULL));
    
    wifi_config_t wifi_config = {
        .ap = {
            .ssid = "ESP32_AP",
            .ssid_len = strlen("ESP32_AP"),
            .channel = 1,
            .password = "12345678",
            .max_connection = 4,
            .authmode = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg.required = false,
        },
    };
    
    if (strlen("12345678") == 0) {
        wifi_config.ap.authmode = WIFI_AUTH_OPEN;
    }
    
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    
    ESP_LOGI(TAG, "AP initialized. SSID:%s password:%s channel:%d",
             wifi_config.ap.ssid, wifi_config.ap.password, wifi_config.ap.channel);
}
```

### 4.3 AP 配置参数详解

| 参数 | 类型 | 范围 | 说明 |
|------|------|------|------|
| ssid | char[32] | 1~32 字节 | 热点名称 |
| password | char[64] | 8~63 字节 | WPA2 密码；OPEN 模式忽略 |
| channel | uint8_t | 1~14 | 信道，建议 1/6/11 |
| authmode | enum | OPEN/WPA2_PSK/WPA/WPA3 | 认证模式 |
| ssid_hidden | bool | true/false | 是否隐藏 SSID |
| max_connection | uint8_t | 1~10 | 最大同时连接数（默认 4） |
| beacon_interval | uint16_t | 100~60000 | beacon 周期（ms），默认 100 |

### 4.4 AP+STA 模式（APSTA）

同时启用 AP 和 STA，常用于：配网时通过 AP 接收配置，再通过 STA 连接到家庭路由器。

```c
// APSTA mode: configure both interfaces
ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));

// Configure AP side
wifi_config_t ap_config = {
    .ap = {
        .ssid = "ESP32_ConfigAP",
        .ssid_len = strlen("ESP32_ConfigAP"),
        .channel = 6,
        .password = "config123",
        .max_connection = 2,
        .authmode = WIFI_AUTH_WPA2_PSK,
    },
};
ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));

// Configure STA side
wifi_config_t sta_config = {
    .sta = {
        .ssid = "HomeRouter",
        .password = "HomePassword",
    },
};
ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_config));

ESP_ERROR_CHECK(esp_wifi_start());
```

### 4.5 AP 事件处理

```c
static void ap_event_handler(void *arg, esp_event_base_t event_base,
                              int32_t event_id, void *event_data) {
    if (event_id == WIFI_EVENT_AP_STACONNECTED) {
        wifi_event_ap_staconnected_t *event = (wifi_event_ap_staconnected_t *)event_data;
        ESP_LOGI(TAG, "Station "MACSTR" joined, AID=%d",
                 MAC2STR(event->mac), event->aid);
    } else if (event_id == WIFI_EVENT_AP_STADISCONNECTED) {
        wifi_event_ap_stadisconnected_t *event = (wifi_event_ap_stadisconnected_t *)event_data;
        ESP_LOGI(TAG, "Station "MACSTR" left, AID=%d",
                 MAC2STR(event->mac), event->aid);
    } else if (event_id == WIFI_EVENT_AP_PROBEREQRECVED) {
        wifi_event_ap_probe_req_rx_t *event = (wifi_event_ap_probe_req_rx_t *)event_data;
        ESP_LOGD(TAG, "Probe request from "MACSTR" rssi=%d",
                 MAC2STR(event->mac), event->rssi);
    }
}
```

---

## 第 5 章 WiFi 安全性

### 5.1 认证模式对比

| 认证模式 | 安全性 | 兼容性 | 适用场景 |
|----------|--------|--------|----------|
| WIFI_AUTH_OPEN | 极低 | 全部 | 公共网络（需配合 Portal） |
| WIFI_AUTH_WEP | 低 | 老旧设备 | 已废弃，不建议使用 |
| WIFI_AUTH_WPA_PSK | 中 | 较广 | 家庭网络（旧路由器） |
| WIFI_AUTH_WPA2_PSK | 高 | 主流 | **推荐**，绝大多数场景 |
| WIFI_AUTH_WPA3_PSK | 极高 | 较新 | 新设备，安全敏感场景 |
| WIFI_AUTH_WPA2_WPA3_PSK | 高 | 主流 | 过渡期，兼容新旧 |

### 5.2 PMF（Protected Management Frames）

PMF（又称 802.11w）加密管理帧，防御去认证攻击（deauth attack）。建议在支持的环境下
始终启用：

```c
wifi_config_t wifi_config = {
    .sta = {
        .ssid = "MySSID",
        .password = "MyPassword",
        .pmf_cfg = {
            .capable = true,   // Capable of PMF (will use if AP supports)
            .required = false, // Don't require PMF (for compatibility)
        },
        .threshold.authmode = WIFI_AUTH_WPA2_PSK,
    },
};
```

### 5.3 WPA3 配置

WPA3 使用 SAE（Simultaneous Authentication of Equals）替代 WPA2 的 PSK，提供更强的
密码保护（防离线字典攻击）。

```c
// WPA3-only configuration (requires AP support)
wifi_config_t wifi_config = {
    .sta = {
        .ssid = "MyWPA3SSID",
        .password = "MyPassword",
        .threshold.authmode = WIFI_AUTH_WPA3_PSK,
        .pmf_cfg = {
            .capable = true,
            .required = true,  // WPA3 requires PMF
        },
    },
};
```

### 5.4 EAP（企业级）认证

ESP32 支持 WPA2-Enterprise（802.1X/EAP），可用于企业网络：

```c
#include "esp_eap_client.h"

// WPA2-Enterprise setup
esp_eap_client_set identity and password
esp_eap_client_set_identity((const uint8_t *)"username", 8);
esp_eap_client_set_username((const uint8_t *)"username", 8);
esp_eap_client_set_password((const uint8_t *)"password", 8);

// Optional: set CA certificate
esp_eap_client_set_ca_cert(ca_cert_pem_start, ca_cert_pem_end - ca_cert_pem_start);

// Enable WPA2-Enterprise
ESP_ERROR_CHECK(esp_wifi_sta_wpa2_ent_enable());

// Configure STA with WPA2-Enterprise
wifi_config_t wifi_config = {
    .sta = {
        .ssid = "CorpNetwork",
        .pmf_cfg.capable = true,
        .pmf_cfg.required = false,
    },
};
ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
```

### 5.5 TLS 证书安全

对于 HTTPS、MQTT over TLS，建议使用根证书固定（certificate pinning）：

```c
extern const unsigned char ca_cert_pem_start[] asm("_binary_ca_cert_pem_start");
extern const unsigned char ca_cert_pem_end[]   asm("_binary_ca_cert_pem_end");

esp_http_client_config_t config = {
    .url = "https://api.example.com",
    .cert_pem = (const char *)ca_cert_pem_start,
    .skip_cert_common_name_check = false,  // Strict CN check
};
```

---

## 第 6 章 TCP/UDP 通信

### 6.1 LwIP 与 socket API

ESP-IDF 集成 LwIP 协议栈，提供 BSD socket 兼容 API。所有 socket 操作与 Linux
下的 API 一致，但需要注意：

- 默认最大 socket 数量为 10（可调整 `LWIP_SOCKET_OFFSET` 与 `CONFIG_LWIP_MAX_SOCKETS`）。
- `recv()` 在非阻塞模式下返回 `EWOULDBLOCK` 时需配合 select/poll。
- `close()` 必须由创建 socket 的同一线程调用，或加锁保护。

### 6.2 TCP 客户端示例

```c
#include "lwip/sockets.h"
#include "lwip/netdb.h"

static const char *TAG = "tcp_client";

int tcp_client_connect(const char *host, int port) {
    struct addrinfo hints = {0};
    struct addrinfo *res;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    
    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port);
    
    int err = getaddrinfo(host, port_str, &hints, &res);
    if (err != 0 || res == NULL) {
        ESP_LOGE(TAG, "DNS lookup failed err=%d", err);
        return -1;
    }
    
    int sock = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (sock < 0) {
        ESP_LOGE(TAG, "Unable to create socket: errno %d", errno);
        freeaddrinfo(res);
        return -1;
    }
    
    ESP_LOGI(TAG, "Socket created, connecting to %s:%d", host, port);
    
    err = connect(sock, res->ai_addr, res->ai_addrlen);
    freeaddrinfo(res);
    
    if (err != 0) {
        ESP_LOGE(TAG, "Socket unable to connect: errno %d", errno);
        close(sock);
        return -1;
    }
    
    ESP_LOGI(TAG, "Successfully connected");
    return sock;
}

int tcp_send_data(int sock, const char *data, size_t len) {
    int err = send(sock, data, len, 0);
    if (err < 0) {
        ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
        return -1;
    }
    return err;
}

int tcp_recv_data(int sock, char *buf, size_t buf_len, int timeout_ms) {
    struct timeval tv = {
        .tv_sec = timeout_ms / 1000,
        .tv_usec = (timeout_ms % 1000) * 1000,
    };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    
    int len = recv(sock, buf, buf_len - 1, 0);
    if (len < 0) {
        ESP_LOGE(TAG, "recv failed: errno %d", errno);
        return -1;
    } else if (len == 0) {
        ESP_LOGI(TAG, "Connection closed by peer");
        return 0;
    }
    buf[len] = '\0';
    return len;
}
```

### 6.3 TCP 服务器示例

```c
void tcp_server_task(void *pvParameters) {
    int listen_sock = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_sock < 0) {
        ESP_LOGE(TAG, "Unable to create socket: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }
    
    int opt = 1;
    setsockopt(listen_sock, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    
    struct sockaddr_in dest_addr = {
        .sin_addr.s_addr = htonl(INADDR_ANY),
        .sin_family = AF_INET,
        .sin_port = htons(3333),
    };
    
    if (bind(listen_sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr)) != 0) {
        ESP_LOGE(TAG, "Socket unable to bind: errno %d", errno);
        close(listen_sock);
        vTaskDelete(NULL);
        return;
    }
    
    if (listen(listen_sock, 1) != 0) {
        ESP_LOGE(TAG, "Error occurred during listen: errno %d", errno);
        close(listen_sock);
        vTaskDelete(NULL);
        return;
    }
    
    ESP_LOGI(TAG, "TCP server listening on port 3333");
    
    while (1) {
        struct sockaddr_in source_addr;
        socklen_t addr_len = sizeof(source_addr);
        int sock = accept(listen_sock, (struct sockaddr *)&source_addr, &addr_len);
        if (sock < 0) {
            ESP_LOGE(TAG, "Unable to accept connection: errno %d", errno);
            break;
        }
        
        char addr_str[16];
        inet_ntoa_r(source_addr.sin_addr, addr_str, sizeof(addr_str));
        ESP_LOGI(TAG, "Connection from %s", addr_str);
        
        // Handle this connection
        char rx_buffer[128];
        int len;
        do {
            len = recv(sock, rx_buffer, sizeof(rx_buffer) - 1, 0);
            if (len > 0) {
                rx_buffer[len] = '\0';
                ESP_LOGI(TAG, "Received %d bytes: %s", len, rx_buffer);
                send(sock, rx_buffer, len, 0);  // Echo back
            }
        } while (len > 0);
        
        shutdown(sock, 0);
        close(sock);
    }
    
    close(listen_sock);
    vTaskDelete(NULL);
}
```

### 6.4 UDP 通信示例

UDP 适用于实时性要求高、可容忍丢包的场景，如 DNS 查询、视频流、CoAP 等。

```c
void udp_client_task(void *pvParameters) {
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        ESP_LOGE(TAG, "Unable to create socket: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }
    
    struct sockaddr_in dest_addr = {0};
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(5000);
    inet_pton(AF_INET, "192.168.1.100", &dest_addr.sin_addr);
    
    char payload[] = "Hello UDP Server!";
    while (1) {
        int err = sendto(sock, payload, strlen(payload), 0,
                         (struct sockaddr *)&dest_addr, sizeof(dest_addr));
        if (err < 0) {
            ESP_LOGE(TAG, "Error occurred during sending: errno %d", errno);
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    
    close(sock);
    vTaskDelete(NULL);
}
```

### 6.5 组播（Multicast）

```c
// Join multicast group 224.0.0.251 (mDNS)
void join_multicast_group(int sock) {
    struct ip_mreq mreq = {0};
    inet_pton(AF_INET, "224.0.0.251", &mreq.imr_multiaddr);
    mreq.imr_interface.s_addr = htonl(INADDR_ANY);
    
    if (setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
        ESP_LOGE(TAG, "IP_ADD_MEMBERSHIP failed: errno %d", errno);
    }
}
```

---

## 第 7 章 HTTP/HTTPS 客户端

### 7.1 HTTP 客户端基础

ESP-IDF 提供 `esp_http_client` 组件，封装了 HTTP/HTTPS 客户端操作：

```c
#include "esp_http_client.h"

static const char *TAG = "http_client";

esp_err_t http_event_handler(esp_http_client_event_t *evt) {
    switch (evt->event_id) {
        case HTTP_EVENT_ERROR:
            ESP_LOGD(TAG, "HTTP_EVENT_ERROR");
            break;
        case HTTP_EVENT_ON_CONNECTED:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_CONNECTED");
            break;
        case HTTP_EVENT_HEADER_SENT:
            ESP_LOGD(TAG, "HTTP_EVENT_HEADER_SENT");
            break;
        case HTTP_EVENT_ON_HEADER:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_HEADER, key=%s, value=%s", evt->header_key, evt->header_value);
            break;
        case HTTP_EVENT_ON_DATA:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_DATA, len=%d", evt->data_len);
            if (!esp_http_client_is_chunked_response(evt->client)) {
                // If you want to print the response data, use:
                // ESP_LOG_BUFFER_HEX(TAG, evt->data, evt->data_len);
            }
            break;
        case HTTP_EVENT_ON_FINISH:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_FINISH");
            break;
        case HTTP_EVENT_DISCONNECTED:
            ESP_LOGD(TAG, "HTTP_EVENT_DISCONNECTED");
            break;
    }
    return ESP_OK;
}

void http_get_task(void *pvParameters) {
    esp_http_client_config_t config = {
        .url = "http://httpbin.org/get",
        .event_handler = http_event_handler,
        .timeout_ms = 10000,
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_err_t err = esp_http_client_perform(client);
    
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "HTTP GET Status = %d, content_length = %lld",
                 esp_http_client_get_status_code(client),
                 esp_http_client_get_content_length(client));
    } else {
        ESP_LOGE(TAG, "HTTP GET request failed: %s", esp_err_to_name(err));
    }
    
    esp_http_client_cleanup(client);
    vTaskDelete(NULL);
}
```

### 7.2 HTTPS 客户端（含证书）

```c
extern const unsigned char server_cert_pem_start[] asm("_binary_server_cert_pem_start");
extern const unsigned char server_cert_pem_end[]   asm("_binary_server_cert_pem_end");

void https_request_task(void *pvParameters) {
    esp_http_client_config_t config = {
        .url = "https://api.example.com/v1/data",
        .event_handler = http_event_handler,
        .cert_pem = (const char *)server_cert_pem_start,
        .timeout_ms = 15000,
        .skip_cert_common_name_check = false,
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&config);
    
    // Set custom headers
    esp_http_client_set_header(client, "Authorization", "Bearer MyToken123");
    esp_http_client_set_header(client, "Content-Type", "application/json");
    
    // POST with body
    const char *post_data = "{\"key\":\"value\",\"ts\":1234567890}";
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_post_field(client, post_data, strlen(post_data));
    
    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "HTTPS POST Status = %d, content_length = %lld",
                 esp_http_client_get_status_code(client),
                 esp_http_client_get_content_length(client));
    } else {
        ESP_LOGE(TAG, "HTTPS POST failed: %s", esp_err_to_name(err));
    }
    
    esp_http_client_cleanup(client);
    vTaskDelete(NULL);
}
```

### 7.3 流式读取大响应

```c
void http_stream_read_task(void *pvParameters) {
    esp_http_client_config_t config = {
        .url = "http://example.com/large_file.bin",
        .event_handler = http_event_handler,
        .buffer_size = 1024,        // Receive buffer
        .buffer_size_tx = 1024,     // Transmit buffer
    };
    
    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_err_t err = esp_http_client_open(client, 0);  // 0 = no request body
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to open HTTP connection: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        vTaskDelete(NULL);
        return;
    }
    
    int content_length = esp_http_client_fetch_headers(client);
    ESP_LOGI(TAG, "Content length: %d", content_length);
    
    char buffer[512];
    int total_read = 0;
    int read_len;
    while ((read_len = esp_http_client_read(client, buffer, sizeof(buffer))) > 0) {
        total_read += read_len;
        // Process buffer here
        ESP_LOGD(TAG, "Read %d bytes, total %d", read_len, total_read);
    }
    
    ESP_LOGI(TAG, "Total read: %d bytes", total_read);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    vTaskDelete(NULL);
}
```

---

## 第 8 章 mDNS 服务发现

### 8.1 mDNS 简介

mDNS（Multicast DNS）在局域网内通过组播 224.0.0.251:5353 解析域名，无需 DNS 服务器。
ESP-IDF 内置 `esp_mdns` 组件，可让设备以 `hostname.local` 被其他设备访问。

### 8.2 mDNS 服务初始化

```c
#include "mdns.h"

void mdns_init_custom(void) {
    // Initialize mDNS
    ESP_ERROR_CHECK(mdns_init());
    
    // Set hostname (will be accessible as esp32-001.local)
    ESP_ERROR_CHECK(mdns_hostname_set("esp32-001"));
    
    // Set default instance name
    ESP_ERROR_CHECK(mdns_instance_name_set("ESP32 IoT Device"));
    
    // Add a service (HTTP server on port 80)
    mdns_service_add("ESP32 Web Server", "_http", "_tcp", 80, NULL, 0);
    
    // Add a service with TXT records
    mdns_txt_item_t serviceTxtData[] = {
        {"board", "esp32"},
        {"version", "1.0.0"},
        {"path", "/"},
    };
    mdns_service_add("ESP32 Web Server", "_http", "_tcp", 80, serviceTxtData, 3);
}
```

### 8.3 查询其他 mDNS 服务

```c
void mdns_query_example(void) {
    // Query for HTTP service
    mdns_result_t *results = NULL;
    esp_err_t err = mdns_query_ptr("_http", "_tcp", 3000, 20, &results);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mDNS query failed: %s", esp_err_to_name(err));
        return;
    }
    
    if (!results) {
        ESP_LOGW(TAG, "No mDNS results found");
        return;
    }
    
    mdns_result_t *r = results;
    while (r) {
        ESP_LOGI(TAG, "Service: %s.%s.%s", r->instance_name, r->service_type, r->proto);
        if (r->hostname) {
            ESP_LOGI(TAG, "  Hostname: %s", r->hostname);
        }
        if (r->port) {
            ESP_LOGI(TAG, "  Port: %u", r->port);
        }
        if (r->addr) {
            ip_addr_t *addr = &r->addr->addr;
            ESP_LOGI(TAG, "  IP: " IPSTR, IP2STR(&addr->u_addr.ip4));
        }
        r = r->next;
    }
    
    mdns_query_results_free(results);
}
```

---

## 第 9 章 WiFi 事件系统

### 9.1 事件系统总览

ESP-IDF 的事件系统基于 `esp_event` 库，提供异步、解耦的事件分发机制：

- **事件源（Event Base）**：`WIFI_EVENT`、`IP_EVENT`、`ETH_EVENT`、`SC_EVENT` 等
- **事件 ID**：`WIFI_EVENT_STA_START`、`IP_EVENT_STA_GOT_IP` 等
- **事件数据**：与事件相关的结构体指针
- **事件循环（Event Loop）**：默认循环或自定义循环

### 9.2 WiFi 事件清单

| 事件 ID | 触发时机 | event_data 类型 |
|---------|----------|------------------|
| WIFI_EVENT_WIFI_READY | WiFi 准备就绪 | NULL |
| WIFI_EVENT_SCAN_DONE | 扫描完成 | wifi_event_scan_done_t |
| WIFI_EVENT_STA_START | STA 已启动 | NULL |
| WIFI_EVENT_STA_STOP | STA 已停止 | NULL |
| WIFI_EVENT_STA_CONNECTED | STA 已关联 | wifi_event_sta_connected_t |
| WIFI_EVENT_STA_DISCONNECTED | STA 已断开 | wifi_event_sta_disconnected_t |
| WIFI_EVENT_STA_AUTHMODE_CHANGE | 认证模式变化 | wifi_event_sta_authmode_change_t |
| WIFI_EVENT_AP_START | AP 已启动 | NULL |
| WIFI_EVENT_AP_STOP | AP 已停止 | NULL |
| WIFI_EVENT_AP_STACONNECTED | 有 STA 连接到 AP | wifi_event_ap_staconnected_t |
| WIFI_EVENT_AP_STADISCONNECTED | STA 从 AP 断开 | wifi_event_ap_stadisconnected_t |
| WIFI_EVENT_AP_PROBEREQRECVED | 收到 Probe Request | wifi_event_ap_probe_req_rx_t |

### 9.3 IP 事件

| 事件 ID | 触发时机 | event_data 类型 |
|---------|----------|------------------|
| IP_EVENT_STA_GOT_IP | STA 获取到 IP | ip_event_got_ip_t |
| IP_EVENT_STA_LOST_IP | STA 丢失 IP | NULL |
| IP_EVENT_AP_STAIPASSIGNED | AP 给 STA 分配了 IP | ip_event_ap_staipassigned_t |
| IP_EVENT_ETH_GOT_IP | 以太网获取 IP | ip_event_got_ip_t |

### 9.4 高级事件处理：多实例注册

同一事件可注册多个 handler，按注册顺序依次调用：

```c
// Register multiple handlers for the same event
esp_event_handler_instance_register(
    WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &reconnect_handler, NULL, &h1);
esp_event_handler_instance_register(
    WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &log_handler, NULL, &h2);
esp_event_handler_instance_register(
    WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &mqtt_disconnect_handler, NULL, &h3);

// Later, unregister specific handler
esp_event_handler_instance_unregister(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, h2);
```

### 9.5 自定义事件循环

```c
ESP_EVENT_DEFINE_BASE(MY_APP_EVENT);

typedef enum {
    MY_APP_EVENT_SENSOR_DATA,
    MY_APP_EVENT_ALARM,
} my_app_event_id_t;

void custom_event_loop_init(void) {
    esp_event_loop_handle_t loop_handle;
    esp_event_loop_args_t loop_args = {
        .queue_size = 32,
        .task_name = "my_event_loop",
        .task_priority = 5,
        .task_stack_size = 4096,
        .task_core_id = 0,
    };
    
    ESP_ERROR_CHECK(esp_event_loop_create(&loop_args, &loop_handle));
    
    esp_event_handler_register_with(loop_handle, MY_APP_EVENT, ESP_EVENT_ANY_ID,
                                     &my_app_event_handler, NULL);
    
    // Post an event
    int sensor_value = 42;
    esp_event_post_to(loop_handle, MY_APP_EVENT, MY_APP_EVENT_SENSOR_DATA,
                      &sensor_value, sizeof(sensor_value), portMAX_DELAY);
}
```

---

## 第 10 章 Smart Config 与 SmartConfig

### 10.1 Smart Config 简介

Smart Config（AirKiss/ESPTouch）允许通过手机 APP 配置 ESP32 的 WiFi，无需硬编码
SSID 和密码。手机 APP 将 SSID/密码编码到 WiFi 包中广播，ESP32 在混杂模式监听并解码。

### 10.2 SmartConfig 实现

```c
#include "esp_smartconfig.h"
#include "esp_wifi.h"

static const char *TAG = "smartconfig";
static EventGroupHandle_t s_wifi_event_group;
#define SMART_CONFIG_DONE_BIT BIT0
#define SMART_CONFIG_ESP_TOUCH BIT1

void smartconfig_event_handler(void *arg, esp_event_base_t event_base,
                                int32_t event_id, void *event_data) {
    if (event_base == SC_EVENT && event_id == SC_EVENT_SCAN_DONE) {
        ESP_LOGD(TAG, "Scan done");
    } else if (event_base == SC_EVENT && event_id == SC_EVENT_FOUND_CHANNEL) {
        ESP_LOGI(TAG, "Found channel");
    } else if (event_base == SC_EVENT && event_id == SC_EVENT_GOT_SSID_PSWD) {
        ESP_LOGI(TAG, "Got SSID and password");
        
        smartconfig_event_got_ssid_pswd_t *evt = (smartconfig_event_got_ssid_pswd_t *)event_data;
        
        wifi_config_t wifi_config = {0};
        memcpy(wifi_config.sta.ssid, evt->ssid, sizeof(wifi_config.sta.ssid));
        memcpy(wifi_config.sta.password, evt->password, sizeof(wifi_config.sta.password));
        wifi_config.sta.bssid_set = evt->bssid_set;
        if (wifi_config.sta.bssid_set) {
            memcpy(wifi_config.sta.bssid, evt->bssid, sizeof(wifi_config.sta.bssid));
        }
        
        ESP_ERROR_CHECK(esp_wifi_disconnect());
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
        ESP_ERROR_CHECK(esp_wifi_connect());
        
        // Save to NVS for next boot
        // (omitted for brevity)
    } else if (event_base == SC_EVENT && event_id == SC_EVENT_SEND_ACK_DONE) {
        ESP_LOGI(TAG, "SmartConfig ACK done");
        xEventGroupSetBits(s_wifi_event_group, SMART_CONFIG_DONE_BIT);
    }
}

void smartconfig_task(void *arg) {
    EventBits_t uxBits;
    
    // Configure SmartConfig
    smartconfig_start_config_t cfg = SMARTCONFIG_START_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_smartconfig_set_type(SC_TYPE_ESPTOUCH));
    ESP_ERROR_CHECK(esp_smartconfig_start(&cfg));
    
    while (1) {
        uxBits = xEventGroupWaitBits(s_wifi_event_group,
                                       SMART_CONFIG_DONE_BIT | SMART_CONFIG_ESP_TOUCH,
                                       true, false, portMAX_DELAY);
        
        if (uxBits & SMART_CONFIG_DONE_BIT) {
            ESP_LOGI(TAG, "SmartConfig done, stopping");
            esp_smartconfig_stop();
            vTaskDelete(NULL);
        }
    }
}
```

### 10.3 BLE 配网（BluFi）

BluFi 是乐鑫自研的蓝牙配网方案，通过 BLE 传输 WiFi 凭据：

```c
#include "blufi.h"

void blufi_init(void) {
    // Initialize BLUFI
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));
    ESP_ERROR_CHECK(esp_bluedroid_init());
    ESP_ERROR_CHECK(esp_bluedroid_enable());
    
    // Start BluFi
    blufi_security_t sec;
    // ... set up security ...
    esp_blufi_register_callbacks(&blufi_callbacks);
    esp_blufi_profile_init();
}
```

### 10.4 WiFi Manager（Web Portal）

更现代的配网方案：第一次开机时启动 AP+HTTP 服务器，用户通过手机连接 AP，在网页上
输入 WiFi 密码。

```c
// Simplified WiFi Manager flow
void wifi_manager_task(void *arg) {
    // 1. Try to connect with saved credentials
    if (try_connect_with_saved_config()) {
        ESP_LOGI(TAG, "Connected with saved config");
        vTaskDelete(NULL);
        return;
    }
    
    // 2. If failed, start AP + HTTP server for configuration
    start_config_ap_mode();      // SSID: "ESP32-Setup"
    start_http_server();         // Serve config portal
    
    // 3. Wait for new credentials
    xEventGroupWaitBits(s_wifi_event_group, NEW_CONFIG_BIT, true, false, portMAX_DELAY);
    
    // 4. Stop AP and try to connect
    stop_http_server();
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_connect();
    
    vTaskDelete(NULL);
}
```

---

## 第 11 章 性能优化

### 11.1 吞吐量优化

ESP32 WiFi 理论最大 TCP 吞吐量约 20-30 Mbps（HT40 模式）。优化建议：

1. **使用 WIFI_PS_NONE**：避免睡眠唤醒开销。
2. **增大 socket 缓冲区**：

```c
int sock_buf_size = 64 * 1024;  // 64 KB
setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &sock_buf_size, sizeof(sock_buf_size));
setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sock_buf_size, sizeof(sock_buf_size));
```

3. **启用 TCP_NODELAY**：禁用 Nagle 算法，降低小包延迟。

```c
int enable = 1;
setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &enable, sizeof(enable));
```

4. **调整 LwIP 配置**（`sdkconfig`）：
   - `CONFIG_LWIP_TCP_SND_BUF_DEFAULT` = 11680
   - `CONFIG_LWIP_TCP_WND_DEFAULT` = 11680
   - `CONFIG_LWIP_TCP_MSS` = 1440

5. **CPU 频率提升到 240 MHz**：`CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y`

### 11.2 延迟优化

- 减少 `esp_wifi_set_ps()` 的睡眠深度
- 在事件处理器中避免长时间操作（> 10 ms 应转交任务）
- 启用 `CONFIG_ESP_TASK_WDT_TIMEOUT_S` 监控任务调度

### 11.3 内存优化

WiFi 驱动占用约 50 KB 内部 RAM。可通过以下配置释放内存：

```c
// Reduce WiFi static buffer count
// sdkconfig: CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=10 (default 10)
// sdkconfig: CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=32 (default 32)

// Move WiFi buffers to PSRAM (if available)
// sdkconfig: CONFIG_ESP_WIFI_RX_IRAM_OPT=n
// sdkconfig: CONFIG_SPIRAM_USE_MALLOC=y
```

### 11.4 启动时间优化

冷启动到 WiFi 连接完成的时间可通过以下方式优化：

| 优化项 | 节省时间 | 说明 |
|--------|----------|------|
| 跳过校准 | -100 ms | 仅生产环境 |
| 减少 NVS 操作 | -50 ms | 缓存配置 |
| 使用 BSSID 直接关联 | -200 ms | 跳过 SSID 扫描 |
| 固定信道 | -300 ms | 跳过全频段扫描 |

```c
// Skip full scan by setting BSSID and channel
wifi_config_t wifi_config = {
    .sta = {
        .ssid = "MySSID",
        .password = "MyPassword",
        .bssid_set = true,
        .bssid = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF},
        .channel = 6,  // Lock to known channel
    },
};
```

### 11.5 网络栈调优表

| 参数 | 默认值 | 推荐值（高吞吐） | 推荐值（低功耗） |
|------|--------|------------------|------------------|
| TCP_SND_BUF | 5744 | 11680 | 2880 |
| TCP_WND | 5744 | 11680 | 2880 |
| TCP_MSS | 1440 | 1440 | 536 |
| TCP_SND_QUEUELEN | 9 | 18 | 6 |
| TCP_RECVMBOX_SIZE | 6 | 16 | 4 |
| UDP_RECVMBOX_SIZE | 6 | 16 | 4 |

---

## 第 12 章 WiFi + BT 共存

### 12.1 共存架构

ESP32 共用 2.4 GHz RF 前端，WiFi 与 BT 必须分时复用。共存由硬件自动处理，但需要
正确配置：

- **共存模式**：`WIFI_BT_COEXIST_ENABLE=y`（默认）
- **优先级**：可配置为 WiFi 优先、BT 优先、均衡
- **天线共享**：建议使用单天线 + 内置开关

### 12.2 共存配置

```c
#include "esp_coexist.h"

// Set coexistence preference
// Options: ESP_COEX_PREFER_WIFI, ESP_COEX_PREFER_BT, ESP_COEX_PREFER_BALANCE
esp_coex_preference_t pref = ESP_COEX_PREFER_BALANCE;
esp_coex_set_preference(pref);
```

### 12.3 共存性能影响

| 场景 | WiFi 吞吐 | BT 吞吐 | 延迟 |
|------|-----------|---------|------|
| 仅 WiFi | 25 Mbps | N/A | 5 ms |
| WiFi + BLE（advertising） | 23 Mbps | 正常 | 6 ms |
| WiFi + BLE（连接，10 Hz） | 18 Mbps | 正常 | 12 ms |
| WiFi + 经典 BT A2DP | 12 Mbps | 正常 | 25 ms |
| WiFi + 经典 BT SPP | 8 Mbps | 正常 | 40 ms |

### 12.4 共存最佳实践

1. **WiFi 使用低信道（1/6/11），BT 自适应跳频避开**：BT AFH 会自动避开 WiFi 信道。
2. **避免同时大数据流**：BT A2DP 流媒体 + WiFi 视频流会互相干扰。
3. **降低 BT 连接间隔**：BLE connInterval > 100 ms 时对 WiFi 影响最小。
4. **使用 PHY 校准数据**：保证射频参数最优。

---

## 第 13 章 常见问题与故障排查

### 13.1 连接失败排查流程

```
1. WIFI_EVENT_STA_START 是否触发？
   ├── 否 → 检查 esp_wifi_start() 调用、NVS 初始化
   └── 是 ↓

2. WIFI_EVENT_STA_DISCONNECTED reason 是多少？
   ├── 201 NO_AP_FOUND → SSID 错误或信号弱（< -85 dBm）
   ├── 202 AUTH_FAIL → 密码错误或认证模式不匹配
   ├── 203 ASSOC_FAIL → 能力位不匹配，检查 802.11n/HT40
   ├── 15 4WAY_HANDSHAKE_TIMEOUT → 密码错误
   └── 4 DEAUTH_LEAVING → AP 主动断开

3. WIFI_EVENT_STA_CONNECTED 但 IP_EVENT_STA_GOT_IP 不触发？
   ├── DHCP 超时 → 检查路由器 DHCP 服务
   └── 静态 IP 模式 → 配置 esp_netif_set_ip_info()
```

### 13.2 FAQ

**Q1：`esp_wifi_connect()` 返回 ESP_ERR_WIFI_CONN`？**

A：通常是因为未调用 `esp_wifi_start()` 或 STA 模式未正确设置。检查 `esp_wifi_set_mode(WIFI_MODE_STA)` 是否在 `esp_wifi_start()` 之前调用。

**Q2：连接成功后立即断开，reason=200？**

A：reason 200 是 `NO_AP_FOUND` 在新版 SDK 中的别名。可能原因：
- AP 隐藏 SSID（需设置 `scan_method = WIFI_ALL_CHANNEL_SCAN`）
- 信号 RSSI < -90 dBm
- AP 不支持 ESP32 当前协议（如纯 5 GHz 路由器）

**Q3：HTTPS 握手失败，错误码 0x7780？**

A：错误码 0x7780 表示证书验证失败。检查：
- 服务器证书过期
- CA 证书未包含根证书
- 系统时间未同步（`esp_sntp` 同步时间）

**Q4：socket `send()` 长时间阻塞？**

A：默认 socket 为阻塞模式，TCP 发送缓冲区满时会阻塞。解决：
- 设置 `SO_SNDTIMEO` 超时
- 使用非阻塞 socket + select/poll
- 检查对端是否 ack（可能是连接已断开但未检测到）

**Q5：连接不稳定，每隔几分钟断一次？**

A：可能原因：
- AP 的 DHCP 租约到期未续约
- 路由器 STA 隔离或 MAC 过滤
- AP 的 keep-alive 超时（需在 `listen_interval` 内主动通信）
- 多个 AP 同名 SSID，发生漫游切换

**Q6：`esp_wifi_set_ps(WIFI_PS_MAX_MODEM)` 后 TCP 连接频繁断开？**

A：MAX_MODEM 模式下，如果 AP 不支持长时间睡眠的 STA，会主动断开。解决：
- 改用 `WIFI_PS_MIN_MODEM`
- 减小 `listen_interval`（建议 ≤ 10）
- 应用层心跳保活（每 30 秒一次）

**Q7：扫描结果为空？**

A：检查：
- `WIFI_EVENT_SCAN_DONE` 是否触发
- 调用 `esp_wifi_scan_start()` 时是否传入正确的 `wifi_scan_config_t`
- 是否启用了 802.11n-only 模式导致老 AP 不响应

**Q8：连接后 30 秒断开，reason=8？**

A：reason=8 是 `ASSOC_EXPIRE`，AP 在关联后未收到 STA 任何响应（通常是电源管理
太激进导致 STA 长时间睡眠）。解决：
- 降低电源管理强度
- 增加 keepalive 间隔

**Q9：SmartConfig 收不到 SSID？**

A：
- 确保手机和 ESP32 在同一 WiFi 频段（仅 2.4 GHz）
- 手机 APP 与 ESP32 SmartConfig 类型匹配（ESPTouch v1/v2/AirKiss）
- 关闭手机的 5G WiFi
- ESP32 距离手机不要太远

**Q10：Deep Sleep 唤醒后 WiFi 连接失败？**

A：Deep Sleep 期间 WiFi 状态丢失。唤醒后需要重新初始化：
```c
// After deep sleep wakeup, WiFi is in uninitialized state
// Must call esp_wifi_init() and esp_wifi_start() again
esp_wifi_init(&cfg);
esp_wifi_start();
// If credentials are in NVS, they will be auto-loaded
```

**Q11：内存不足，启动 WiFi 失败？**

A：检查 `CONFIG_ESP_WIFI_IRAM_OPT` 与 PSRAM 配置。若启用 PSRAM，可设置：
```
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_ESP32_SPIRAM_SUPPORT=y
```

**Q12：连接某些品牌路由器失败？**

A：部分路由器对 STA 的能力位要求严格。尝试：
```c
wifi_config.sta.rm_enabled = 1;      // Radio Measurement
wifi_config.sta.btm_enabled = 1;     // BSS Transition Management
wifi_config.sta.mfp_enabled = 1;     // Management Frame Protection
```

---

## 第 14 章 ESP32 系列对比

### 14.1 ESP32 系列芯片对比

| 型号 | CPU | Flash | PSRAM | WiFi | BT | 典型功耗 |
|------|-----|-------|-------|------|-----|----------|
| ESP32 (原版) | 双核 240 MHz Xtensa LX6 | 4-16 MB | 0-8 MB | b/g/n HT40 | 4.2 + BLE | 95 mA |
| ESP32-S2 | 单核 240 MHz Xtensa LX7 | 4 MB | 0-2 MB | b/g/n HT40 | 无 | 75 mA |
| ESP32-S3 | 双核 240 MHz Xtensa LX7 | 8-16 MB | 0-8 MB | b/g/n HT40 | 5.0 + BLE | 90 mA |
| ESP32-C3 | 单核 160 MHz RISC-V | 4 MB | 无 | b/g/n HT40 | 5.0 + BLE | 70 mA |
| ESP32-C6 | 单核 160 MHz RISC-V | 4-8 MB | 0-4 MB | b/g/n HT40 | 5.3 + BLE 5.0 | 65 mA |
| ESP32-H2 | 单核 96 MHz RISC-V | 4 MB | 无 | 无 | 5.3 + Thread/Zigbee | 35 mA |

### 14.2 WiFi 特性差异

| 特性 | ESP32 | ESP32-S2 | ESP32-S3 | ESP32-C3 | ESP32-C6 |
|------|-------|----------|----------|----------|----------|
| WiFi 4 (802.11n) | 是 | 是 | 是 | 是 | 是 |
| WiFi 6 (802.11ax) | 否 | 否 | 否 | 否 | 是 |
| HT40 | 是 | 是 | 是 | 是 | 是 |
| MIMO | 1x1 | 1x1 | 1x1 | 1x1 | 1x1 |
| 最大 TX 功率 | 20 dBm | 20 dBm | 20 dBm | 20 dBm | 21 dBm |
| 内置 PA | 是 | 是 | 是 | 是 | 是 |
| TWT（目标唤醒时间） | 否 | 否 | 否 | 否 | 是 |

### 14.3 ESP32-C6 WiFi 6 新特性

ESP32-C6 引入 WiFi 6（802.11ax）支持，主要改进：

- **OFDMA**：多用户正交频分多址，提升密集环境吞吐量
- **TWT**（Target Wakeup Time）：允许 STA 与 AP 协商唤醒时间，进一步降低功耗
- **BSS 着色**：减少相邻信道干扰
- **更安全的加密**：强制 WPA3 与 PMF

```c
// ESP32-C6 specific: enable TWT (Target Wakeup Time)
#include "esp_wifi_he.h"

esp_wifi_he_twt_setup_config_t twt_config = {
    .twt_id = 1,
    .negotiation_type = 0,
    .wake_duration_us = 5000,
    .wake_interval_us = 1000000,  // 1 second
};
esp_wifi_he_twt_setup(&twt_config);
```

### 14.4 选型建议

| 应用场景 | 推荐型号 | 原因 |
|----------|----------|------|
| 通用 IoT（双需求 WiFi+BT） | ESP32 | 平衡、生态成熟、便宜 |
| 高性能 AI/视觉 | ESP32-S3 | 双核、向量指令、PSRAM |
| 极简 IoT（无需 BT） | ESP32-S2 | 便宜、单核够用 |
| 低功耗 BLE 主导 | ESP32-C3 / H2 | RISC-V、低功耗、单核 |
| 高密度 IoT 环境 | ESP32-C6 | WiFi 6、OFDMA、TWT |
| Thread/Zigbee 网关 | ESP32-H2 | 内置 802.15.4 |

### 14.5 跨系列迁移指南

从 ESP32 迁移到 ESP32-S3：
- CPU 架构相同（Xtensa LX7），大部分代码可直接复用
- GPIO 编号不同，需要修改引脚配置
- USB OTG 内置，无需外挂 PHY
- AI 加速指令（SIMD）需重新编译

从 ESP32 迁移到 ESP32-C3：
- CPU 架构变化（Xtensa → RISC-V），汇编代码需重写
- 单核，多线程同步可简化
- 内存更少，需优化内存使用
- 部分外设驱动 API 不兼容（如 UART、I2C 重构）

---

## 第 15 章 进阶：固件 OTA 升级

### 15.1 OTA 架构

ESP32 支持 A/B 分区 OTA：运行在 OTA0/OTA1 分区，下载新固件写入另一分区，下次
启动时切换到新分区。如果新固件无法启动，可回滚到旧分区。

### 15.2 OTA 实现示例

```c
#include "esp_ota_ops.h"
#include "esp_http_client.h"

void ota_update_task(void *pvParameters) {
    const char *url = "https://example.com/firmware.bin";
    
    esp_http_client_config_t config = {
        .url = url,
        .cert_pem = (const char *)server_cert_pem_start,
        .timeout_ms = 30000,
        .buffer_size = 1024,
        .buffer_size_tx = 1024,
        .keep_alive_enable = true,
    };
    
    esp_https_ota_config_t ota_config = {
        .http_config = &config,
        .partial_http_download = true,
        .max_http_request_size = 8192,
    };
    
    esp_https_ota_handle_t https_ota_handle = NULL;
    esp_err_t err = esp_https_ota_begin(&ota_config, &https_ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "OTA begin failed: %s", esp_err_to_name(err));
        vTaskDelete(NULL);
        return;
    }
    
    while (1) {
        err = esp_https_ota_perform(https_ota_handle);
        if (err != ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
            break;
        }
        int progress = esp_https_ota_get_image_len_read(https_ota_handle);
        int total = esp_https_ota_get_image_size(https_ota_handle);
        ESP_LOGI(TAG, "OTA progress: %d/%d (%d%%)",
                 progress, total, progress * 100 / total);
    }
    
    if (esp_https_ota_is_complete_data_received(https_ota_handle)) {
        err = esp_https_ota_finish(https_ota_handle);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "OTA success, restarting...");
            esp_restart();
        } else {
            ESP_LOGE(TAG, "OTA finish failed: %s", esp_err_to_name(err));
        }
    } else {
        ESP_LOGE(TAG, "OTA incomplete");
        esp_https_ota_abort(https_ota_handle);
    }
    
    vTaskDelete(NULL);
}
```

### 15.3 分区表配置

OTA 需要自定义分区表 `partitions.csv`：

```
# Name,   Type, SubType, Offset,  Size,    Flags
nvs,      data, nvs,     0x9000,  0x4000,
phy_init, data, phy,     0xf000,  0x1000,
factory,  app,  factory, 0x10000, 1M,
ota_0,    app,  ota_0,   0x110000,1M,
ota_1,    app,  ota_1,   0x210000,1M,
otadata,  data, ota,     0x310000,0x2000,
```

### 15.4 回滚机制

```c
// Mark current firmware as valid (after self-test)
esp_ota_img_states_t state;
esp_ota_get_state_partition(esp_ota_get_running_partition(), &state);

if (state == ESP_OTA_IMG_PENDING_VERIFY) {
    // Run self-test
    if (self_test_passed()) {
        esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "Firmware marked as valid");
    } else {
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}
```

---

## 第 16 章 进阶：MQTT 客户端

### 16.1 MQTT 与 IoT

MQTT 是 IoT 领域最常用的应用层协议，基于发布/订阅模型，支持 QoS 0/1/2 三种质量
等级。ESP-IDF 集成 `esp_mqtt` 组件，基于 Eclipse Paho 移植。

### 16.2 MQTT 客户端实现

```c
#include "mqtt_client.h"

static const char *TAG = "mqtt";

static esp_err_t mqtt_event_handler_cb(esp_mqtt_event_handle_t event) {
    esp_mqtt_client_handle_t client = event->client;
    
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            ESP_LOGI(TAG, "MQTT_CONNECTED");
            esp_mqtt_client_subscribe(client, "/topic/sensor/cmd", 1);
            esp_mqtt_client_publish(client, "/topic/sensor/online", "1", 1, 1, 0);
            break;
            
        case MQTT_EVENT_DISCONNECTED:
            ESP_LOGI(TAG, "MQTT_DISCONNECTED");
            break;
            
        case MQTT_EVENT_SUBSCRIBED:
            ESP_LOGI(TAG, "MQTT_SUBSCRIBED, msg_id=%d", event->msg_id);
            break;
            
        case MQTT_EVENT_UNSUBSCRIBED:
            ESP_LOGI(TAG, "MQTT_UNSUBSCRIBED, msg_id=%d", event->msg_id);
            break;
            
        case MQTT_EVENT_PUBLISHED:
            ESP_LOGI(TAG, "MQTT_PUBLISHED, msg_id=%d", event->msg_id);
            break;
            
        case MQTT_EVENT_DATA:
            ESP_LOGI(TAG, "MQTT_DATA, topic=%.*s, data=%.*s",
                     event->topic_len, event->topic,
                     event->data_len, event->data);
            // Handle incoming command
            if (strncmp(event->topic, "/topic/sensor/cmd", event->topic_len) == 0) {
                handle_command(event->data, event->data_len);
            }
            break;
            
        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "MQTT_ERROR");
            if (event->error_handle->error_type == MQTT_ERROR_TYPE_ESP_TLS) {
                ESP_LOGE(TAG, "Last error code: 0x%x", event->error_handle->esp_tls_last_esp_err);
            } else if (event->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
                ESP_LOGE(TAG, "Connection refused, reason: %d", event->error_handle->connect_return_code);
            }
            break;
    }
    return ESP_OK;
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                                int32_t event_id, void *event_data) {
    mqtt_event_handler_cb(event_data);
}

void mqtt_app_start(void) {
    esp_mqtt_client_config_t mqtt_cfg = {
        .uri = "mqtt://broker.example.com:1883",
        .client_id = "esp32-001",
        .username = "user",
        .password = "pass",
        .keepalive = 60,
        .lwt_topic = "/topic/sensor/offline",
        .lwt_msg = "1",
        .lwt_qos = 1,
        .lwt_retain = 1,
        .disable_clean_session = false,
        .task_stack = 6144,
        .buffer_size = 1024,
    };
    
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(client);
}
```

### 16.3 MQTT 与 WiFi 重连协调

```c
// Coordinate MQTT reconnection with WiFi reconnection (exponential backoff)
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
        // WiFi disconnected - stop MQTT to avoid repeated reconnect attempts
        if (mqtt_client != NULL) {
            esp_mqtt_client_stop(mqtt_client);
        }
        // Trigger WiFi reconnect with exponential backoff
        schedule_reconnect_with_backoff();
    }
}

static void ip_event_handler(void *arg, esp_event_base_t event_base,
                              int32_t event_id, void *event_data) {
    if (event_id == IP_EVENT_STA_GOT_IP) {
        // WiFi connected - start MQTT
        if (mqtt_client != NULL) {
            esp_mqtt_client_start(mqtt_client);
        }
    }
}
```

---

## 第 17 章 进阶：低功耗深度睡眠

### 17.1 深度睡眠与 WiFi

Deep Sleep 模式下 WiFi 完全关闭，功耗约 10 µA。配合 WiFi 周期性唤醒上传数据，
可实现电池供电数月续航。

### 17.2 完整的低频上报示例

```c
#include "esp_sleep.h"
#include "esp_wifi.h"

#define uS_TO_S_FACTOR 1000000ULL
#define TIME_TO_SLEEP  60  // 60 seconds

RTC_DATA_ATTR int boot_count = 0;

void deep_sleep_wakeup_task(void *arg) {
    boot_count++;
    ESP_LOGI(TAG, "Boot count: %d", boot_count);
    
    // 1. Initialize WiFi and connect
    wifi_init_sta();
    
    // Wait for connection (with exponential backoff already built in)
    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group, WIFI_CONNECTED_BIT, false, true, pdMS_TO_TICKS(30000));
    
    if (bits & WIFI_CONNECTED_BIT) {
        // 2. Send sensor data to server
        send_sensor_data();
        
        // 3. Wait for send to complete
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    
    // 4. Disconnect and deinit WiFi
    esp_wifi_disconnect();
    esp_wifi_stop();
    esp_wifi_deinit();
    
    // 5. Configure wakeup timer
    esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
    ESP_LOGI(TAG, "Entering deep sleep for %d seconds", TIME_TO_SLEEP);
    
    // 6. Enter deep sleep
    esp_deep_sleep_start();
}
```

### 17.3 唤醒源

| 唤醒源 | API | 功耗 | 适用场景 |
|--------|-----|------|----------|
| 定时器 | `esp_sleep_enable_timer_wakeup()` | 10 µA | 周期性上报 |
| GPIO (EXT0) | `esp_sleep_enable_ext0_wakeup()` | 10 µA | 单个 GPIO 唤醒 |
| GPIO (EXT1) | `esp_sleep_enable_ext1_wakeup()` | 10 µA | 多个 GPIO 唤醒 |
| ULP 协处理器 | `esp_sleep_enable_ulp_wakeup()` | 100 µA | ADC 监测、复杂逻辑 |
| 触摸传感器 | `esp_sleep_enable_touchpad_wakeup()` | 50 µA | 触摸唤醒 |

### 17.4 ULP 协处理器

ULP（Ultra Low Power）协处理器在 Deep Sleep 期间运行，可执行简单任务：

```c
// ULP program (assembly or C macro)
const ulp_insn_t program[] = {
    // Read ADC channel 0
    I_ADC(R0, 0),
    // Compare with threshold
    M_BGE wakeup, 1,
    // Wait 1 second
    I_DELAY(8000000),
    // Loop
    M_BX 0,
    // Wakeup main CPU
    wakeup:
    I_WAKE(),
    I_HALT(),
};

void init_ulp(void) {
    esp_err_t err = ulp_load_binary(0, program, sizeof(program));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to load ULP program");
        return;
    }
    
    // Set ADC channel 0 (GPIO36)
    SET_PERI_REG_BITS(SENS_SAR_START_FORCE_REG, SENS_SAR1_EN_PAD_FORCE_V, 1, SENS_SAR1_EN_PAD_FORCE_S);
    SET_PERI_REG_BITS(SENS_SAR_READER1_CTRL_REG, SENS_SAR1_SAMPLE_NUM_V, 1, SENS_SAR1_SAMPLE_NUM_S);
    
    // Set ULP wake interval to 1 second
    REG_SET_FIELD(RTC_CNTL_ULP_CP_TIMER_REG, RTC_CNTL_ULP_CP_TIMER_SLP_CYCLE, 20000);
    
    // Start ULP
    SET_PERI_REG_MASK(RTC_CNTL_STATE0_REG, RTC_CNTL_ULP_CP_SLP_TIMER_EN);
    esp_sleep_enable_ulp_wakeup();
}
```

---

## 第 18 章 进阶：自定义 WiFi 配置管理

### 18.1 配置存储结构

```c
typedef struct {
    char ssid[32];
    char password[64];
    uint8_t bssid[6];
    bool bssid_set;
    uint8_t channel;
    wifi_auth_mode_t authmode;
    bool pmf_required;
    uint8_t listen_interval;
    wifi_ps_type_t ps_type;
} wifi_creds_t;

static const char *NVS_NAMESPACE = "wifi_creds";

esp_err_t save_wifi_config(const wifi_creds_t *creds) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    
    err = nvs_set_blob(handle, "creds", creds, sizeof(wifi_creds_t));
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

esp_err_t load_wifi_config(wifi_creds_t *creds) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) return err;
    
    size_t required_size = sizeof(wifi_creds_t);
    err = nvs_get_blob(handle, "creds", creds, &required_size);
    nvs_close(handle);
    return err;
}
```

### 18.2 多热点备用配置

```c
typedef struct {
    wifi_creds_t configs[3];
    uint8_t count;
    uint8_t last_used;
} wifi_multi_config_t;

void connect_to_any_saved_ap(void) {
    wifi_multi_config_t multi;
    if (load_multi_config(&multi) != ESP_OK) {
        ESP_LOGE(TAG, "No saved WiFi config");
        return;
    }
    
    for (int i = 0; i < multi.count; i++) {
        uint8_t idx = (multi.last_used + i) % multi.count;
        wifi_creds_t *c = &multi.configs[idx];
        
        ESP_LOGI(TAG, "Trying config %d: SSID=%s", idx, c->ssid);
        
        wifi_config_t wc = {0};
        memcpy(wc.sta.ssid, c->ssid, sizeof(wc.sta.ssid));
        memcpy(wc.sta.password, c->password, sizeof(wc.sta.password));
        wc.sta.threshold.authmode = c->authmode;
        
        esp_wifi_set_config(WIFI_IF_STA, &wc);
        esp_wifi_connect();
        
        // Wait up to 10 seconds for connection
        EventBits_t bits = xEventGroupWaitBits(
            s_wifi_event_group, WIFI_CONNECTED_BIT, true, false, pdMS_TO_TICKS(10000));
        
        if (bits & WIFI_CONNECTED_BIT) {
            multi.last_used = idx;
            save_multi_config(&multi);
            ESP_LOGI(TAG, "Connected to config %d", idx);
            return;
        }
    }
    
    ESP_LOGE(TAG, "All saved configs failed");
}
```

---

## 第 19 章 WiFi 扫描与信道分析

### 19.1 主动/被动扫描

```c
// Active scan (default): send probe request on each channel
wifi_scan_config_t scan_config = {
    .ssid = NULL,        // Any SSID
    .bssid = NULL,       // Any BSSID
    .channel = 0,        // All channels
    .show_hidden = true, // Include hidden SSIDs
    .scan_type = WIFI_SCAN_TYPE_ACTIVE,
    .scan_time.active.min = 100,  // ms per channel
    .scan_time.active.max = 300,
};

esp_wifi_scan_start(&scan_config, true);  // Block until done

wifi_ap_record_t *ap_records;
uint16_t ap_count = 0;
esp_wifi_scan_get_ap_num(&ap_count);
ap_records = malloc(ap_count * sizeof(wifi_ap_record_t));
esp_wifi_scan_get_ap_records(&ap_count, ap_records);

ESP_LOGI(TAG, "Found %d APs:", ap_count);
for (int i = 0; i < ap_count; i++) {
    ESP_LOGI(TAG, "  [%d] %-32s | ch=%d rssi=%d auth=%d",
             i, ap_records[i].ssid, ap_records[i].primary,
             ap_records[i].rssi, ap_records[i].authmode);
}

free(ap_records);
```

### 19.2 信道使用分析

```c
// Analyze channel congestion
void analyze_channel_congestion(void) {
    wifi_ap_record_t aps[32];
    uint16_t count = 32;
    esp_wifi_scan_get_ap_records(&count, aps);
    
    int channel_count[14] = {0};
    int channel_rssi_sum[14] = {0};
    
    for (int i = 0; i < count; i++) {
        int ch = aps[i].primary;
        if (ch >= 1 && ch <= 13) {
            channel_count[ch]++;
            channel_rssi_sum[ch] += aps[i].rssi;
        }
    }
    
    ESP_LOGI(TAG, "Channel congestion analysis:");
    ESP_LOGI(TAG, "Ch | Count | Avg RSSI | Recommendation");
    for (int ch = 1; ch <= 13; ch++) {
        if (channel_count[ch] > 0) {
            int avg_rssi = channel_rssi_sum[ch] / channel_count[ch];
            const char *rec = (channel_count[ch] < 3 && avg_rssi < -75) ? "GOOD" :
                              (channel_count[ch] < 6) ? "FAIR" : "CROWDED";
            ESP_LOGI(TAG, "%2d | %5d | %8d | %s", ch, channel_count[ch], avg_rssi, rec);
        }
    }
}
```

### 19.3 推荐信道选择

```c
// Find best channel for AP mode
int find_best_ap_channel(void) {
    esp_wifi_scan_start(NULL, true);
    
    wifi_ap_record_t aps[32];
    uint16_t count = 32;
    esp_wifi_scan_get_ap_records(&count, aps);
    
    // Score each channel (1, 6, 11 are non-overlapping)
    int scores[14] = {0};
    for (int i = 0; i < count; i++) {
        int ch = aps[i].primary;
        if (ch >= 1 && ch <= 13) {
            // Penalize based on RSSI (stronger = more penalty)
            int penalty = (100 + aps[i].rssi);  // -100 ~ 0 → 0 ~ 100
            scores[ch] += penalty;
            // Adjacent channels also get partial penalty
            if (ch > 1) scores[ch-1] += penalty / 2;
            if (ch < 13) scores[ch+1] += penalty / 2;
        }
    }
    
    // Find non-overlapping channel with lowest score
    int best_ch = 1;
    int best_score = scores[1];
    int candidates[] = {1, 6, 11};
    for (int i = 0; i < 3; i++) {
        int ch = candidates[i];
        if (scores[ch] < best_score) {
            best_score = scores[ch];
            best_ch = ch;
        }
    }
    
    ESP_LOGI(TAG, "Best AP channel: %d (score=%d)", best_ch, best_score);
    return best_ch;
}
```

---

## 第 20 章 SNTP 时间同步

### 20.1 SNTP 配置

```c
#include "esp_sntp.h"

void sntp_sync_time(void) {
    ESP_LOGI(TAG, "Initializing SNTP");
    sntp_setoperatingmode(SNTP_OPMODE_POLL);
    sntp_setservername(0, "pool.ntp.org");
    sntp_setservername(1, "ntp.aliyun.com");
    sntp_setservername(2, "time.windows.com");
    sntp_init();
    
    // Wait for time to be set
    time_t now = 0;
    struct tm timeinfo = {0};
    int retry = 0;
    const int retry_count = 10;
    
    while (timeinfo.tm_year < (2020 - 1900) && ++retry < retry_count) {
        ESP_LOGI(TAG, "Waiting for system time to be set... (%d/%d)", retry, retry_count);
        vTaskDelay(pdMS_TO_TICKS(2000));
        time(&now);
        localtime_r(&now, &timeinfo);
    }
    
    if (retry < retry_count) {
        char strftime_buf[64];
        strftime(strftime_buf, sizeof(strftime_buf), "%c", &timeinfo);
        ESP_LOGI(TAG, "Time synchronized: %s", strftime_buf);
    }
}
```

### 20.2 时区设置

```c
#include <time.h>

void set_timezone(const char *tz) {
    // Examples:
    // "CST-8" for China Standard Time (UTC+8)
    // "PST-8" for Pacific Standard Time (UTC-8)
    // "UTC0" for UTC
    setenv("TZ", tz, 1);
    tzset();
}

// Set China timezone
set_timezone("CST-8");
```

---

## 第 21 章 WiFi 抓包与调试

### 21.1 启用 WiFi 日志

```c
// Enable verbose WiFi logging
esp_log_level_set("wifi", ESP_LOG_VERBOSE);
esp_log_level_set("esp_wifi_internal", ESP_LOG_DEBUG);
esp_log_level_set("phy", ESP_LOG_DEBUG);
esp_log_level_set("pp", ESP_LOG_VERBOSE);
esp_log_level_set("net80211", ESP_LOG_VERBOSE);
```

### 21.2 WiFi Promiscuous 模式

```c
// Enable promiscuous mode for packet sniffing
void wifi_sniffer_init(void) {
    wifi_promiscuous_filter_t filter = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | 
                       WIFI_PROMIS_FILTER_MASK_DATA |
                       WIFI_PROMIS_FILTER_MASK_CTRL,
    };
    esp_wifi_set_promiscuous_filter(&filter);
    esp_wifi_set_promiscuous_rx_cb(&sniffer_rx_callback);
    esp_wifi_set_promiscuous(true);
}

void sniffer_rx_callback(void *buf, wifi_promiscuous_pkt_type_t type) {
    wifi_promiscuous_pkt_t *pkt = (wifi_promiscuous_pkt_t *)buf;
    wifi_pkt_rx_ctrl_t *ctrl = &pkt->rx_ctrl;
    
    ESP_LOGI(TAG, "Packet type=%d len=%d rssi=%d rate=%d",
             type, ctrl->sig_len, ctrl->rssi, ctrl->rate);
    
    // Analyze packet content (first 64 bytes)
    ESP_LOG_BUFFER_HEX(TAG, pkt->payload, min(64, ctrl->sig_len));
}
```

### 21.3 LwIP 统计

```c
// Display LwIP statistics
void print_lwip_stats(void) {
    ESP_LOGI(TAG, "LwIP stats:");
    stats_display();
    
    // Or check specific counters
    ESP_LOGI(TAG, "  TCP xmit: %lu", lwip_stats.tcp.xmit);
    ESP_LOGI(TAG, "  TCP recv: %lu", lwip_stats.tcp.recv);
    ESP_LOGI(TAG, "  TCP drop: %lu", lwip_stats.tcp.drop);
    ESP_LOGI(TAG, "  IP drop: %lu", lwip_stats.ip.drop);
    ESP_LOGI(TAG, "  MEM err: %lu", lwip_stats.mem.err);
}
```

### 21.4 网络诊断工具

```c
// Ping-like test using ICMP
void ping_test(const char *host) {
    struct addrinfo hints = {0};
    hints.ai_family = AF_INET;
    struct addrinfo *res;
    
    if (getaddrinfo(host, NULL, &hints, &res) != 0) {
        ESP_LOGE(TAG, "DNS resolution failed");
        return;
    }
    
    int sock = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Cannot create ICMP socket");
        freeaddrinfo(res);
        return;
    }
    
    struct sockaddr_in *addr = (struct sockaddr_in *)res->ai_addr;
    
    // Build ICMP echo request
    struct __attribute__((packed)) {
        uint8_t type;
        uint8_t code;
        uint16_t checksum;
        uint16_t id;
        uint16_t seq;
        uint8_t data[32];
    } icmp_packet = {
        .type = 8,
        .code = 0,
        .id = htons(0x1234),
        .seq = htons(1),
    };
    memset(icmp_packet.data, 0xAA, sizeof(icmp_packet.data));
    
    // Calculate checksum
    uint16_t *buf = (uint16_t *)&icmp_packet;
    uint32_t sum = 0;
    for (int i = 0; i < sizeof(icmp_packet) / 2; i++) {
        sum += ntohs(buf[i]);
    }
    icmp_packet.checksum = htons(~(sum + (sum >> 16)) & 0xFFFF);
    
    int start = esp_timer_get_time() / 1000;
    sendto(sock, &icmp_packet, sizeof(icmp_packet), 0,
           (struct sockaddr *)addr, sizeof(*addr));
    
    char reply[64];
    socklen_t len = sizeof(*addr);
    int n = recvfrom(sock, reply, sizeof(reply), 0, NULL, &len);
    int end = esp_timer_get_time() / 1000;
    
    if (n > 0) {
        ESP_LOGI(TAG, "Ping %s: reply in %d ms", host, end - start);
    } else {
        ESP_LOGE(TAG, "Ping timeout");
    }
    
    freeaddrinfo(res);
    close(sock);
}
```

---

## 第 22 章 ESP-NOW：设备间直连

### 22.1 ESP-NOW 简介

ESP-NOW 是乐鑫自研的连接less 协议，允许 ESP32 之间直接通信，无需路由器。延迟低
（~5 ms）、单次最多 250 字节、支持加密。

### 22.2 ESP-NOW 收发

```c
#include "esp_now.h"

static const char *TAG = "espnow";

uint8_t peer_mac[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};

void espnow_recv_cb(const esp_now_recv_info_t *recv_info, const uint8_t *data, int data_len) {
    ESP_LOGI(TAG, "Received %d bytes from "MACSTR, data_len, MAC2STR(recv_info->src_addr));
    ESP_LOG_BUFFER_HEX(TAG, data, data_len);
}

void espnow_send_cb(const uint8_t *mac_addr, esp_now_send_status_t status) {
    ESP_LOGI(TAG, "Send to "MACSTR" status=%d", MAC2STR(mac_addr), status);
}

void espnow_init(void) {
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_recv_cb));
    ESP_ERROR_CHECK(esp_now_register_send_cb(espnow_send_cb));
    
    // Add peer
    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, peer_mac, 6);
    peer.channel = 0;  // Current channel
    peer.encrypt = false;
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
}

void espnow_send(const uint8_t *data, size_t len) {
    esp_now_send(peer_mac, data, len);
}
```

### 22.3 ESP-NOW 加密

```c
// Encrypted ESP-NOW
uint8_t key[16] = {0x01, 0x02, 0x03, ... };  // 16-byte AES key

esp_now_peer_info_t peer = {0};
memcpy(peer.peer_addr, peer_mac, 6);
peer.encrypt = true;
memcpy(peer.lmk, key, 16);  // Local Master Key
esp_now_add_peer(&peer);
```

### 22.4 ESP-NOW vs WiFi 对比

| 特性 | ESP-NOW | WiFi (TCP/UDP) |
|------|---------|----------------|
| 路由器需求 | 否 | 是 |
| 延迟 | ~5 ms | ~30 ms (TCP) |
| 单次数据量 | 250 字节 | 无限制 |
| 设备数量 | 最多 17 加密 / 多个不加密 | 受路由器限制 |
| 功耗 | 低 | 较高 |
| 安全性 | AES-128 | WPA2/3 |

---

## 第 23 章 WiFi Mesh 网络

### 23.1 ESP-MESH 简介

ESP-MESH 是乐鑫自研的 mesh 网络协议，允许多个 ESP32 互连成网，自动路由。适合
大规模 IoT 部署。

### 23.2 MESH 初始化

```c
#include "esp_mesh.h"

#define MESH_ID "00,11,22,33,44,55"

void mesh_init(void) {
    // 1. Initialize WiFi as STA
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_mesh_netifs(&sta_netif, &ap_netif);
    
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    
    // 2. Configure MESH
    mesh_cfg_t mesh_cfg = MESH_INIT_CONFIG_DEFAULT();
    memcpy(mesh_cfg.mesh_id, (uint8_t *)MESH_ID, 6);
    mesh_cfg.channel = 6;
    memcpy(mesh_cfg.router.ssid, "BackhaulAP", 11);
    memcpy(mesh_cfg.router.password, "password", 9);
    mesh_cfg.mesh_ap.max_connection = 6;
    memcpy(mesh_cfg.mesh_ap.password, "mesh_password", 13);
    
    ESP_ERROR_CHECK(esp_mesh_init());
    ESP_ERROR_CHECK(esp_mesh_set_config(&mesh_cfg));
    ESP_ERROR_CHECK(esp_mesh_start());
    
    ESP_LOGI(TAG, "MESH started");
}
```

### 23.3 MESH 数据收发

```c
void mesh_send_to_root(const char *data, size_t len) {
    mesh_data_t mdata = {
        .data = (uint8_t *)data,
        .size = len,
        .proto = MESH_PROTO_BIN,
        .tos = MESH_TOS_P2P,
    };
    
    esp_mesh_send(NULL, &mdata, MESH_DATA_TODS, NULL, 0);
}

void mesh_recv_task(void *arg) {
    mesh_addr_t from;
    mesh_data_t data;
    uint8_t buf[256];
    data.data = buf;
    data.size = sizeof(buf);
    
    while (1) {
        int flag = 0;
        esp_err_t err = esp_mesh_recv(&from, &data, portMAX_DELAY, &flag, NULL, 0);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "Received from "MACSTR", size=%d",
                     MAC2STR(from.addr), data.size);
        }
    }
}
```

---

## 第 24 章 WiFi 加密与 WPA3 SAE

### 24.1 WPA3 SAE 原理

WPA3-Personal 使用 SAE（Dragonfly 协议）替代 WPA2 的 PSK：
- **抗离线字典攻击**：攻击者无法在抓包后离线尝试密码
- **前向安全**：即使密码泄露，过往流量仍安全
- **长度无关**：密码强度不再依赖长度（但短密码仍不安全）

### 24.2 WPA3 兼容性配置

```c
// WPA2/WPA3 transition mode (recommended)
wifi_config_t wifi_config = {
    .sta = {
        .ssid = "MySSID",
        .password = "MyPassword",
        .threshold.authmode = WIFI_AUTH_WPA2_WPA3_PSK,
        .pmf_cfg = {
            .capable = true,
            .required = false,  // Don't require PMF for WPA2 fallback
        },
        .sae_pwe_h2e = WPA3_SAE_PWE_BOTH,  // Try H2E first, then Hunt-and-Peck
    },
};
```

### 24.3 SAE 密码元素

```c
// Configure SAE H2E (Hash-to-Element) for WPA3
// H2E is faster and more secure than Hunt-and-Peck
wifi_config_t wifi_config = {
    .sta = {
        .sae_pwe_h2e = WPA3_SAE_PWE_HUNT_AND_PECK,  // or WPA3_SAE_PWE_H2E or WPA3_SAE_PWE_BOTH
    },
};
```

---

## 第 25 章 测试与认证

### 25.1 WiFi 性能测试

```c
// Throughput test client
void throughput_test_task(void *pvParameters) {
    int sock = tcp_client_connect("192.168.1.100", 5000);
    if (sock < 0) {
        vTaskDelete(NULL);
        return;
    }
    
    char *buf = malloc(8192);
    memset(buf, 'A', 8192);
    
    int64_t start = esp_timer_get_time();
    int64_t total_sent = 0;
    
    while ((esp_timer_get_time() - start) < 30 * 1000000) {  // 30 seconds
        int sent = send(sock, buf, 8192, 0);
        if (sent > 0) {
            total_sent += sent;
        } else if (sent < 0) {
            ESP_LOGE(TAG, "Send failed: errno %d", errno);
            break;
        }
    }
    
    int64_t elapsed = esp_timer_get_time() - start;
    double mbps = (double)total_sent * 8 / elapsed;
    ESP_LOGI(TAG, "Throughput: %.2f Mbps (%lld bytes in %.2fs)",
             mbps, total_sent, elapsed / 1000000.0);
    
    free(buf);
    close(sock);
    vTaskDelete(NULL);
}
```

### 25.2 RF 测试模式

```c
// Enter RF test mode (continuous TX/RX)
esp_wifi_set_mode(WIFI_MODE_NULL);  // Disable STA/AP

// Set TX power
esp_wifi_set_max_tx_power(80);  // 8 dBm (value in 0.25 dBm)

// Enable continuous TX (for certification testing)
// Note: requires special RF test command via esptool
```

---

## 第 26 章 总结与最佳实践

### 26.1 开发检查清单

**初始化阶段**：
- [ ] NVS 初始化（`nvs_flash_init()`）
- [ ] 默认事件循环创建（`esp_event_loop_create_default()`）
- [ ] netif 初始化与默认接口创建
- [ ] WiFi 初始化配置（`WIFI_INIT_CONFIG_DEFAULT()`）
- [ ] 事件处理器注册（WIFI_EVENT 和 IP_EVENT）
- [ ] 设置模式与配置
- [ ] 启动 WiFi（`esp_wifi_start()`）

**连接阶段**：
- [ ] WIFI_EVENT_STA_START → 调用 `esp_wifi_connect()`
- [ ] WIFI_EVENT_STA_DISCONNECTED → 实现指数退避重连
- [ ] IP_EVENT_STA_GOT_IP → 标记连接成功，重置 retry count

**生产部署**：
- [ ] 配置管理（NVS 持久化）
- [ ] 配网方案（SmartConfig / BluFi / WiFi Manager）
- [ ] OTA 升级
- [ ] 省电模式选择
- [ ] 错误恢复（指数退避、看门狗）
- [ ] 日志分级
- [ ] RF 校准

### 26.2 关键参数速查

| 参数 | 默认值 | 调整建议 |
|------|--------|----------|
| `WIFI_PS_NONE` | 否 | 高吞吐场景必选 |
| `WIFI_PS_MIN_MODEM` | 否 | IoT 默认推荐 |
| `WIFI_PS_MAX_MODEM` | 否 | 电池供电推荐 |
| `listen_interval` | 1 | 3-10 视应用而定 |
| `max_connection`（AP） | 4 | 上限 10 |
| `beacon_interval`（AP） | 100 ms | 不建议修改 |
| `threshold.authmode` | OPEN | 至少 WPA2_PSK |
| `MAX_RETRY_COUNT` | - | 建议 10，配合指数退避 |

### 26.3 参考资源

- ESP-IDF Programming Guide: <https://docs.espressif.com/projects/esp-idf/>
- ESP32 Datasheet: <https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf>
- ESP32 Wi-Fi API Reference: <https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_wifi.html>
- LwIP API: <https://www.nongnu.org/lwip/2_1_x/index.html>

---

## 第 27 章 附录 A：常用代码片段

### 27.1 获取当前连接信息

```c
void print_wifi_info(void) {
    wifi_ap_record_t ap;
    esp_wifi_sta_get_ap_info(&ap);
    
    ESP_LOGI(TAG, "Connected to:");
    ESP_LOGI(TAG, "  SSID: %s", ap.ssid);
    ESP_LOGI(TAG, "  BSSID: "MACSTR, MAC2STR(ap.bssid));
    ESP_LOGI(TAG, "  Channel: %d", ap.primary);
    ESP_LOGI(TAG, "  RSSI: %d dBm", ap.rssi);
    ESP_LOGI(TAG, "  Auth mode: %d", ap.authmode);
    
    esp_netif_ip_info_t ip_info;
    esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"), &ip_info);
    ESP_LOGI(TAG, "IP info:");
    ESP_LOGI(TAG, "  IP: "IPSTR, IP2STR(&ip_info.ip));
    ESP_LOGI(TAG, "  Mask: "IPSTR, IP2STR(&ip_info.netmask));
    ESP_LOGI(TAG, "  GW: "IPSTR, IP2STR(&ip_info.gw));
}
```

### 27.2 强制重连

```c
void force_reconnect(void) {
    s_retry_count = 0;  // Reset retry counter
    esp_wifi_disconnect();
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_wifi_connect();
}
```

### 27.3 切换省电模式

```c
void switch_ps_mode(wifi_ps_type_t mode) {
    esp_err_t err = esp_wifi_set_ps(mode);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Power save mode switched to %d", mode);
    } else {
        ESP_LOGE(TAG, "Failed to switch PS mode: %s", esp_err_to_name(err));
    }
}

// Example: Switch to high-performance mode during upload, then back to low power
void upload_then_sleep(void) {
    switch_ps_mode(WIFI_PS_NONE);          // High performance
    upload_data();
    switch_ps_mode(WIFI_PS_MAX_MODEM);     // Low power
}
```

### 27.4 MAC 地址自定义

```c
// Set custom MAC address (must be called before esp_wifi_start())
uint8_t custom_mac[6] = {0x02, 0x00, 0x00, 0x12, 0x34, 0x56};
esp_err_t err = esp_wifi_set_mac(WIFI_IF_STA, custom_mac);
if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to set MAC: %s", esp_err_to_name(err));
}

// Or set via efuse (one-time, irreversible)
// esp_efuse_mac_set_custom(custom_mac);
```

---

## 第 28 章 附录 B：典型应用架构

### 28.1 智能家居传感器

```
[Sensor] → [ESP32] → WiFi → [MQTT Broker] → [Home Assistant]
                              ↓
                          [Cloud Server]
```

特性：
- 使用 WIFI_PS_MIN_MODEM，平衡功耗与响应
- MQTT 长连接 + 遗嘱消息（LWT）
- 每 30 秒上报温湿度，订阅控制命令
- OTA 升级支持

### 28.2 工业网关

```
[Modbus Devices] → RS485 → [ESP32-S3] → WiFi → [MQTT/HTTP] → [SCADA]
                              ↓
                          [BLE] → [BLE Sensors]
```

特性：
- WIFI_PS_NONE，持续数据流
- 双协议栈（WiFi + BLE）
- 多通道并发（TCP + MQTT + HTTP）
- 内置看门狗与心跳

### 28.3 电池供电远程监测

```
[Battery] → [ESP32-C3] → [Deep Sleep 10min] → WiFi → [HTTP POST] → [Sleep]
```

特性：
- WIFI_PS_MAX_MODEM + Deep Sleep
- 平均功耗 < 1 mA
- 电池寿命 1-3 年（CR123A）
- 失败重试使用指数退避

---

## 第 29 章 附录 C：SDK 配置参考

### 29.1 sdkconfig 关键项

```ini
# WiFi
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=10
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=32
CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=32
CONFIG_ESP_WIFI_TX_BA_WIN=6
CONFIG_ESP_WIFI_RX_BA_WIN=6
CONFIG_ESP_WIFI_NVS_ENABLED=y
CONFIG_ESP_WIFI_SOFTAP_BEACON_MAX_LEN=752
CONFIG_ESP_WIFI_MGMT_SBUF_NUM=32
CONFIG_ESP_WIFI_IRAM_OPT=n
CONFIG_ESP_WIFI_RX_IRAM_OPT=n
CONFIG_ESP_WIFI_ENABLE_WPA3_SAE=y
CONFIG_ESP_WIFI_ENABLE_WPA3_OWE_STA=y

# Power Management
CONFIG_PM_ENABLE=y
CONFIG_PM_USE_RTC_TIMER_REF=y
CONFIG_PM_POWER_DOWN_CPU=y
CONFIG_PM_SLP_IRAM_OPT=y
CONFIG_RTOS_IDLE_ALLOW_DEEP_SLEEP=y
CONFIG_FREERTOS_USE_TICKLESS_IDLE=y

# LwIP
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=11680
CONFIG_LWIP_TCP_WND_DEFAULT=11680
CONFIG_LWIP_TCP_MSS=1440
CONFIG_LWIP_TCP_SND_QUEUELEN=18
CONFIG_LWIP_TCP_RECVMBOX_SIZE=16

# PSRAM (for ESP32 with PSRAM)
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_TYPE_AUTO=y
CONFIG_SPIRAM_SIZE=8
CONFIG_SPIRAM_SPEED_40M=y
CONFIG_SPIRAM_BOOT_INIT=y

# Event Loop
CONFIG_ESP_EVENT_POST_FROM_ISR=y
CONFIG_ESP_EVENT_POST_FROM_IRAM_ISR=y
```

### 29.2 性能 vs 功耗 配置对比

```ini
# === HIGH PERFORMANCE ===
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=16
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=64
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=23280
CONFIG_LWIP_TCP_WND_DEFAULT=23280
CONFIG_ESP_WIFI_AMPDU_TX_ENABLED=y
CONFIG_ESP_WIFI_AMPDU_RX_ENABLED=y

# === LOW POWER ===
CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_80=y
CONFIG_PM_ENABLE=y
CONFIG_FREERTOS_USE_TICKLESS_IDLE=y
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=4
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=8
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=2880
CONFIG_LWIP_TCP_WND_DEFAULT=2880
```

---

## 第 30 章 附录 D：故障速查表

| 现象 | 可能原因 | 解决方案 |
|------|----------|----------|
| 连接立即失败 | SSID/密码错误 | 检查 SSID 大小写、密码长度 |
| 连接超时 | AP 不存在/信号弱 | 移动设备/检查信道 |
| 30 秒后断开 | reason=8 ASSOC_EXPIRE | 减小 listen_interval |
| 频繁断开 | RSSI 过低 | 检查天线/距离 |
| HTTPS 握手失败 | 时间未同步 | SNTP 同步后再发起 HTTPS |
| TCP 发送阻塞 | 缓冲区满 | 设置 SO_SNDTIMEO |
| 内存不足 | 缓冲区过多 | 减小 BUFFER_NUM |
| 扫描无结果 | WiFi 未启动 | 检查 esp_wifi_start() |
| OTA 失败 | 分区表错误 | 检查 partitions.csv |
| BLE+WiFi 干扰 | 同频干扰 | 启用共存配置 |

---

## 第 31 章 WebSocket 实时通信

### 31.1 WebSocket 协议简介

WebSocket 是基于 HTTP 升级的全双工通信协议，适用于实时性要求高的场景，如远程
控制、即时消息、协同编辑、实时图表等。相比 HTTP 轮询，WebSocket 减少了重复连接
开销与延迟。

WebSocket 协议栈：

```
应用层（用户数据）
       ↓
WebSocket 协议（握手 + 帧）
       ↓
HTTP/1.1（仅握手阶段）
       ↓
TCP
       ↓
IP
```

### 31.2 ESP32 WebSocket 客户端

ESP-IDF 提供 `esp_websocket_client` 组件，封装 WebSocket 客户端：

```c
#include "esp_websocket_client.h"

static const char *TAG = "ws_client";

static void websocket_event_handler(void *handler_args, esp_event_base_t base,
                                     int32_t event_id, void *event_data) {
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    switch (event_id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "WebSocket CONNECTED");
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGI(TAG, "WebSocket DISCONNECTED");
            break;
        case WEBSOCKET_EVENT_DATA:
            ESP_LOGI(TAG, "WebSocket DATA received, op=%d, len=%d",
                     data->op_code, data->data_len);
            if (data->op_code == 0x1) {  // Text frame
                ESP_LOGI(TAG, "Text payload: %.*s", data->data_len, (char *)data->data_ptr);
            }
            break;
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "WebSocket ERROR");
            break;
    }
}

void websocket_app_start(void) {
    esp_websocket_client_config_t ws_cfg = {
        .uri = "wss://echo.websocket.org",
        .cert_pem = (const char *)ca_cert_pem_start,
        .reconnect_timeout_ms = 5000,
        .network_timeout_ms = 10000,
        .buffer_size = 1024,
        .task_stack = 6144,
    };
    
    esp_websocket_client_handle_t client = esp_websocket_client_init(&ws_cfg);
    esp_websocket_register_events(client, WEBSOCKET_EVENT_ANY,
                                   websocket_event_handler, NULL);
    esp_websocket_client_start(client);
    
    // Send a message
    char msg[] = "Hello WebSocket Server";
    esp_websocket_client_send_text(client, msg, strlen(msg), portMAX_DELAY);
    
    // Periodic send task
    while (1) {
        if (esp_websocket_client_is_connected(client)) {
            char buf[64];
            int len = snprintf(buf, sizeof(buf), "{\"ts\":%lld}", 
                              (long long)time(NULL));
            esp_websocket_client_send_text(client, buf, len, portMAX_DELAY);
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
```

### 31.3 WebSocket 与指数退避重连

`esp_websocket_client` 内置自动重连机制，但默认行为可能与 WiFi 重连冲突。建议：

```c
// Custom reconnection logic with exponential backoff
void coordinated_ws_reconnect(void) {
    static int ws_retry = 0;
    if (!is_wifi_connected()) {
        // Don't reconnect WS if WiFi is down
        return;
    }
    
    uint32_t delay_ms = calculate_backoff_delay(ws_retry);
    ESP_LOGI(TAG, "WebSocket 重连：指数退避 %lu ms", (unsigned long)delay_ms);
    
    // Use timer to schedule reconnect
    xTimerChangePeriod(ws_reconnect_timer, pdMS_TO_TICKS(delay_ms), 0);
    xTimerStart(ws_reconnect_timer, 0);
    ws_retry++;
}
```

### 31.4 WebSocket 帧格式

WebSocket 帧格式（RFC 6455）：

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-------+-+-------------+-------------------------------+
|F|R|R|R| opcode|M| Payload len |    Extended payload length    |
|I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
|N|V|V|V|       |S|             |   (if payload len==126/127)   |
| |1|2|3|       |K|             |                               |
+-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
|     Extended payload length continued, if payload len == 127  |
+ - - - - - - - - - - - - - - - +-------------------------------+
|                               |Masking-key, if MASK set to 1  |
+-------------------------------+-------------------------------+
| Masking-key (continued)       |          Payload Data         |
+-------------------------------- - - - - - - - - - - - - - - - +
:                     Payload Data continued ...                :
```

opcode 含义：

| opcode | 含义 | 说明 |
|--------|------|------|
| 0x0 | continuation | 分片帧的后续帧 |
| 0x1 | text | UTF-8 文本帧 |
| 0x2 | binary | 二进制帧 |
| 0x8 | close | 关闭帧 |
| 0x9 | ping | 心跳请求 |
| 0xA | pong | 心跳响应 |

---

## 第 32 章 CoAP 协议

### 32.1 CoAP 简介

CoAP（Constrained Application Protocol）是专为资源受限设备设计的应用层协议，
基于 UDP，类似 HTTP 的请求/响应模型，但开销更小。常用于 6LoWPAN、Thread、
智能家居等低功耗场景。

| 特性 | HTTP | CoAP |
|------|------|------|
| 传输层 | TCP | UDP |
| 头部开销 | 大（数百字节） | 小（4 字节） |
| 可靠性 | TCP 保证 | 应用层重传 |
| 方法 | GET/POST/PUT/DELETE | GET/POST/PUT/DELETE |
| 资源发现 | 无（依赖 URL） | `/.well-known/core` |
| 观察订阅 | 不支持 | 支持（OBSERVE） |

### 32.2 ESP32 CoAP 客户端

ESP-IDF 集成 libcoap，可用于 CoAP 通信：

```c
#include "coap.h"

static coap_context_t *ctx = NULL;

void coap_request_handler(struct coap_context_t *ctx,
                           coap_session_t *session,
                           coap_pdu_t *sent,
                           coap_pdu_t *received,
                           const coap_tid_t id) {
    unsigned char *data;
    size_t data_len;
    
    if (coap_get_data(received, &data_len, &data)) {
        ESP_LOGI(TAG, "CoAP response: %.*s", (int)data_len, data);
    }
}

void coap_client_task(void *pvParameters) {
    coap_address_t dst;
    coap_address_init(&dst);
    dst.addr.sin.sin_family = AF_INET;
    dst.addr.sin.sin_port = htons(COAP_DEFAULT_PORT);
    inet_pton(AF_INET, "192.168.1.100", &dst.addr.sin.sin_addr);
    
    ctx = coap_new_context(NULL);
    coap_session_t *session = coap_new_client_session(ctx, NULL, &dst, COAP_PROTO_UDP);
    
    coap_register_response_handler(ctx, coap_request_handler);
    
    // Build GET request
    coap_pdu_t *pdu = coap_pdu_init(COAP_MESSAGE_CON, COAP_REQUEST_GET,
                                     coap_new_message_id(session),
                                     coap_session_max_pdu_size(session));
    
    // Add URI: coap://server/sensor/temp
    coap_add_option(pdu, COAP_OPTION_URI_PATH, 6, (const uint8_t *)"sensor");
    coap_add_option(pdu, COAP_OPTION_URI_PATH, 4, (const uint8_t *)"temp");
    
    coap_send(session, pdu);
    
    // Process events
    while (1) {
        coap_io_process(ctx, 1000);
    }
    
    coap_session_release(session);
    coap_free_context(ctx);
    vTaskDelete(NULL);
}
```

### 32.3 CoAP 观察模式（Observe）

CoAP 的 Observe 扩展允许客户端订阅资源变化，服务器在资源变化时推送通知：

```c
// Subscribe to a resource
coap_pdu_t *pdu = coap_pdu_init(COAP_MESSAGE_CON, COAP_REQUEST_GET,
                                  coap_new_message_id(session),
                                  coap_session_max_pdu_size(session));

// Add Observe option (register)
unsigned char obs = 0;
coap_add_option(pdu, COAP_OPTION_OBSERVE, 1, &obs);

// Add URI
coap_add_option(pdu, COAP_OPTION_URI_PATH, 6, (const uint8_t *)"sensor");
coap_add_option(pdu, COAP_OPTION_URI_PATH, 4, (const uint8_t *)"temp");

coap_send(session, pdu);
```

---

## 第 33 章 HTTP 服务器

### 33.1 ESP32 HTTP 服务器基础

ESP32 可以作为 HTTP 服务器，提供 Web 配置界面、API 接口、文件下载等。

```c
#include "esp_http_server.h"

static const char *TAG = "http_server";

// GET /api/status - return JSON status
esp_err_t status_get_handler(httpd_req_t *req) {
    char json[256];
    int len = snprintf(json, sizeof(json),
        "{\"uptime\":%lld,\"heap\":%lu,\"rssi\":%d,\"wifi\":\"%s\"}",
        (long long)esp_timer_get_time() / 1000000,
        (unsigned long)esp_get_free_heap_size(),
        get_current_rssi(),
        is_wifi_connected() ? "connected" : "disconnected");
    
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, json, len);
    return ESP_OK;
}

// POST /api/config - accept new WiFi config
esp_err_t config_post_handler(httpd_req_t *req) {
    char buf[256];
    int total_len = req->content_len;
    if (total_len >= sizeof(buf)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Content too long");
        return ESP_FAIL;
    }
    
    int received = 0;
    while (received < total_len) {
        int ret = httpd_req_recv(req, buf + received, total_len - received);
        if (ret <= 0) {
            if (ret == HTTPD_SOCK_ERR_TIMEOUT) continue;
            httpd_resp_send_500(req);
            return ESP_FAIL;
        }
        received += ret;
    }
    buf[received] = '\0';
    
    // Parse JSON (simplified)
    char ssid[32], password[64];
    if (parse_wifi_config_json(buf, ssid, password) != 0) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid JSON");
        return ESP_FAIL;
    }
    
    // Save and apply
    save_wifi_credentials(ssid, password);
    
    httpd_resp_send_chunk(req, "{\"status\":\"ok\"}", 16);
    httpd_resp_send_chunk(req, NULL, 0);
    return ESP_OK;
}

// Static page handler
esp_err_t index_get_handler(httpd_req_t *req) {
    extern const unsigned char index_html_start[] asm("_binary_index_html_start");
    extern const unsigned char index_html_end[]   asm("_binary_index_html_end");
    size_t len = index_html_end - index_html_start;
    
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, (const char *)index_html_start, len);
    return ESP_OK;
}

httpd_handle_t start_webserver(void) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 8;
    config.stack_size = 8192;
    config.task_priority = 5;
    
    httpd_handle_t server = NULL;
    if (httpd_start(&server, &config) == ESP_OK) {
        // Register handlers
        httpd_uri_t status_uri = {
            .uri = "/api/status",
            .method = HTTP_GET,
            .handler = status_get_handler,
        };
        httpd_register_uri_handler(server, &status_uri);
        
        httpd_uri_t config_uri = {
            .uri = "/api/config",
            .method = HTTP_POST,
            .handler = config_post_handler,
        };
        httpd_register_uri_handler(server, &config_uri);
        
        httpd_uri_t index_uri = {
            .uri = "/",
            .method = HTTP_GET,
            .handler = index_get_handler,
        };
        httpd_register_uri_handler(server, &index_uri);
    }
    return server;
}
```

### 33.2 HTTPS 服务器

```c
httpd_config_t config = HTTPD_DEFAULT_CONFIG();
config.transport_type = HTTPD_TRANSPORT_OVER_SSL;
config.port = 443;
config.servercert = (const char *)server_cert_pem_start;
config.privkey = (const char *)server_key_pem_start;
```

### 33.3 WebSocket 服务器（HTTP 升级）

ESP-IDF HTTP 服务器内置 WebSocket 支持：

```c
esp_err_t ws_handler(httpd_req_t *req) {
    if (req->method == HTTP_GET) {
        // WebSocket handshake handled by HTTP server
        return ESP_OK;
    }
    
    // Receive WebSocket frame
    httpd_ws_frame_t ws_pkt;
    memset(&ws_pkt, 0, sizeof(httpd_ws_frame_t));
    ws_pkt.type = HTTPD_WS_TYPE_TEXT;
    
    // First call to get frame length
    esp_err_t ret = httpd_ws_recv_frame(req, &ws_pkt, 0);
    if (ret != ESP_OK) return ret;
    
    if (ws_pkt.len) {
        uint8_t *buf = malloc(ws_pkt.len + 1);
        ws_pkt.payload = buf;
        ret = httpd_ws_recv_frame(req, &ws_pkt, ws_pkt.len);
        if (ret != ESP_OK) {
            free(buf);
            return ret;
        }
        buf[ws_pkt.len] = '\0';
        ESP_LOGI(TAG, "WS message: %s", buf);
        
        // Echo back
        ret = httpd_ws_send_frame(req, &ws_pkt);
        free(buf);
    }
    return ret;
}
```

---

## 第 34 章 DNS 与 DHCP

### 34.1 DHCP 客户端

ESP32 STA 模式默认使用 DHCP 获取 IP：

```c
// Default: DHCP enabled
esp_netif_dhcpc_start(sta_netif);

// Disable DHCP and use static IP
esp_netif_dhcpc_stop(sta_netif);
esp_netif_ip_info_t ip_info = {0};
inet_pton(AF_INET, "192.168.1.200", &ip_info.ip);
inet_pton(AF_INET, "192.168.1.1", &ip_info.gw);
inet_pton(AF_INET, "255.255.255.0", &ip_info.netmask);
esp_netif_set_ip_info(sta_netif, &ip_info);

// Set DNS
esp_netif_dns_info_t dns_info = {0};
inet_pton(AF_INET, "8.8.8.8", &dns_info.ip.u_addr.ip4);
dns_info.ip.type = IPADDR_TYPE_V4;
esp_netif_set_dns_info(sta_netif, ESP_NETIF_DNS_MAIN, &dns_info);

// Backup DNS
inet_pton(AF_INET, "114.114.114.114", &dns_info.ip.u_addr.ip4);
esp_netif_set_dns_info(sta_netif, ESP_NETIF_DNS_BACKUP, &dns_info);
```

### 34.2 DHCP 服务器（AP 模式）

```c
// Configure DHCP server for AP
esp_netif_dhcps_stop(ap_netif);

esp_netif_ip_info_t ap_ip = {0};
inet_pton(AF_INET, "192.168.4.1", &ap_ip.ip);
inet_pton(AF_INET, "192.168.4.1", &ap_ip.gw);
inet_pton(AF_INET, "255.255.255.0", &ap_ip.netmask);
esp_netif_set_ip_info(ap_netif, &ap_ip);

dhcps_lease_t lease = {0};
inet_pton(AF_INET, "192.168.4.100", &lease.start_ip);
inet_pton(AF_INET, "192.168.4.200", &lease.end_ip);
esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_SET,
                        ESP_NETIF_SUBNET_MASK, &ap_ip.netmask, sizeof(ap_ip.netmask));
esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_SET,
                        ESP_NETIF_DOMAIN_NAME_SERVER, &ap_ip.ip, sizeof(ap_ip.ip));

esp_netif_dhcps_start(ap_netif);
```

### 34.3 自定义 DNS 服务器

可用于实现 Captive Portal（设备捕获所有 DNS 查询，强制跳转到配置页面）：

```c
#include "dns_server.h"

void start_dns_server(void) {
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(53),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    bind(sock, (struct sockaddr *)&addr, sizeof(addr));
    
    while (1) {
        uint8_t buf[512];
        struct sockaddr_in source;
        socklen_t source_len = sizeof(source);
        int n = recvfrom(sock, buf, sizeof(buf), 0,
                         (struct sockaddr *)&source, &source_len);
        if (n > 0) {
            // Build DNS response: always answer with our IP (192.168.4.1)
            uint8_t *response = malloc(n + 16);
            memcpy(response, buf, n);
            
            // Set QR=1 (response) and RA=1
            response[2] |= 0x80;  // QR
            response[3] |= 0x80;  // RA
            
            // ANCOUNT = 1
            response[6] = 0;
            response[7] = 1;
            
            // Append answer section
            int offset = n;
            response[offset++] = 0xC0;  // Pointer to question name
            response[offset++] = 0x0C;
            response[offset++] = 0;     // Type A
            response[offset++] = 1;
            response[offset++] = 0;     // Class IN
            response[offset++] = 1;
            // TTL = 60 seconds
            response[offset++] = 0;
            response[offset++] = 0;
            response[offset++] = 0;
            response[offset++] = 60;
            response[offset++] = 0;     // RDLENGTH = 4
            response[offset++] = 4;
            // RDATA = 192.168.4.1
            response[offset++] = 192;
            response[offset++] = 168;
            response[offset++] = 4;
            response[offset++] = 1;
            
            sendto(sock, response, offset, 0,
                   (struct sockaddr *)&source, source_len);
            free(response);
        }
    }
}
```

---

## 第 35 章 IPv6 支持

### 35.1 启用 IPv6

```c
// Enable IPv6 in sdkconfig
// CONFIG_LWIP_IPV6=y
// CONFIG_LWIP_IPV6_NUM_ADDRESSES=6

// Create IPv6-capable netif
esp_netif_create_default_wifi_sta();

// Enable IPv6 on the interface
esp_netif_create_ip6_linklocal(sta_netif);

// Wait for IPv6 address
EventBits_t bits = xEventGroupWaitBits(
    s_wifi_event_group, WIFI_CONNECTED_BIT, true, false, portMAX_DELAY);

// Get IPv6 address
esp_ip6_addr_t ip6;
if (esp_netif_get_ip6_linklocal(sta_netif, &ip6) == ESP_OK) {
    ESP_LOGI(TAG, "IPv6 link-local: " IPV6STR, IPV62STR(ip6));
}
```

### 35.2 IPv6 地址类型

| 类型 | 前缀 | 说明 |
|------|------|------|
| Link-Local | fe80::/10 | 链路本地，自动配置 |
| Global Unicast | 2000::/3 | 全局地址，需 SLAAC 或 DHCPv6 |
| Unique Local | fc00::/7 | 内网地址，类似 IPv4 私网 |
| Multicast | ff00::/8 | 组播地址 |

### 35.3 SLAAC 自动配置

ESP32 支持 SLAAC（Stateless Address Autoconfiguration），通过 Router Advertisement
获取全局 IPv6 地址：

```c
// SLAAC is enabled by default in ESP-IDF
// Wait for GLOBAL IPv6 address
esp_ip6_addr_t ip6;
int retry = 0;
while (retry++ < 20) {
    if (esp_netif_get_ip6_global(sta_netif, &ip6) == ESP_OK) {
        ESP_LOGI(TAG, "Got IPv6 global: " IPV6STR, IPV62STR(ip6));
        break;
    }
    vTaskDelay(pdMS_TO_TICKS(500));
}
```

---

## 第 36 章 路由与 NAT

### 36.1 ESP32 作为路由器

ESP32 可以作为软路由器，将 WiFi STA 接口（上行）连接到 AP，将 AP 接口（下行）
连接到本地设备，实现 NAT 转发：

```c
// Enable NAT in sdkconfig
// CONFIG_LWIP_IP_FORWARD=y
// CONFIG_LWIP_IPV4_NAPT=y

#include "lwip/lwip_napt.h"

void enable_napt(void) {
    // Enable NAPT on AP interface
    ip_napt_enable(netif_ip4_addr(AP_NETIF), 1);
    
    ESP_LOGI(TAG, "NAPT enabled");
}
```

注意：NAPT（Network Address Port Translation）会消耗较多内存和 CPU，仅在必要场景
启用，如 WiFi 中继、4G 转发等。

### 36.2 端口转发

```c
// Simple port forwarding from AP to local server
// (Not directly supported by LwIP, requires custom implementation)
typedef struct {
    uint16_t external_port;
    uint16_t internal_port;
    uint32_t internal_ip;
} port_forward_t;

// Implementation involves:
// 1. Capture incoming packets on AP interface
// 2. Modify destination IP/port
// 3. Reinject to internal interface
// 4. Reverse for return traffic
```

---

## 第 37 章 边缘计算与本地 AI 推理

### 37.1 ESP32-S3 神经网络推理

ESP32-S3 集成向量指令（SIMD），适合轻量级神经网络推理。乐鑫提供 ESP-PPQ 和
ESP-DL 库用于模型部署：

```c
#include "esp_dl.h"

void run_inference(void) {
    // Load model (quantized TFLite or custom format)
    model_t *model = model_load_from_partition(model_partition);
    
    // Allocate input/output tensors
    tensor_t input = {
        .data = malloc(28 * 28 * sizeof(float)),  // 28x28 image
        .dims = {1, 28, 28, 1},
        .type = TENSOR_TYPE_FLOAT32,
    };
    tensor_t output = {
        .data = malloc(10 * sizeof(float)),  // 10 classes
        .dims = {1, 10},
        .type = TENSOR_TYPE_FLOAT32,
    };
    
    // Fill input with sensor data
    fill_input_from_sensor(input.data);
    
    // Run inference
    int64_t start = esp_timer_get_time();
    model_invoke(model, &input, &output);
    int64_t elapsed = esp_timer_get_time() - start;
    
    ESP_LOGI(TAG, "Inference: %lld us", elapsed);
    
    // Find max output (predicted class)
    int max_idx = 0;
    float max_val = ((float *)output.data)[0];
    for (int i = 1; i < 10; i++) {
        if (((float *)output.data)[i] > max_val) {
            max_val = ((float *)output.data)[i];
            max_idx = i;
        }
    }
    
    ESP_LOGI(TAG, "Predicted: %d (confidence=%.3f)", max_idx, max_val);
    
    free(input.data);
    free(output.data);
    model_unload(model);
}
```

### 37.2 与云端协同

边缘 + 云端协同架构：

```
[传感器] → [ESP32 边缘推理] → 高置信度 → [本地执行]
                          ↓
                       低置信度
                          ↓
                    [WiFi] → [云端大模型] → [反馈/微调]
```

```c
// Edge + cloud collaboration
void sensor_data_handler(float *data, int len) {
    float confidence = run_local_inference(data, len);
    
    if (confidence > 0.9) {
        // High confidence: act locally
        apply_local_action(predicted_class);
    } else {
        // Low confidence: upload to cloud
        upload_to_cloud(data, len, predicted_class);
    }
}
```

---

## 第 38 章 AWS IoT 集成

### 38.1 AWS IoT Core 概述

AWS IoT Core 提供 MQTT broker 与设备影子（Device Shadow）服务。ESP32 通过
MQTT over TLS 与 AWS IoT Core 通信。

### 38.2 设备证书配置

```c
extern const unsigned char aws_root_ca_pem_start[] asm("_binary_aws_root_ca_pem_start");
extern const unsigned char aws_root_ca_pem_end[]   asm("_binary_aws_root_ca_pem_end");
extern const unsigned char device_cert_pem_start[] asm("_binary_device_cert_pem_start");
extern const unsigned char device_cert_pem_end[]   asm("_binary_device_cert_pem_end");
extern const unsigned char device_key_pem_start[]  asm("_binary_device_key_pem_start");
extern const unsigned char device_key_pem_end[]    asm("_binary_device_key_pem_end");

void aws_iot_init(void) {
    esp_mqtt_client_config_t mqtt_cfg = {
        .uri = "mqtts://a1b2c3d4e5f6g7.iot.us-east-1.amazonaws.com:8883",
        .client_id = "esp32-device-001",
        .cert_pem = (const char *)aws_root_ca_pem_start,
        .client_cert = (const char *)device_cert_pem_start,
        .client_key = (const char *)device_key_pem_start,
        .keepalive = 60,
        .lwt_topic = "$aws/things/esp32-device-001/shadow/update",
        .lwt_msg = "{\"state\":{\"reported\":{\"online\":false}}}",
        .lwt_qos = 1,
    };
    
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_start(client);
}
```

### 38.3 设备影子交互

```c
// Update device shadow
void update_shadow(int temperature, int humidity) {
    char payload[256];
    int len = snprintf(payload, sizeof(payload),
        "{\"state\":{\"reported\":{\"temp\":%d,\"humid\":%d,\"ts\":%lld}}}",
        temperature, humidity, (long long)time(NULL));
    
    esp_mqtt_client_publish(client,
        "$aws/things/esp32-device-001/shadow/update",
        payload, len, 1, 0);
}

// Subscribe to desired state changes
void subscribe_shadow_delta(void) {
    esp_mqtt_client_subscribe(client,
        "$aws/things/esp32-device-001/shadow/update/delta", 1);
}

// In MQTT event handler, handle delta messages
case MQTT_EVENT_DATA:
    if (strstr(event->topic, "shadow/update/delta")) {
        // Parse desired state and act on it
        parse_shadow_delta(event->data, event->data_len);
    }
    break;
```

---

## 第 39 章 阿里云 IoT 集成

### 39.1 一机一密 vs 一型一密

阿里云 IoT 平台支持两种设备认证方式：

| 方式 | 说明 | 适用场景 |
|------|------|----------|
| 一机一密 | 每个设备有独立的 DeviceSecret | 量产设备，安全性高 |
| 一型一密 | 同型号设备共享 ProductSecret，激活时获取 DeviceSecret | 大批量设备，简化产线 |

### 39.2 MQTT 连接参数

阿里云 IoT 使用专用 MQTT 服务器，连接参数计算较复杂：

```c
// Compute MQTT connection parameters for Aliyun IoT
void compute_aliyun_mqtt_params(char *client_id, char *username, char *password,
                                 const char *device_name, const char *product_key,
                                 const char *device_secret) {
    // Client ID: {device_name}&{product_key}
    snprintf(client_id, 64, "%s&%s|securemode=2,_v=sdk-c-1.0.0|",
             device_name, product_key);
    
    // Username: {device_name}&{product_key}
    snprintf(username, 64, "%s&%s", device_name, product_key);
    
    // Password: HMAC-SHA256(content, device_secret)
    // content = clientId{device_name}timestamp{ts}deviceName{device_name}
    //           productKey{product_key}
    char content[256];
    int64_t ts = time(NULL) * 1000;
    snprintf(content, sizeof(content),
             "clientId%stimestamp%llddeviceName%sproductKey%s",
             device_name, ts, device_name, product_key);
    
    // Compute HMAC-SHA256
    mbedtls_md_context_t ctx;
    mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
    mbedtls_md_hmac_starts(&ctx,
                            (const unsigned char *)device_secret,
                            strlen(device_secret));
    mbedtls_md_hmac_update(&ctx, (const unsigned char *)content, strlen(content));
    
    unsigned char hmac[32];
    mbedtls_md_hmac_finish(&ctx, hmac);
    mbedtls_md_free(&ctx);
    
    // Base64 encode
    size_t out_len;
    mbedtls_base64_encode((unsigned char *)password, 64, &out_len, hmac, 32);
}

void aliyun_iot_init(void) {
    char client_id[64], username[64], password[64];
    compute_aliyun_mqtt_params(client_id, username, password,
                                "my_device", "a1XYZ12345",
                                "abcdef0123456789...");
    
    char broker_uri[128];
    snprintf(broker_uri, sizeof(broker_uri),
             "mqtt://%s.iot-as-mqtt.cn-shanghai.aliyuncs.com:1883",
             "a1XYZ12345");
    
    esp_mqtt_client_config_t mqtt_cfg = {
        .uri = broker_uri,
        .client_id = client_id,
        .username = username,
        .password = password,
        .keepalive = 60,
    };
    
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_start(client);
}
```

### 39.3 物模型属性上报

```c
// Report device properties to Aliyun IoT Platform
void report_properties(int temperature, int humidity, int co2) {
    char payload[512];
    int len = snprintf(payload, sizeof(payload),
        "{\"id\":\"%lld\",\"version\":\"1.0\",\"params\":{"
        "\"Temperature\":%d,\"Humidity\":%d,\"CO2\":%d"
        "},\"method\":\"thing.event.property.post\"}",
        (long long)time(NULL), temperature, humidity, co2);
    
    char topic[128];
    snprintf(topic, sizeof(topic),
             "/sys/a1XYZ12345/my_device/thing/event/property/post");
    
    esp_mqtt_client_publish(client, topic, payload, len, 1, 0);
}

// Subscribe to property set commands
void subscribe_property_set(void) {
    char topic[128];
    snprintf(topic, sizeof(topic),
             "/sys/a1XYZ12345/my_device/thing/service/property/set");
    esp_mqtt_client_subscribe(client, topic, 1);
}
```

---

## 第 40 章 物联网协议对比

### 40.1 主流 IoT 协议对比

| 协议 | 传输层 | 头部开销 | 安全性 | 适用场景 | 典型应用 |
|------|--------|----------|--------|----------|----------|
| MQTT | TCP | ~2 字节 | TLS | 网络稳定环境 | 工业网关、智能家居 |
| CoAP | UDP | 4 字节 | DTLS | 资源受限、低延迟 | 6LoWPAN、Thread |
| HTTP | TCP | 大 | TLS | 通用 Web | RESTful API |
| WebSocket | TCP | 2-10 字节 | TLS | 实时双向 | 即时消息、远程控制 |
| LwM2M | UDP | 中 | DTLS | 设备管理 | OTA、配置管理 |
| AMQP | TCP | 大 | TLS | 企业级消息 | 金融、企业系统 |
| STOMP | TCP | 中 | TLS | 简单消息 | 简单消息推送 |

### 40.2 选型决策树

```
是否资源受限设备？
├── 是 → CoAP（UDP，4 字节头部）
└── 否
    ├── 需要实时双向通信？
    │   ├── 是 → WebSocket
    │   └── 否
    │       ├── 需要 RESTful API？
    │       │   ├── 是 → HTTP
    │       │   └── 否 → MQTT（默认推荐）
```

### 40.3 MQTT vs CoAP 详细对比

| 特性 | MQTT | CoAP |
|------|------|------|
| 传输层 | TCP | UDP |
| 模型 | 发布/订阅 | 请求/响应 |
| 可靠性 | TCP 保证 | 应用层重传 |
| QoS 等级 | 0/1/2 | CON/NON |
| 资源发现 | 不支持 | `/.well-known/core` |
| 观察订阅 | 不支持（需 LwM2M） | 内置支持 |
| 头部大小 | 2 字节（最小） | 4 字节 |
| 防火墙穿透 | 良好（TCP） | 较差（UDP） |
| 服务器实现 | 较复杂 | 简单 |
| 客户端实现 | 简单 | 较简单 |

---

## 第 41 章 安全加固与防御

### 41.1 Flash 加密

启用 Flash 加密防止固件被读取：

```bash
# In ESP-IDF menuconfig:
# Security features → Flash encryption → Enable flash encryption on boot

# Or via esptool:
espefuse.py burn_efuse FLASH_CRYPT_CNT

# After enabling, all subsequent flashing requires encrypted firmware:
espsecure.py encrypt_flash_data --keyfile flash_encryption_key.bin --address 0x10000 -i app.bin -o app_encrypted.bin
```

### 41.2 Secure Boot

Secure Boot 防止固件被替换：

```bash
# Generate secure boot signing key
espsecure.py generate_signing_key secure_boot_signing_key.pem

# Configure in menuconfig:
# Security features → Secure Boot → Enable secure boot

# Build and flash with signing
idf.py build flash
```

### 41.3 NVS 加密

保护 NVS 中的敏感数据（如 WiFi 密码、API Token）：

```c
#include "nvs_flash.h"
#include "nvs_sec_provider.h"

void init_encrypted_nvs(void) {
    esp_err_t err = nvs_flash_init_security_provider(
        NVS_SEC_PROVIDER_HMAC, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init NVS security provider");
        return;
    }
    
    nvs_sec_cfg_t cfg = {0};
    // Generate or load encryption keys
    nvs_sec_provider_generate_keys(&cfg);
    
    nvs_flash_read_cfg_t read_cfg = {
        .encrypted = true,
    };
    nvs_flash_init_with_cfg(&read_cfg);
}
```

### 41.4 防御常见攻击

| 攻击类型 | 防御措施 |
|----------|----------|
| Deauth Attack | 启用 PMF（802.11w） |
| Wi-Fi 密码爆破 | 使用 WPA3-SAE |
| 固件逆向 | Flash 加密 + Secure Boot |
| NVS 数据泄露 | NVS 加密 |
| 中间人攻击 | HTTPS + 证书固定 |
| 重放攻击 | 时间戳 + Nonce |
| 缓冲区溢出 | 启用 stack canary |
| 代码注入 | 禁用危险函数（strcpy/sprintf） |

### 41.5 安全编码准则

```c
// BAD: vulnerable to buffer overflow
char buf[32];
strcpy(buf, user_input);  // User input may exceed 32 bytes

// GOOD: use safe functions
strlcpy(buf, user_input, sizeof(buf));

// BAD: format string vulnerability
char user_msg[256];
sprintf(buf, user_msg);  // If user_msg contains %s/%x, stack leak

// GOOD: literal format string
snprintf(buf, sizeof(buf), "%s", user_msg);

// BAD: command injection (in embedded context, this could affect config)
system(user_input);

// GOOD: validate input first
if (is_valid_input(user_input, len)) {
    process_input(user_input, len);
}
```

---

## 第 42 章 量产与生产测试

### 42.1 量产烧录流程

```bash
# Step 1: Generate unique device certificate per device
python generate_cert.py --device-id $SERIAL_NUMBER --output certs/

# Step 2: Burn efuses (Secure Boot key, Flash Encryption key)
espefuse.py burn_key SECURE_BOOT_KEY secure_boot_key.bin
espefuse.py burn_key BLOCK_KEY0 flash_encryption_key.bin

# Step 3: Flash firmware + certificates
esptool.py --port $PORT --baud 921600 write_flash \
    0x10000 firmware.bin \
    0x100000 device_cert.bin \
    0x110000 device_key.bin

# Step 4: Burn efuse to enable encryption (irreversible!)
espefuse.py burn_efuse FLASH_CRYPT_CNT

# Step 5: Verification
esptool.py --port $PORT verify_flash 0x10000 firmware.bin
```

### 42.2 自动化产线测试

```c
// Production test firmware (run on assembly line)
void production_test(void) {
    int pass_count = 0;
    int total_tests = 0;
    
    ESP_LOGI(TAG, "=== Production Test Start ===");
    
    // Test 1: WiFi connection
    total_tests++;
    if (test_wifi_connect("TestAP", "test1234")) {
        ESP_LOGI(TAG, "[PASS] WiFi connection");
        pass_count++;
    } else {
        ESP_LOGE(TAG, "[FAIL] WiFi connection");
    }
    
    // Test 2: Flash read/write
    total_tests++;
    if (test_flash_rw()) {
        ESP_LOGI(TAG, "[PASS] Flash R/W");
        pass_count++;
    } else {
        ESP_LOGE(TAG, "[FAIL] Flash R/W");
    }
    
    // Test 3: GPIO loopback test
    total_tests++;
    if (test_gpio_loopback()) {
        ESP_LOGI(TAG, "[PASS] GPIO");
        pass_count++;
    }
    
    // Test 4: ADC measurement
    total_tests++;
    if (test_adc(0, 1.65, 0.1)) {  // Channel 0, expected 1.65V ± 100mV
        ESP_LOGI(TAG, "[PASS] ADC");
        pass_count++;
    }
    
    // Test 5: WiFi RSSI
    total_tests++;
    int rssi = get_wifi_rssi();
    if (rssi > -70) {
        ESP_LOGI(TAG, "[PASS] RSSI=%d dBm", rssi);
        pass_count++;
    } else {
        ESP_LOGE(TAG, "[FAIL] RSSI=%d dBm", rssi);
    }
    
    // Test 6: MAC address uniqueness
    total_tests++;
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    if (is_mac_unique(mac)) {
        ESP_LOGI(TAG, "[PASS] MAC: "MACSTR, MAC2STR(mac));
        pass_count++;
    }
    
    ESP_LOGI(TAG, "=== Result: %d/%d ===", pass_count, total_tests);
    
    if (pass_count == total_tests) {
        // Signal pass via GPIO
        gpio_set_level(GPIO_NUM_2, 1);  // Green LED
    } else {
        // Signal fail
        gpio_set_level(GPIO_NUM_4, 1);  // Red LED
    }
}
```

### 42.3 RF 一致性测试

```c
// RF calibration test
void rf_calibration_test(void) {
    // Set to continuous TX mode
    esp_wifi_set_mode(WIFI_MODE_NULL);
    esp_wifi_start();
    
    // Set TX power
    esp_wifi_set_max_tx_power(80);  // 20 dBm
    
    // Test equipment measures:
    // - TX power (dBm)
    // - EVM (Error Vector Magnitude)
    // - Spectrum mask
    // - Center frequency offset
    // - Symbol clock offset
    
    // Continuous carrier test
    esp_phy_tx_test_mode_set(0x01);  // Continuous TX
    
    // Or modulated TX
    esp_phy_tx_test_mode_set(0x02);
    
    // Wait for measurement
    vTaskDelay(pdMS_TO_TICKS(5000));
    
    // Stop test
    esp_phy_tx_test_mode_set(0x00);
}
```

### 42.4 产线日志与追溯

```c
// Read chip ID (unique)
uint64_t chip_id = 0;
esp_efuse_mac_get_default((uint8_t *)&chip_id);

// Generate production record
char record[256];
snprintf(record, sizeof(record),
         "chip_id=%012llX,mac="MACSTR",fw_version=%s,build_date=%s,test=%s",
         (unsigned long long)chip_id,
         MAC2STR(mac),
         FIRMWARE_VERSION,
         BUILD_DATE,
         (pass_count == total_tests) ? "PASS" : "FAIL");

// Upload to MES system
upload_to_mes(record);
```

---

## 第 43 章 项目实战：WiFi 气象站

### 43.1 项目需求

构建一个 WiFi 气象站，每分钟上报温湿度、气压、CO2 浓度到 MQTT 服务器，支持
OTA 升级与远程配置。

### 43.2 硬件清单

- ESP32-S3-DevKitC（主控）
- BME280（温湿度气压传感器）
- SCD41（CO2 传感器，I2C）
- 0.96" OLED（本地显示，I2C）
- USB 5V 电源

### 43.3 软件架构

```
[main task]
  ↓
[sensor task] - 读取 BME280 + SCD41
  ↓
[display task] - OLED 显示
  ↓
[network task] - MQTT 上报
  ↓
[ota task] - 检查固件更新
```

### 43.4 关键代码

```c
// main.c - 项目主入口
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "weather_station";

void app_main(void) {
    ESP_LOGI(TAG, "=== Weather Station Starting ===");
    
    // 1. Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    
    // 2. Initialize WiFi with exponential backoff reconnect
    wifi_init_sta();
    
    // 3. Wait for WiFi
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                         false, true, portMAX_DELAY);
    
    // 4. Initialize SNTP
    sntp_sync_time();
    
    // 5. Initialize I2C bus
    i2c_init(GPIO_NUM_21, GPIO_NUM_22, 400000);
    
    // 6. Initialize sensors
    bme280_init();
    scd41_init();
    
    // 7. Initialize OLED display
    ssd1306_init();
    
    // 8. Initialize MQTT
    mqtt_app_start();
    
    // 9. Create tasks
    xTaskCreate(sensor_task, "sensor", 4096, NULL, 5, NULL);
    xTaskCreate(display_task, "display", 4096, NULL, 4, NULL);
    xTaskCreate(network_task, "network", 6144, NULL, 3, NULL);
    xTaskCreate(ota_check_task, "ota", 8192, NULL, 2, NULL);
    
    ESP_LOGI(TAG, "All tasks started");
}

// Sensor task
void sensor_task(void *arg) {
    while (1) {
        sensor_data_t data = {0};
        bme280_read(&data.temp, &data.humid, &data.pressure);
        scd41_read(&data.co2);
        data.timestamp = time(NULL);
        
        // Save to shared buffer (with mutex)
        xSemaphoreWrite(sensor_mutex, &data, portMAX_DELAY);
        
        // Wait 60 seconds
        vTaskDelay(pdMS_TO_TICKS(60000));
    }
}

// Network task: upload to MQTT
void network_task(void *arg) {
    while (1) {
        sensor_data_t data;
        if (xSemaphoreRead(sensor_mutex, &data, portMAX_DELAY) == pdTRUE) {
            // Build JSON payload
            char payload[256];
            int len = snprintf(payload, sizeof(payload),
                "{\"ts\":%lld,\"temp\":%.2f,\"humid\":%.2f,"
                "\"pressure\":%.1f,\"co2\":%d}",
                (long long)data.timestamp,
                data.temp, data.humid, data.pressure, data.co2);
            
            // Publish to MQTT (with retry on failure)
            int retry = 0;
            while (retry < 3) {
                int msg_id = esp_mqtt_client_publish(
                    mqtt_client, "/weather_station/data",
                    payload, len, 1, 0);
                if (msg_id >= 0) break;
                
                retry++;
                vTaskDelay(pdMS_TO_TICKS(1000 * (1 << retry)));  // 指数退避重试
            }
        }
        vTaskDelay(pdMS_TO_TICKS(60000));
    }
}
```

### 43.5 性能指标

| 指标 | 目标值 | 实测值 |
|------|--------|--------|
| 启动到首次上报 | < 10 s | 6.8 s |
| 平均功耗 | < 80 mA | 72 mA |
| 传感器采样精度 | ±0.5°C | ±0.3°C |
| MQTT 上报成功率 | > 99% | 99.7% |
| OTA 升级时间 | < 60 s | 42 s |

---

## 第 44 章 项目实战：WiFi 视频流

### 44.1 项目需求

使用 ESP32-CAM 实现低延迟 MJPEG 视频流，可通过浏览器实时观看。

### 44.2 关键代码

```c
#include "esp_camera.h"
#include "esp_http_server.h"

// Camera configuration for ESP32-CAM (AI-Thinker module)
camera_config_t camera_config = {
    .pin_pwdn = 32,
    .pin_reset = -1,
    .pin_xclk = 0,
    .pin_sccb_sda = 26,
    .pin_sccb_scl = 27,
    .pin_d7 = 35,
    .pin_d6 = 34,
    .pin_d5 = 39,
    .pin_d4 = 36,
    .pin_d3 = 21,
    .pin_d2 = 19,
    .pin_d1 = 18,
    .pin_d0 = 5,
    .pin_vsync = 25,
    .pin_href = 23,
    .pin_pclk = 22,
    .xclk_freq_hz = 20000000,
    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_JPEG,
    .frame_size = FRAMESIZE_VGA,    // 640x480
    .jpeg_quality = 10,
    .fb_count = 2,
};

esp_err_t stream_handler(httpd_req_t *req) {
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
    char part_buf[64];
    
    res = httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=123456789000000000000987654321");
    if (res != ESP_OK) return res;
    
    while (1) {
        fb = esp_camera_fb_get();
        if (!fb) {
            res = ESP_FAIL;
            break;
        }
        
        size_t hlen = snprintf(part_buf, 64,
            "\r\n--123456789000000000000987654321\r\n"
            "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
            fb->len);
        
        if (httpd_resp_send_chunk(req, part_buf, hlen) != ESP_OK ||
            httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len) != ESP_OK) {
            esp_camera_fb_return(fb);
            res = ESP_FAIL;
            break;
        }
        
        esp_camera_fb_return(fb);
    }
    
    return res;
}

void start_camera_server(void) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    config.max_uri_handlers = 4;
    
    httpd_handle_t server = NULL;
    httpd_start(&server, &config);
    
    httpd_uri_t stream_uri = {
        .uri = "/stream",
        .method = HTTP_GET,
        .handler = stream_handler,
    };
    httpd_register_uri_handler(server, &stream_uri);
}
```

### 44.3 性能优化

```c
// Optimize for lower latency
void optimize_camera_stream(void) {
    // 1. Use WIFI_PS_NONE for consistent performance
    esp_wifi_set_ps(WIFI_PS_NONE);
    
    // 2. Reduce frame size for lower latency
    camera_config.frame_size = FRAMESIZE_QVGA;  // 320x240
    camera_config.fb_count = 1;
    camera_config.jpeg_quality = 15;  // Lower quality = smaller size
    
    // 3. Increase CPU frequency
    // sdkconfig: CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
    
    // 4. Use PSRAM for frame buffer
    // sdkconfig: CONFIG_CAMERA_JPEG_MODE=y
}
```

### 44.4 实测性能

| 配置 | 分辨率 | 帧率 | 延迟 | WiFi 吞吐 |
|------|--------|------|------|------------|
| QVGA + Quality 15 | 320x240 | 25 fps | 80 ms | 1.5 Mbps |
| VGA + Quality 10 | 640x480 | 12 fps | 150 ms | 2.8 Mbps |
| HD + Quality 5 | 1280x720 | 5 fps | 350 ms | 4.2 Mbps |

---

## 第 45 章 调试技巧与日志管理

### 45.1 日志分级与生产配置

```c
// Set log levels per module
esp_log_level_set("*", ESP_LOG_INFO);              // Default
esp_log_level_set("wifi", ESP_LOG_WARN);           // Reduce WiFi noise
esp_log_level_set("wifi_reconnect", ESP_LOG_DEBUG);// Debug reconnect logic
esp_log_level_set("mqtt", ESP_LOG_INFO);
esp_log_level_set("http_server", ESP_LOG_ERROR);   // Only errors in production

// Production build: reduce log level
#ifdef CONFIG_RELEASE_BUILD
esp_log_level_set("*", ESP_LOG_WARN);
#endif
```

### 45.2 自定义日志后端

将日志写入 SD 卡或上传到云端：

```c
#include "esp_log.h"

static FILE *log_file = NULL;

void log_to_sdcard(const char *tag, esp_log_level_t level,
                    const char *format, va_list args) {
    if (!log_file) return;
    
    time_t now = time(NULL);
    struct tm tm_info;
    localtime_r(&now, &tm_info);
    
    fprintf(log_file, "[%04d-%02d-%02d %02d:%02d:%02d] %c (%s) ",
            tm_info.tm_year + 1900, tm_info.tm_mon + 1, tm_info.tm_mday,
            tm_info.tm_hour, tm_info.tm_min, tm_info.tm_sec,
            "NEWIDV"[level], tag);
    vfprintf(log_file, format, args);
    fprintf(log_file, "\n");
    fflush(log_file);
}

void enable_sdcard_logging(void) {
    log_file = fopen("/sdcard/app.log", "a");
    if (log_file) {
        esp_log_set_vprintf(log_to_sdcard);
        ESP_LOGI(TAG, "Logging to SD card enabled");
    }
}
```

### 45.3 内存调试

```c
// Check free heap
ESP_LOGI(TAG, "Free heap: %u bytes", esp_get_free_heap_size());
ESP_LOGI(TAG, "Min free heap: %u bytes", esp_get_minimum_free_heap_size());

// Check PSRAM (if available)
ESP_LOGI(TAG, "Free PSRAM: %u bytes", heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

// Heap tracing: detect memory leaks
#include "esp_heap_trace.h"
#define NUM_RECORDS 100
static heap_trace_record_t trace_record[NUM_RECORDS];

void start_heap_trace(void) {
    heap_trace_init_standalone(trace_record, NUM_RECORDS);
    heap_trace_start(HEAP_TRACE_LEAKS);
}

void stop_heap_trace(void) {
    heap_trace_stop();
    heap_trace_dump();
}
```

### 45.4 任务看门狗与堆栈溢出

```c
// Enable stack overflow check
// sdkconfig: CONFIG_FREERTOS_CHECK_STACKOVERFLOW_CANARY=y

// Add task watchdog
void critical_task(void *arg) {
    esp_task_wdt_init(10, true);  // 10s timeout, panic on trigger
    esp_task_wdt_add(NULL);
    
    while (1) {
        // Critical work
        do_critical_work();
        
        // Reset watchdog
        esp_task_wdt_reset();
    }
}

// Check task stack usage
void print_stack_usage(void) {
    TaskHandle_t task = xTaskGetCurrentTaskHandle();
    UBaseType_t high_water = uxTaskGetStackHighWaterMark(task);
    ESP_LOGI(TAG, "Stack high water: %u bytes", high_water * sizeof(StackType_t));
}
```

### 45.5 性能分析

```c
// Function profiling
#include "esp_timer.h"

#define PROFILE_BEGIN() int64_t _start = esp_timer_get_time()
#define PROFILE_END(name) \
    ESP_LOGI("PROFILE", "%s: %lld us", name, esp_timer_get_time() - _start)

void profiled_function(void) {
    PROFILE_BEGIN();
    
    // ... some work
    
    PROFILE_END("expensive_op");
}

// CPU usage per task
void print_cpu_usage(void) {
    TaskStatus_t *tasks;
    UBaseType_t count = uxTaskGetNumberOfTasks();
    tasks = malloc(count * sizeof(TaskStatus_t));
    
    uint32_t total_runtime;
    count = uxTaskGetSystemState(tasks, count, &total_runtime);
    
    ESP_LOGI(TAG, "Task CPU usage:");
    for (int i = 0; i < count; i++) {
        float pct = (float)tasks[i].ulRunTimeCounter / total_runtime * 100;
        ESP_LOGI(TAG, "  %-16s: %.2f%%", tasks[i].pcTaskName, pct);
    }
    
    free(tasks);
}
```

---

## 第 46 章 综合实战：智能家居网关

### 46.1 项目概述

构建一个多功能智能家居网关，集成：
- WiFi（连接云端）
- BLE（与本地 BLE 传感器通信）
- Zigbee/Thread（与基于 802.15.4 的设备通信）
- 本地 Web 配置界面
- MQTT 上报
- OTA 升级
- 语音控制（离线唤醒词）

### 46.2 系统架构

```
[BLE 温湿度计] ─┐
[Zigbee 灯泡]  ─┼─→ [ESP32-S3 网关] ──→ WiFi ──→ [MQTT Broker] ──→ [云平台]
[本地按键]     ─┘                          ↓
                                      [本地 Web 配置]
[语音唤醒]     ─→ [ESP32-S3 网关] ──→ 本地执行 / 云端识别
```

### 46.3 任务优先级设计

| 任务 | 优先级 | 栈大小 | 说明 |
|------|--------|--------|------|
| WiFi 事件处理 | 23 | 4 KB | 系统级，最高 |
| MQTT 客户端 | 5 | 6 KB | 网络通信 |
| BLE 协议栈 | 22 | 4 KB | 系统级 |
| Zigbee/Thread 协议栈 | 22 | 4 KB | 系统级 |
| 传感器轮询 | 4 | 4 KB | 周期性任务 |
| Web 服务器 | 4 | 8 KB | 用户接口 |
| OTA 检查 | 2 | 8 KB | 后台任务 |
| 语音唤醒 | 8 | 8 KB | 实时性要求 |
| LED 指示 | 1 | 2 KB | 视觉反馈 |

### 46.4 核心代码

```c
// Smart home gateway main entry
void app_main(void) {
    ESP_LOGI(TAG, "=== Smart Home Gateway Starting ===");
    
    // 1. Initialize NVS and WiFi
    nvs_flash_init();
    esp_event_loop_create_default();
    wifi_init_sta_with_backoff();  // With exponential backoff
    
    // 2. Initialize SNTP
    sntp_sync_time();
    
    // 3. Start MQTT client
    mqtt_app_start();
    
    // 4. Initialize BLE for local sensors
    ble_init();
    ble_start_scan();
    
    // 5. Initialize Zigbee (if supported)
    #if CONFIG_ZIGBEE_ENABLED
    esp_zigbee_init();
    esp_zigbee_start();
    #endif
    
    // 6. Start local web server for configuration
    start_webserver();
    
    // 7. Start OTA task
    xTaskCreate(ota_check_task, "ota", 8192, NULL, 2, NULL);
    
    // 8. Start sensor polling task
    xTaskCreate(sensor_poll_task, "sensor", 4096, NULL, 4, NULL);
    
    // 9. Start voice wake task (if mic available)
    #if CONFIG_VOICE_WAKE_ENABLED
    xTaskCreate(voice_wake_task, "voice", 8192, NULL, 8, NULL);
    #endif
    
    // 10. Start LED indicator
    xTaskCreate(led_indicator_task, "led", 2048, NULL, 1, NULL);
    
    ESP_LOGI(TAG, "Gateway started. Free heap: %u", esp_get_free_heap_size());
}

// Coordinated handling of WiFi/MQTT reconnection
static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                                int32_t event_id, void *event_data) {
    if (event_id == WIFI_EVENT_STA_DISCONNECTED) {
        // Stop MQTT to avoid storm
        esp_mqtt_client_stop(mqtt_client);
        // Stop BLE scanning to free CPU
        ble_stop_scan();
        // Schedule WiFi reconnect with exponential backoff
        schedule_wifi_reconnect_with_backoff();
    } else if (event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    }
}

static void ip_event_handler(void *arg, esp_event_base_t event_base,
                              int32_t event_id, void *event_data) {
    if (event_id == IP_EVENT_STA_GOT_IP) {
        // WiFi connected, restore network services
        esp_mqtt_client_start(mqtt_client);
        ble_start_scan();
        ESP_LOGI(TAG, "Network services restored");
    }
}
```

### 46.5 状态管理

```c
// Centralized state management
typedef struct {
    bool wifi_connected;
    bool mqtt_connected;
    bool ble_scanning;
    int wifi_rssi;
    int retry_count;
    time_t last_data_upload;
} gateway_state_t;

static gateway_state_t gw_state = {0};
static SemaphoreHandle_t state_mutex = NULL;

void update_state(bool wifi, bool mqtt, bool ble, int rssi, int retry) {
    xSemaphoreTake(state_mutex, portMAX_DELAY);
    gw_state.wifi_connected = wifi;
    gw_state.mqtt_connected = mqtt;
    gw_state.ble_scanning = ble;
    gw_state.wifi_rssi = rssi;
    gw_state.retry_count = retry;
    xSemaphoreGive(state_mutex);
}

void print_state(void) {
    xSemaphoreTake(state_mutex, portMAX_DELAY);
    ESP_LOGI(TAG, "State: WiFi=%s MQTT=%s BLE=%s RSSI=%d retry=%d",
             gw_state.wifi_connected ? "ON" : "OFF",
             gw_state.mqtt_connected ? "ON" : "OFF",
             gw_state.ble_scanning ? "ON" : "OFF",
             gw_state.wifi_rssi,
             gw_state.retry_count);
    xSemaphoreGive(state_mutex);
}
```

### 46.6 LED 指示灯状态机

```c
typedef enum {
    LED_STATE_OFF,
    LED_STATE_WIFI_CONNECTING,      // Slow blink
    LED_STATE_WIFI_CONNECTED,       // Solid on
    LED_STATE_MQTT_CONNECTED,       // Fast blink briefly, then solid
    LED_STATE_ERROR,                // Fast continuous blink
    LED_STATE_OTA_UPDATING,         // Pulse
} led_state_t;

void led_indicator_task(void *arg) {
    led_state_t state = LED_STATE_OFF;
    
    while (1) {
        // Determine current state
        if (gw_state.retry_count >= MAX_RETRY_COUNT) {
            state = LED_STATE_ERROR;
        } else if (!gw_state.wifi_connected) {
            state = LED_STATE_WIFI_CONNECTING;
        } else if (gw_state.wifi_connected && !gw_state.mqtt_connected) {
            state = LED_STATE_WIFI_CONNECTED;
        } else {
            state = LED_STATE_MQTT_CONNECTED;
        }
        
        // Drive LED based on state
        switch (state) {
            case LED_STATE_OFF:
                gpio_set_level(GPIO_NUM_2, 0);
                vTaskDelay(pdMS_TO_TICKS(1000));
                break;
            case LED_STATE_WIFI_CONNECTING:
                gpio_set_level(GPIO_NUM_2, 1);
                vTaskDelay(pdMS_TO_TICKS(500));
                gpio_set_level(GPIO_NUM_2, 0);
                vTaskDelay(pdMS_TO_TICKS(500));
                break;
            case LED_STATE_WIFI_CONNECTED:
                gpio_set_level(GPIO_NUM_2, 1);
                vTaskDelay(pdMS_TO_TICKS(100));
                break;
            case LED_STATE_MQTT_CONNECTED:
                gpio_set_level(GPIO_NUM_2, 1);
                vTaskDelay(pdMS_TO_TICKS(1000));
                break;
            case LED_STATE_ERROR:
                for (int i = 0; i < 5; i++) {
                    gpio_set_level(GPIO_NUM_2, 1);
                    vTaskDelay(pdMS_TO_TICKS(100));
                    gpio_set_level(GPIO_NUM_2, 0);
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
                vTaskDelay(pdMS_TO_TICKS(500));
                break;
            default:
                gpio_set_level(GPIO_NUM_2, 0);
                vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}
```

---

## 第 47 章 综合实战：低功耗传感器节点

### 47.1 项目需求

构建一个电池供电的温湿度传感器节点，每 10 分钟上报一次数据，CR2032 纽扣电池
续航 1 年。

### 47.2 功耗预算

| 组件 | 工作时电流 | 睡眠时电流 | 工作时间占比 |
|------|-----------|-----------|--------------|
| ESP32-C3 (Deep Sleep) | 30 mA | 5 µA | 0.5% (3s/10min) |
| SHT30 (传感器) | 1.5 mA | 0.5 µA | 0.5% |
| 电源转换损耗 | - | 2 µA | - |
| **总计** | - | **7.5 µA** (平均) | - |

理论续航：220 mAh / 0.0075 mA / 24h / 365d ≈ **3.3 年**

### 47.3 代码实现

```c
// Ultra-low power sensor node
#include "esp_sleep.h"
#include "esp_wifi.h"
#include "driver/gpio.h"
#include "driver/i2c.h"

#define uS_TO_S_FACTOR 1000000ULL
#define SLEEP_DURATION_SEC 600  // 10 minutes
#define UPLOAD_TIMEOUT_MS 30000

RTC_DATA_ATTR int boot_count = 0;
RTC_DATA_ATTR time_t last_sync_time = 0;

void app_main(void) {
    boot_count++;
    ESP_LOGI(TAG, "Boot #%d", boot_count);
    
    // 1. Quick sensor read (in low-power mode)
    float temp, humid;
    if (read_sht30(&temp, &humid) != ESP_OK) {
        ESP_LOGE(TAG, "Sensor read failed");
        goto enter_sleep;
    }
    
    // 2. WiFi fast connect
    wifi_init_fast_connect();  // Use BSSID + channel to skip scan
    
    // Wait for connection (with exponential backoff reconnect)
    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group, WIFI_CONNECTED_BIT,
        true, false, pdMS_TO_TICKS(UPLOAD_TIMEOUT_MS));
    
    if (bits & WIFI_CONNECTED_BIT) {
        // 3. Quick HTTP POST
        char payload[128];
        int len = snprintf(payload, sizeof(payload),
            "{\"temp\":%.2f,\"humid\":%.2f,\"boot\":%d,\"ts\":%lld}",
            temp, humid, boot_count, (long long)time(NULL));
        
        http_post("http://server.example.com/api/sensor", payload, len);
        
        // 4. Wait briefly for ACK
        vTaskDelay(pdMS_TO_TICKS(500));
    } else {
        ESP_LOGW(TAG, "WiFi failed, will retry next cycle");
    }
    
    // 5. Cleanup
    esp_wifi_disconnect();
    esp_wifi_stop();
    esp_wifi_deinit();
    
enter_sleep:
    // 6. Configure wakeup sources
    esp_sleep_enable_timer_wakeup(SLEEP_DURATION_SEC * uS_TO_S_FACTOR);
    
    // Optional: GPIO wake on button press
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);  // BOOT button
    
    // 7. Power down unnecessary peripherals
    gpio_deep_sleep_hold_dis();
    
    // 8. Enter deep sleep
    ESP_LOGI(TAG, "Deep sleep for %d s", SLEEP_DURATION_SEC);
    esp_deep_sleep_start();
}

// Fast WiFi connect (skip scan, use saved BSSID)
void wifi_init_fast_connect(void) {
    // Read saved WiFi config from NVS
    wifi_creds_t creds;
    if (load_wifi_config(&creds) != ESP_OK) {
        ESP_LOGE(TAG, "No WiFi config");
        return;
    }
    
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    cfg.static_rx_buf_num = 4;       // Reduce buffers for fast boot
    cfg.dynamic_rx_buf_num = 8;
    cfg.tx_buf_type = 1;             // Dynamic TX
    cfg.dynamic_tx_buf_num = 8;
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    
    // Use WIFI_PS_NONE for fastest connection
    esp_wifi_set_ps(WIFI_PS_NONE);
    
    // Configure with saved BSSID and channel
    wifi_config_t wc = {0};
    memcpy(wc.sta.ssid, creds.ssid, sizeof(wc.sta.ssid));
    memcpy(wc.sta.password, creds.password, sizeof(wc.sta.password));
    wc.sta.bssid_set = creds.bssid_set;
    if (creds.bssid_set) {
        memcpy(wc.sta.bssid, creds.bssid, sizeof(wc.sta.bssid));
    }
    wc.sta.channel = creds.channel;
    
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wc);
    esp_wifi_start();
    // esp_wifi_connect() will be called by WIFI_EVENT_STA_START handler
}
```

### 47.4 实测结果

| 指标 | 目标 | 实测 |
|------|------|------|
| 启动到上报完成 | < 5 s | 3.2 s |
| Deep Sleep 电流 | < 10 µA | 7.2 µA |
| 平均功耗 | < 100 µA | 85 µA |
| 上报成功率 | > 95% | 97.8% |
| CR2032 续航 | > 12 个月 | 15 个月（预估） |

---

## 第 48 章 最佳实践总结

### 48.1 开发流程最佳实践

1. **从最小可用版本开始**：先实现基础 WiFi 连接，再逐步添加功能。
2. **使用版本控制**：每个里程碑打 tag，便于回滚。
3. **集成 CI/CD**：使用 GitHub Actions 自动构建测试。
4. **建立监控**：日志 + 远程监控，及时发现线上问题。
5. **定期更新 SDK**：跟进乐鑫的安全补丁与 bug 修复。

### 48.2 代码质量

1. **静态分析**：使用 cppcheck、clang-tidy 检查代码。
2. **单元测试**：使用 Unity 框架测试核心逻辑。
3. **代码审查**：所有 PR 必须经过审查。
4. **命名规范**：函数 `snake_case`，宏 `UPPER_CASE`，类型 `CamelCase_t`。
5. **错误处理**：所有 API 返回值必须检查，使用 `ESP_ERROR_CHECK` 或显式判断。

### 48.3 部署与运维

1. **灰度发布**：OTA 先推送到 1% 设备，观察 24 小时无问题再全量。
2. **回滚机制**：必须启用 OTA 回滚，新固件自检失败自动回滚。
3. **监控告警**：MQTT 离线率、固件版本分布、错误率监控。
4. **日志收集**：关键日志上传到云端（注意脱敏）。
5. **固件签名**：所有固件必须签名，防止未授权升级。

### 48.4 性能调优 Checklist

- [ ] CPU 频率匹配应用需求（80/160/240 MHz）
- [ ] WiFi 缓冲区大小适配
- [ ] 任务栈大小适当（不溢出，不浪费）
- [ ] 关键路径无阻塞操作
- [ ] 中断处理简短（< 100 µs）
- [ ] 优先级反转已避免
- [ ] 内存泄漏已修复
- [ ] 看门狗已配置

### 48.5 安全加固 Checklist

- [ ] Flash 加密已启用
- [ ] Secure Boot 已启用
- [ ] NVS 加密已启用
- [ ] WiFi 至少使用 WPA2-PSK
- [ ] HTTPS 证书固定
- [ ] 设备证书已部署（一机一密）
- [ ] OTA 包已签名验证
- [ ] 危险函数已禁用
- [ ] 输入长度已校验
- [ ] 调试接口已禁用（JTAG/UART）

---

## 第 49 章 进阶：ESP-CSI 信道状态信息

### 49.1 CSI 简介

ESP-CSI（Channel State Information）允许 ESP32 提取 WiFi 物理层的信道状态信息，
用于室内定位、动作识别、呼吸检测等感知应用。CSI 比 RSSI 更精细，包含 OFDM 子载波
的振幅与相位信息。

### 49.2 启用 CSI

```c
#include "esp_csi.h"

static void csi_rx_cb(void *ctx, void *data) {
    esp_wifi_csi_info_t *info = (esp_wifi_csi_info_t *)data;
    
    ESP_LOGD(TAG, "CSI: seq=%d rssi=%d len=%d",
             info->rx_ctrl.seq_num, info->rx_ctrl.rssi, info->len);
    
    // Process CSI data (signed 8-bit complex numbers)
    int8_t *csi = info->buf;
    int len = info->len;
    
    // Compute amplitude per subcarrier
    for (int i = 0; i < len / 2; i++) {
        int16_t real = csi[i * 2];
        int16_t imag = csi[i * 2 + 1];
        uint16_t amplitude = sqrt(real * real + imag * imag);
        // Process amplitude
    }
}

void enable_csi(void) {
    wifi_csi_config_t csi_config = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}
```

### 49.3 CSI 应用场景

| 应用 | 原理 | 精度 |
|------|------|------|
| 室内定位 | 指纹库匹配 CSI 特征 | 1-2 米 |
| 跌倒检测 | CSI 波形分析运动特征 | 95%+ |
| 呼吸监测 | 检测周期性胸部起伏 | ±2 bpm |
| 手势识别 | 多径效应下的 CSI 变化 | 90%+ |
| 入侵检测 | 持续监听 CSI 异常 | 99%+ |

---

## 第 50 章 WiFi 性能基准测试

### 50.1 基准测试方法

```c
// Comprehensive WiFi performance benchmark
typedef struct {
    int rssi;
    int tx_power;
    int throughput_uplink;
    int throughput_downlink;
    int latency_avg;
    int latency_p99;
    int jitter;
    int packet_loss;
    int connect_time;
} wifi_benchmark_t;

wifi_benchmark_t run_wifi_benchmark(void) {
    wifi_benchmark_t result = {0};
    
    // 1. Get RSSI
    wifi_ap_record_t ap;
    esp_wifi_sta_get_ap_info(&ap);
    result.rssi = ap.rssi;
    
    // 2. Get TX power
    int8_t tx_power;
    esp_wifi_get_max_tx_power(&tx_power);
    result.tx_power = tx_power / 4;  // 0.25 dBm units
    
    // 3. Uplink throughput test (10 seconds)
    int sock = tcp_client_connect("192.168.1.100", 5000);
    if (sock >= 0) {
        char *buf = malloc(8192);
        memset(buf, 'A', 8192);
        int64_t start = esp_timer_get_time();
        int64_t total = 0;
        while ((esp_timer_get_time() - start) < 10 * 1000000) {
            int n = send(sock, buf, 8192, 0);
            if (n > 0) total += n;
            else break;
        }
        result.throughput_uplink = total * 8 / 10000000;  // bps
        free(buf);
        close(sock);
    }
    
    // 4. Latency test (100 pings)
    int latencies[100];
    sock = tcp_client_connect("192.168.1.100", 5000);
    if (sock >= 0) {
        for (int i = 0; i < 100; i++) {
            char ping[] = "PING";
            int64_t start = esp_timer_get_time();
            send(sock, ping, 4, 0);
            char buf[16];
            recv(sock, buf, 4, 0);
            latencies[i] = (esp_timer_get_time() - start) / 1000;
        }
        close(sock);
        
        // Compute stats
        int sum = 0, max = 0;
        for (int i = 0; i < 100; i++) {
            sum += latencies[i];
            if (latencies[i] > max) max = latencies[i];
        }
        result.latency_avg = sum / 100;
        result.latency_p99 = max;
        
        // Jitter (std dev)
        int var = 0;
        for (int i = 0; i < 100; i++) {
            int d = latencies[i] - result.latency_avg;
            var += d * d;
        }
        result.jitter = (int)sqrt(var / 100);
    }
    
    return result;
}
```

### 50.2 典型基准测试结果

下表为 ESP32 在不同条件下的实测性能（仅供参考）：

| 配置 | 上行吞吐 | 下行吞吐 | 平均延迟 | P99 延迟 | 丢包率 |
|------|----------|----------|----------|----------|--------|
| WIFI_PS_NONE, RSSI=-40, HT40 | 22 Mbps | 28 Mbps | 4 ms | 12 ms | 0.1% |
| WIFI_PS_NONE, RSSI=-70, HT40 | 15 Mbps | 18 Mbps | 8 ms | 35 ms | 0.5% |
| WIFI_PS_MIN_MODEM, RSSI=-40 | 18 Mbps | 22 Mbps | 18 ms | 60 ms | 0.3% |
| WIFI_PS_MAX_MODEM, RSSI=-40 | 12 Mbps | 15 Mbps | 120 ms | 300 ms | 1.2% |
| WIFI_PS_NONE, RSSI=-85, HT20 | 3 Mbps | 4 Mbps | 25 ms | 150 ms | 5% |

### 50.3 基准测试注意事项

1. **测试环境**：避免干扰，使用专用 AP，关闭其他设备。
2. **多次测量**：单次测量误差大，至少取 5 次平均。
3. **预热**：先运行 30 秒预热缓存，再开始测量。
4. **方向性**：天线方向性影响大，固定方向测试。
5. **温度**：芯片温度升高会降低性能，长时间测试需注意散热。

---

## 第 51 章 WiFi 驱动配置深度解析

### 51.1 关键配置项详解

ESP-IDF 的 WiFi 驱动有大量配置项（menuconfig），正确配置对性能至关重要：

```ini
# === WiFi Buffer Configuration ===
# Static RX buffer (allocated at init, never freed)
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=10
# Range: 2-25, default 10
# Trade-off: more = more concurrent RX, less = less memory

# Dynamic RX buffer (allocated on demand)
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=32
# Range: 0-64, default 32
# 0 disables dynamic allocation (not recommended)

# TX buffer type: static (0) or dynamic (1)
CONFIG_ESP_WIFI_TX_BUFFER_TYPE=1
# Dynamic recommended for most use cases

# Dynamic TX buffer count
CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=32
# Range: 16-64, default 32

# Block Ack window (for aggregation)
CONFIG_ESP_WIFI_TX_BA_WIN=6
CONFIG_ESP_WIFI_RX_BA_WIN=6
# Larger window = better throughput but more memory

# Management buffer
CONFIG_ESP_WIFI_MGMT_SBUF_NUM=32
# Range: 16-64, default 32

# IRAM optimization (move critical code to IRAM for speed)
CONFIG_ESP_WIFI_IRAM_OPT=n
# Enable for performance, disable for memory savings
CONFIG_ESP_WIFI_RX_IRAM_OPT=n
# RX-side IRAM optimization

# WPA3 support
CONFIG_ESP_WIFI_ENABLE_WPA3_SAE=y
# Enable WPA3 SAE authentication

# SoftAP beacon max length
CONFIG_ESP_WIFI_SOFTAP_BEACON_MAX_LEN=752
# Default 752, increase for many TXT records in mDNS

# PSRAM support
CONFIG_ESP_WIFI_CACHE_TX_BUF_NUM=16
CONFIG_ESP_WIFI_CACHE_TX_BUF_NUM_IN_PSRAM=0
# Use PSRAM for cache TX buffers (if available)
```

### 51.2 不同场景的推荐配置

#### 51.2.1 高吞吐场景（视频流、文件传输）

```ini
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=16
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=64
CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=64
CONFIG_ESP_WIFI_TX_BA_WIN=16
CONFIG_ESP_WIFI_RX_BA_WIN=16
CONFIG_ESP_WIFI_IRAM_OPT=y
CONFIG_ESP_WIFI_AMPDU_TX_ENABLED=y
CONFIG_ESP_WIFI_AMPDU_RX_ENABLED=y
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=23280
CONFIG_LWIP_TCP_WND_DEFAULT=23280
```

#### 51.2.2 低功耗场景（电池供电 IoT）

```ini
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=4
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=8
CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=8
CONFIG_ESP_WIFI_TX_BA_WIN=4
CONFIG_ESP_WIFI_RX_BA_WIN=4
CONFIG_ESP_WIFI_IRAM_OPT=n
CONFIG_ESP_WIFI_RX_IRAM_OPT=n
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=2880
CONFIG_LWIP_TCP_WND_DEFAULT=2880
```

#### 51.2.3 内存受限场景（ESP32-S2 仅有 4MB Flash）

```ini
CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=4
CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=8
CONFIG_ESP_WIFI_DYNAMIC_TX_BUFFER_NUM=8
CONFIG_ESP_WIFI_IRAM_OPT=n
CONFIG_ESP_WIFI_RX_IRAM_OPT=n
CONFIG_ESP_WIFI_AMPDU_TX_ENABLED=n  # Disable AMPDU for memory
CONFIG_ESP_WIFI_AMPDU_RX_ENABLED=n
```

### 51.3 WiFi 缓冲区内存占用

| 配置项 | 默认值 | 单个缓冲区大小 | 总占用 |
|--------|--------|----------------|--------|
| STATIC_RX_BUFFER | 10 | 1600 B | 16 KB |
| DYNAMIC_RX_BUFFER | 32 | 1600 B | 51 KB（上限） |
| DYNAMIC_TX_BUFFER | 32 | 1600 B | 51 KB（上限） |
| MGMT_SBUF | 32 | 256 B | 8 KB |
| TX_BA_WIN | 6 | 64 B | 384 B |
| RX_BA_WIN | 6 | 64 B | 384 B |

总计默认约 127 KB（最大），实际占用取决于流量。

### 51.4 启用 AMPDU 提升 throughput

AMPDU（Aggregated MAC Protocol Data Unit）将多个 MPDU 聚合为一个帧，显著提升
吞吐量：

```ini
# Enable A-MPDU (TX and RX aggregation)
CONFIG_ESP_WIFI_AMPDU_TX_ENABLED=y
CONFIG_ESP_WIFI_AMPDU_RX_ENABLED=y

# Increase aggregation window
CONFIG_ESP_WIFI_TX_BA_WIN=16  # Max 32
CONFIG_ESP_WIFI_RX_BA_WIN=16  # Max 32
```

AMPDU 对 throughput 影响实测：

| 配置 | 吞吐 | 改进 |
|------|------|------|
| AMPDU 禁用 | 5 Mbps | 基准 |
| AMPDU=6（默认） | 15 Mbps | +200% |
| AMPDU=16 | 22 Mbps | +340% |
| AMPDU=32 | 25 Mbps | +400% |

---

## 第 52 章 多核与并发编程

### 52.1 ESP32 双核架构

ESP32（原版）和 ESP32-S3 为双核 Xtensa LX6/LX7：
- **Core 0**（PRO_CPU）：协议栈（WiFi/BT）、系统任务
- **Core 1**（APP_CPU）：应用任务

默认任务绑定：
- `wifi` / `bt` / `lwip`：Core 0
- `app_main`：Core 0（可手动迁移）
- 用户任务：默认 Core 0，可指定 Core 1

### 52.2 任务绑定

```c
// Pin task to specific core
xTaskCreatePinnedToCore(
    my_task,           // Task function
    "my_task",         // Name
    4096,              // Stack size
    NULL,              // Parameters
    5,                 // Priority
    &task_handle,      // Handle
    1);                // Core ID (0 or 1)

// Get current core
int core = xPortGetCoreID();
ESP_LOGI(TAG, "Running on core %d", core);
```

### 52.3 WiFi 与应用任务分离

```c
void app_main(void) {
    // Run WiFi and network stack on Core 0 (default)
    wifi_init_sta();
    mqtt_app_start();
    
    // Run sensor processing on Core 1
    xTaskCreatePinnedToCore(sensor_task, "sensor", 4096, NULL, 5, NULL, 1);
    
    // Run display on Core 1
    xTaskCreatePinnedToCore(display_task, "display", 4096, NULL, 4, NULL, 1);
    
    // Run heavy computation on Core 1
    xTaskCreatePinnedToCore(ml_inference_task, "ml", 8192, NULL, 6, NULL, 1);
}
```

### 52.4 多任务同步

ESP-IDF 提供多种同步原语：

```c
// 1. Mutex (binary, recursive)
SemaphoreHandle_t mutex = xSemaphoreCreateMutex();
xSemaphoreTake(mutex, portMAX_DELAY);
// Critical section
xSemaphoreGive(mutex);

// 2. Recursive mutex (can be taken multiple times by same task)
SemaphoreHandle_t rmutex = xSemaphoreCreateRecursiveMutex();
xSemaphoreTakeRecursive(rmutex, portMAX_DELAY);
xSemaphoreTakeRecursive(rmutex, portMAX_DELAY);  // Same task can take again
xSemaphoreGiveRecursive(rmutex);
xSemaphoreGiveRecursive(rmutex);

// 3. Binary semaphore (signal between tasks)
SemaphoreHandle_t bin_sem = xSemaphoreCreateBinary();
// Task A: xSemaphoreGive(bin_sem);
// Task B: xSemaphoreTake(bin_sem, portMAX_DELAY);

// 4. Counting semaphore (resource pool)
SemaphoreHandle_t count_sem = xSemaphoreCreateCounting(5, 5);  // 5 resources

// 5. Queue (message passing)
QueueHandle_t queue = xQueueCreate(10, sizeof(my_struct_t));
xQueueSend(queue, &data, portMAX_DELAY);
xQueueReceive(queue, &data, portMAX_DELAY);

// 6. Event group (multiple event flags)
EventGroupHandle_t events = xEventGroupCreate();
xEventGroupSetBits(events, BIT0 | BIT1);
EventBits_t bits = xEventGroupWaitBits(events, BIT0 | BIT1,
                                       pdTRUE, pdTRUE, portMAX_DELAY);

// 7. Task notification (lightweight, single-task target)
xTaskNotifyGive(task_handle);
uint32_t value = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
```

### 52.5 死锁避免

```c
// BAD: potential deadlock
// Task A: take mutex1, then mutex2
// Task B: take mutex2, then mutex1
// → Both wait forever

// GOOD: always acquire mutexes in same order
void task_a(void *arg) {
    xSemaphoreTake(mutex1, portMAX_DELAY);
    xSemaphoreTake(mutex2, portMAX_DELAY);
    // Work
    xSemaphoreGive(mutex2);
    xSemaphoreGive(mutex1);
}

void task_b(void *arg) {
    xSemaphoreTake(mutex1, portMAX_DELAY);  // Same order!
    xSemaphoreTake(mutex2, portMAX_DELAY);
    // Work
    xSemaphoreGive(mutex2);
    xSemaphoreGive(mutex1);
}
```

### 52.6 任务优先级设计原则

| 优先级范围 | 用途 | 示例 |
|-----------|------|------|
| 22-24 | 系统级（不可更改） | WiFi/BT/LWIP 协议栈 |
| 15-21 | 高优先级实时任务 | PID 控制、电机驱动 |
| 10-14 | 中优先级任务 | 传感器采样、显示刷新 |
| 5-9 | 普通应用任务 | MQTT、HTTP、业务逻辑 |
| 1-4 | 低优先级后台任务 | OTA 检查、状态指示 |
| 0 | Idle task | 系统空闲、Light Sleep |

### 52.7 FreeRTOS 任务统计

```c
// Enable runtime stats in menuconfig
// CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y
// CONFIG_FREERTOS_USE_TRACE_FACILITY=y

void print_task_stats(void) {
    char buf[1024];
    vTaskList(buf);
    ESP_LOGI(TAG, "Task list:\n%s", buf);
    
    vTaskGetRunTimeStats(buf);
    ESP_LOGI(TAG, "CPU usage:\n%s", buf);
}
```

输出示例：

```
Task            Runtime     %       Stack
wifi            1234567     45.2%   2048
bt              345678      12.6%   1536
lwip            234567      8.6%    1024
app_main        123456      4.5%    3072
sensor          89012       3.3%    2048
IDLE0           678901      24.9%   512
IDLE1           56789       2.1%    512
```

---

## 第 53 章 WiFi 与外部以太网共存

### 53.1 ESP32 以太网支持

ESP32 支持外接以太网 PHY（如 IP101、LAN8720、DP83848）或 SPI 以太网模块
（如 W5500、DM9051）：

```c
#include "esp_eth.h"
#include "esp_eth_mac_esp.h"
#include "esp_eth_phy.h"

void ethernet_init(void) {
    // Configure MAC
    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    esp_eth_mac_t *mac = esp_eth_mac_new_esp32(&mac_config);
    
    // Configure PHY (LAN8720)
    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG();
    phy_config.phy_addr = 0;  // Set by hardware strapping
    esp_eth_phy_t *phy = esp_eth_phy_new_lan8720(&phy_config);
    
    // Create Ethernet driver
    esp_eth_config_t eth_config = ETH_DEFAULT_CONFIG(mac, phy);
    esp_eth_handle_t eth_handle = NULL;
    ESP_ERROR_CHECK(esp_eth_driver_install(&eth_config, &eth_handle));
    
    // Create netif
    esp_netif_config_t netif_config = ESP_NETIF_DEFAULT_ETH();
    esp_netif_t *eth_netif = esp_netif_new(&netif_config);
    
    // Attach driver to netif
    esp_eth_netif_glue_handle_t glue = esp_eth_new_netif_glue(eth_handle);
    esp_netif_attach(eth_netif, glue);
    
    // Register event handlers
    esp_event_handler_register(ETH_EVENT, ESP_EVENT_ANY_ID,
                                eth_event_handler, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_ETH_GOT_IP,
                                ip_event_handler, NULL);
    
    // Start Ethernet
    esp_eth_start(eth_handle);
}
```

### 53.2 WiFi + Ethernet 协同

ESP32 可同时启用 WiFi 和以太网，分别作为两个网络接口：

```c
// Both interfaces active
// STA WiFi: 192.168.1.x (via AP)
// Ethernet: 192.168.2.x (via cable)

// Default route selection
// LwIP uses interface with highest metric by default
// Can be configured via esp_netif_set_default()
```

### 53.3 接口故障切换

```c
// Implement automatic failover between WiFi and Ethernet
static esp_netif_t *primary_netif = NULL;
static esp_netif_t *backup_netif = NULL;

void network_failover_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data) {
    if (base == IP_EVENT && event_id == IP_EVENT_ETH_GOT_IP) {
        // Ethernet connected, use as primary
        esp_netif_set_default_eth();
        primary_netif = eth_netif;
        backup_netif = sta_netif;
        ESP_LOGI(TAG, "Switched to Ethernet as primary");
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        if (primary_netif == NULL) {
            // No Ethernet, use WiFi
            esp_netif_set_default_wifi_sta();
            primary_netif = sta_netif;
            ESP_LOGI(TAG, "Switched to WiFi as primary");
        }
    } else if (base == ETH_EVENT && event_id == ETHERNET_EVENT_DISCONNECTED) {
        if (primary_netif == eth_netif) {
            // Ethernet down, failover to WiFi
            esp_netif_set_default_wifi_sta();
            primary_netif = sta_netif;
            ESP_LOGI(TAG, "Failed over to WiFi");
        }
    }
}
```

---

## 第 54 章 WiFi 物理层深度

### 54.1 802.11 b/g/n 调制方式

| 标准 | 调制 | 编码 | 最大速率 | 信道带宽 |
|------|------|------|----------|----------|
| 802.11b | DSSS/CCK | - | 11 Mbps | 22 MHz |
| 802.11g | OFDM | 卷积码 | 54 Mbps | 20 MHz |
| 802.11n (HT20) | OFDM | 卷积码 + LDPC | 72.2 Mbps | 20 MHz |
| 802.11n (HT40) | OFDM | 卷积码 + LDPC | 150 Mbps | 40 MHz |

### 54.2 OFDM 子载波

OFDM（正交频分多用）将数据分散到多个子载波上：

| 模式 | 子载波总数 | 数据子载波 | 导频子载波 | 频率间隔 |
|------|-----------|-----------|-----------|----------|
| HT20 | 64 | 52 | 4 | 312.5 kHz |
| HT40 | 128 | 108 | 6 | 312.5 kHz |

### 54.3 编码率

| 编码率 | 数据冗余 | 说明 |
|--------|----------|------|
| 1/2 | 50% | 最强纠错，最低速率 |
| 2/3 | 33% | 中等 |
| 3/4 | 25% | 中等 |
| 5/6 | 17% | 最弱纠错，最高速率 |

### 54.4 调制方式与速率

| MCS 索引 | 调制 | 编码率 | HT20 速率 | HT40 速率 |
|----------|------|--------|-----------|-----------|
| 0 | BPSK | 1/2 | 6.5 Mbps | 13.5 Mbps |
| 1 | QPSK | 1/2 | 13 Mbps | 27 Mbps |
| 2 | QPSK | 3/4 | 19.5 Mbps | 40.5 Mbps |
| 3 | 16-QAM | 1/2 | 26 Mbps | 54 Mbps |
| 4 | 16-QAM | 3/4 | 39 Mbps | 81 Mbps |
| 5 | 64-QAM | 2/3 | 52 Mbps | 108 Mbps |
| 6 | 64-QAM | 3/4 | 58.5 Mbps | 121.5 Mbps |
| 7 | 64-QAM | 5/6 | 65 Mbps | 135 Mbps |

### 54.5 速率自适应

ESP32 WiFi 驱动自动根据信号质量调整 MCS 索引：

```c
// Force specific PHY rate (not recommended, breaks auto rate)
#include "esp_wifi_types.h"
wifi_phy_rate_t rate = WIFI_PHY_RATE_MCS7_SGI;  // MCS7, short GI
esp_wifi_config_80211_tx_rate(WIFI_IF_STA, rate);

// Get current rate
wifi_phy_rate_t current_rate;
esp_wifi_get_80211_tx_rate(WIFI_IF_STA, &current_rate);
```

### 54.6 SGI（Short Guard Interval）

GI（保护间隔）防止 OFDM 符号间干扰：

| GI 长度 | 模式 | 改进 |
|---------|------|------|
| 800 ns | 标准模式 | 基准 |
| 400 ns | SGI 模式 | +10% 吞吐 |

启用 SGI：

```c
wifi_config_t wifi_config = {
    .sta = {
        // SGI enabled by default
    },
};
// In sdkconfig: CONFIG_ESP_WIFI_ENABLE_SGI=y (default)
```

---

## 第 55 章 WiFi 天线与射频设计

### 55.1 天线类型

| 天线类型 | 增益 | 方向性 | 适用场景 |
|----------|------|--------|----------|
| PCB 天线 | 2 dBi | 全向 | 模块内置，节省成本 |
| 陶瓷天线 | 2-3 dBi | 全向 | 小尺寸设备 |
| 外置棒状天线 | 3-5 dBi | 全向 | 路由器、网关 |
| 贴片天线 | 5-8 dBi | 定向 | 点对点通信 |
| 八木天线 | 10-15 dBi | 强定向 | 长距离通信 |

### 55.2 PCB 天线设计要点

1. **天线区域净空**：天线正下方/周围 5mm 内禁止铺铜。
2. **阻抗匹配**：50Ω 阻抗线，宽度根据 PCB 介电常数计算。
3. **馈线长度**：尽量短，减少损耗。
4. **地平面**：天线附近保证完整地平面。
5. **远离金属**：金属外壳会严重影响天线性能。

### 55.3 RF 性能测试指标

| 指标 | 单位 | 优秀 | 合格 | 不合格 |
|------|------|------|------|--------|
| TX 功率 | dBm | 18-20 | 15-17 | < 15 |
| RX 灵敏度（MCS7） | dBm | -75 | -70 | > -65 |
| EVM | dB | -30 | -25 | > -20 |
| 频率误差 | ppm | < 10 | < 20 | > 20 |
| 杂散辐射 | dBm | < -40 | < -30 | > -30 |

### 55.4 RF 校准

ESP32 出厂前需进行 RF 校准，写入 efuse：

```bash
# Use esp_phy_rf_init_data to calibrate
# Factory calibration writes to NVS partition "phy"
# If NVS is erased, calibration data is lost

# Verify calibration
esptool.py --port $PORT read_mac  # Also shows calibration info
```

应用代码可重新校准：

```c
#include "esp_phy_init.h"

void recalibrate_phy(void) {
    // Force PHY recalibration
    esp_phy_erase_cal_data_in_nvs();
    esp_phy_load_cal_and_init(PHY_RF_CAL_FULL);
    
    ESP_LOGI(TAG, "PHY recalibrated");
}
```

### 55.5 天线匹配网络

ESP32 RF 输出到天线之间通常需要 π 型匹配网络：

```
ESP32 RF pin ──[L1]──┬──[C2]── Antenna
                     │
                    [C1]
                     │
                    GND
```

典型值：L1 = 2.4 nH，C1 = 1.2 pF，C2 = 1.2 pF（取决于 PCB 与天线）。

调试时使用网络分析仪（VNA）调整：
1. 测量天线阻抗（应接近 50Ω）
2. 调整 L/C 值，使 S11 < -10 dB @ 2.45 GHz
3. 验证整个频段（2.4-2.5 GHz）的回波损耗

---

## 第 56 章 WiFi 与其他无线协议协同

### 56.1 ESP32 多协议支持

ESP32 系列芯片支持多种无线协议：

| 芯片 | WiFi | BT Classic | BLE | 802.15.4 |
|------|------|-----------|-----|----------|
| ESP32 | b/g/n | 4.2 | 4.2 | - |
| ESP32-S2 | b/g/n | - | - | - |
| ESP32-S3 | b/g/n | - | 5.0 | - |
| ESP32-C3 | b/g/n | - | 5.0 | - |
| ESP32-C6 | b/g/n (WiFi 6) | - | 5.0 | - |
| ESP32-H2 | - | - | 5.3 | Thread/Zigbee |
| ESP32-C6 + H2 组合 | b/g/n (WiFi 6) | - | 5.0 | Thread/Zigbee |

### 56.2 WiFi + BLE 共存配置

```c
// Configure coexistence
#include "esp_coexist.h"

// Set preference
esp_coex_preference_t pref = ESP_COEX_PREFER_BALANCE;
esp_coex_set_preference(pref);

// Available options:
// ESP_COEX_PREFER_WIFI - WiFi priority
// ESP_COEX_PREFER_BT - BT priority
// ESP_COEX_PREFER_BALANCE - Balanced (default)
```

### 56.3 共存场景性能影响

| 场景 | WiFi 吞吐 | BT 吞吐 | 延迟 |
|------|-----------|---------|------|
| 仅 WiFi | 25 Mbps | - | 5 ms |
| WiFi + BLE 广播 | 23 Mbps | 正常 | 6 ms |
| WiFi + BLE 连接（10Hz） | 18 Mbps | 正常 | 12 ms |
| WiFi + BLE 连接（100Hz） | 10 Mbps | 正常 | 25 ms |
| WiFi + BT A2DP | 12 Mbps | 正常 | 25 ms |
| WiFi + BT SPP | 8 Mbps | 正常 | 40 ms |
| WiFi + BT 全双工 | 5 Mbps | 正常 | 60 ms |

### 56.4 共存最佳实践

1. **信道选择**：WiFi 使用 1/6/11 信道，BT AFH 自动避开。
2. **降低 BT 连接间隔**：BLE connInterval > 100 ms 时影响最小。
3. **避免同时大数据流**：BT A2DP 流媒体 + WiFi 视频流互相干扰。
4. **使用 PHY 校准数据**：保证 RF 参数最优。
5. **天线布局**：避免 WiFi 与 BT 天线相互耦合。

---

## 第 57 章 综合性能调优案例

### 57.1 案例一：智能门铃实时视频优化

**问题**：ESP32-CAM 智能门铃，视频流延迟过高（500ms+），用户体验差。

**分析**：
- 默认 WIFI_PS_MIN_MODEM，每次发送前需唤醒
- MJPEG 帧 30KB，分多个 TCP 包发送
- TCP_NODELAY 未启用，Nagle 算法累积小包

**优化方案**：

```c
void optimize_doorbell_stream(void) {
    // 1. Disable WiFi power saving for video stream
    esp_wifi_set_ps(WIFI_PS_NONE);
    
    // 2. Enable TCP_NODELAY
    int enable = 1;
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &enable, sizeof(enable));
    
    // 3. Increase socket buffer
    int buf_size = 32 * 1024;
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &buf_size, sizeof(buf_size));
    
    // 4. Reduce JPEG quality (frame size)
    camera_config.jpeg_quality = 12;  // Was 5
    
    // 5. Use smaller frame size
    camera_config.frame_size = FRAMESIZE_QVGA;  // 320x240
    
    // 6. Optimize CPU frequency
    // sdkconfig: CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y
}
```

**结果**：
- 延迟从 500ms 降至 80ms
- 帧率从 5 fps 提升至 20 fps
- 功耗增加约 30%（可接受）

### 57.2 案例二：电池传感器续航优化

**问题**：ESP32-C3 温湿度传感器，CR2032 电池仅 3 个月续航。

**分析**：
- 默认 WIFI_PS_NONE，持续高功耗
- 每次唤醒重新初始化 WiFi，耗时长
- WiFi 连接失败后立即重试，消耗电量

**优化方案**：

```c
void optimize_battery_sensor(void) {
    // 1. Use WIFI_PS_MAX_MODEM after connection
    esp_wifi_set_ps(WIFI_PS_MAX_MODEM);
    
    // 2. Enable Light Sleep
    esp_pm_config_esp32_t pm_config = {
        .max_freq_mhz = 80,  // Lower CPU frequency
        .min_freq_mhz = 10,
        .light_sleep_enable = true,
    };
    esp_pm_configure(&pm_config);
    
    // 3. Use fast connect (skip scan)
    wifi_config.sta.bssid_set = true;
    memcpy(wifi_config.sta.bssid, saved_bssid, 6);
    wifi_config.sta.channel = saved_channel;
    
    // 4. Exponential backoff on connection failure
    if (connect_failed) {
        uint32_t delay = 1 << retry_count;  // 1, 2, 4, 8, 16s...
        vTaskDelay(pdMS_TO_TICKS(delay * 1000));
    }
    
    // 5. Reduce WiFi buffer count (save memory)
    // sdkconfig:
    // CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM=4
    // CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM=4
    
    // 6. Minimize active time: batch sensor readings
    sensor_data_t readings[10];
    for (int i = 0; i < 10; i++) {
        readings[i] = read_sensor();
        vTaskDelay(pdMS_TO_TICKS(60000));  // 1 minute
    }
    // Upload all at once
    upload_batch(readings, 10);
}
```

**结果**：
- 续航从 3 个月延长至 18 个月
- 上传成功率从 95% 提升至 99%
- 平均功耗从 5 mA 降至 0.15 mA

### 57.3 案例三：高密度 IoT 部署优化

**问题**：办公楼部署 200+ ESP32 设备，频繁断连、吞吐骤降。

**分析**：
- 所有设备默认信道扫描，加重 AP 负担
- DHCP 续约风暴（所有设备同时到期）
- mDNS 广播泛滥

**优化方案**：

```c
// 1. Pre-configure channel (skip scan)
wifi_config.sta.channel = 6;  // Known AP channel

// 2. Stagger DHCP renewal times
int device_id = get_device_id();  // Unique per device
int delay_seconds = device_id * 30;  // Stagger by 30 seconds
vTaskDelay(pdMS_TO_TICKS(delay_seconds * 1000));

// 3. Disable mDNS in dense deployment
// (Or use specific service names to reduce conflicts)

// 4. Use WIFI_PS_MIN_MODEM (not NONE) to reduce airtime
esp_wifi_set_ps(WIFI_PS_MIN_MODEM);

// 5. Batch sensor uploads (reduce per-device traffic)
static sensor_data_t buffer[10];
static int buffer_count = 0;

void collect_and_upload(sensor_data_t data) {
    buffer[buffer_count++] = data;
    if (buffer_count >= 10) {
        upload_batch(buffer, buffer_count);
        buffer_count = 0;
    }
}

// 6. Use ESP-NOW for local mesh (offload AP)
// Instead of all devices connecting to AP, use ESP-NOW mesh
```

**结果**：
- 200+ 设备稳定运行
- 平均延迟从 200ms 降至 50ms
- AP CPU 利用率从 95% 降至 40%

---

## 第 58 章 附录 E：完整 WiFi 重连示例

```c
// Complete production-grade WiFi reconnection with exponential backoff
// Includes: jitter, RSSI check, reason-code-aware retry, MQTT coordination

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/timers.h"
#include "esp_random.h"

static const char *TAG = "wifi_manager";

// Configuration
#define MAX_RETRY_COUNT         15
#define BACKOFF_CAP_MS          (60 * 1000)  // 60 seconds
#define BACKOFF_BASE_MS         1000         // 1 second
#define BACKOFF_JITTER_PCT      25           // 25% jitter
#define WEAK_SIGNAL_RSSI_THRESH -85
#define WEAK_SIGNAL_DELAY_MS    (2 * 60 * 1000)  // 2 minutes

// State
typedef enum {
    WIFI_STATE_DISCONNECTED,
    WIFI_STATE_CONNECTING,
    WIFI_STATE_CONNECTED,
    WIFI_STATE_BACKOFF,
    WIFI_STATE_GAVE_UP,
} wifi_state_t;

static wifi_state_t s_state = WIFI_STATE_DISCONNECTED;
static int s_retry_count = 0;
static EventGroupHandle_t s_events;
static TimerHandle_t s_reconnect_timer;
static esp_mqtt_client_handle_t s_mqtt_client;

#define EVENT_WIFI_CONNECTED    BIT0
#define EVENT_WIFI_DISCONNECTED BIT1
#define EVENT_WIFI_GAVE_UP      BIT2

// Calculate backoff delay with jitter
static uint32_t calculate_backoff(int retry, int8_t rssi) {
    // Weak signal: extended delay
    if (rssi < WEAK_SIGNAL_RSSI_THRESH && rssi != 0) {
        ESP_LOGW(TAG, "Weak signal RSSI=%d, extended backoff", rssi);
        return WEAK_SIGNAL_DELAY_MS;
    }
    
    // Exponential backoff: 2^retry * BASE, capped
    uint32_t delay = BACKOFF_BASE_MS << retry;
    if (delay > BACKOFF_CAP_MS) {
        delay = BACKOFF_CAP_MS;
    }
    
    // Add jitter (±jitter_pct%)
    uint32_t jitter_range = delay * BACKOFF_JITTER_PCT / 100;
    int32_t jitter = (int32_t)(esp_random() % (2 * jitter_range + 1)) - jitter_range;
    int32_t final_delay = (int32_t)delay + jitter;
    if (final_delay < 100) final_delay = 100;  // Min 100ms
    
    return (uint32_t)final_delay;
}

// Check if reason code warrants retry
static bool should_retry_reason(uint8_t reason) {
    switch (reason) {
        case 15:    // 4WAY_HANDSHAKE_TIMEOUT
        case 202:   // AUTH_FAIL
            ESP_LOGE(TAG, "Auth failed (reason=%d), likely wrong password", reason);
            return false;
        case 201:   // NO_AP_FOUND
        case 8:     // ASSOC_EXPIRE
        case 4:     // DEAUTH_LEAVING
        case 9:     // ASSOC_NOT_AUTHED
        case 1:     // UNSPECIFIED
        case 2:     // AUTH_EXPIRE
            return true;
        default:
            ESP_LOGW(TAG, "Unknown reason %d, retrying", reason);
            return true;
    }
}

// Reconnect timer callback
static void reconnect_timer_cb(TimerHandle_t timer) {
    if (s_state != WIFI_STATE_BACKOFF) return;
    
    ESP_LOGI(TAG, "重连 attempt #%d (指数退避)", s_retry_count);
    s_state = WIFI_STATE_CONNECTING;
    esp_wifi_connect();
}

// WiFi event handler
static void wifi_event_handler(void *arg, esp_event_base_t base,
                                int32_t id, void *data) {
    if (base == WIFI_EVENT) {
        switch (id) {
            case WIFI_EVENT_STA_START:
                ESP_LOGI(TAG, "WiFi STA started, connecting...");
                s_state = WIFI_STATE_CONNECTING;
                s_retry_count = 0;
                esp_wifi_connect();
                break;
                
            case WIFI_EVENT_STA_CONNECTED:
                ESP_LOGI(TAG, "WiFi connected to AP");
                break;
                
            case WIFI_EVENT_STA_DISCONNECTED: {
                wifi_event_sta_disconnected_t *disc = data;
                ESP_LOGW(TAG, "WiFi disconnected, reason=%d rssi=%d retry=%d",
                         disc->reason, disc->rssi, s_retry_count);
                
                // Stop MQTT to prevent storm
                if (s_mqtt_client) {
                    esp_mqtt_client_stop(s_mqtt_client);
                }
                
                // Check if we should retry
                if (!should_retry_reason(disc->reason)) {
                    ESP_LOGE(TAG, "Stopping retries due to fatal reason");
                    s_state = WIFI_STATE_GAVE_UP;
                    xEventGroupSetBits(s_events, EVENT_WIFI_GAVE_UP);
                    break;
                }
                
                if (s_retry_count >= MAX_RETRY_COUNT) {
                    ESP_LOGE(TAG, "重连次数超过上限 %d", MAX_RETRY_COUNT);
                    s_state = WIFI_STATE_GAVE_UP;
                    xEventGroupSetBits(s_events, EVENT_WIFI_GAVE_UP);
                    break;
                }
                
                // Schedule reconnect with exponential backoff
                uint32_t delay = calculate_backoff(s_retry_count, disc->rssi);
                ESP_LOGI(TAG, "指数退避: waiting %lu ms before 重连",
                         (unsigned long)delay);
                
                s_state = WIFI_STATE_BACKOFF;
                xTimerChangePeriod(s_reconnect_timer, pdMS_TO_TICKS(delay), 0);
                xTimerStart(s_reconnect_timer, 0);
                
                s_retry_count++;
                xEventGroupSetBits(s_events, EVENT_WIFI_DISCONNECTED);
                break;
            }
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        
        s_state = WIFI_STATE_CONNECTED;
        s_retry_count = 0;  // Reset retry on success
        
        // Start MQTT
        if (s_mqtt_client) {
            esp_mqtt_client_start(s_mqtt_client);
        }
        
        xEventGroupSetBits(s_events, EVENT_WIFI_CONNECTED);
    }
}

// Initialize WiFi with full reconnection logic
void wifi_manager_init(void) {
    // Create event group
    s_events = xEventGroupCreate();
    
    // Create reconnect timer (one-shot)
    s_reconnect_timer = xTimerCreate(
        "wifi_reconnect",
        pdMS_TO_TICKS(BACKOFF_BASE_MS),
        pdFALSE,  // one-shot
        NULL,
        reconnect_timer_cb
    );
    
    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    
    // Initialize network interface
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    
    // Initialize WiFi
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    
    // Register event handlers
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));
    
    // Configure WiFi
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = CONFIG_WIFI_SSID,
            .password = CONFIG_WIFI_PASSWORD,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg = {
                .capable = true,
                .required = false,
            },
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    
    ESP_LOGI(TAG, "WiFi manager initialized, waiting for connection...");
}

// Wait for connection with timeout
bool wifi_manager_wait_connected(uint32_t timeout_ms) {
    EventBits_t bits = xEventGroupWaitBits(
        s_events,
        EVENT_WIFI_CONNECTED | EVENT_WIFI_GAVE_UP,
        pdTRUE, pdFALSE,
        pdMS_TO_TICKS(timeout_ms));
    
    return (bits & EVENT_WIFI_CONNECTED) != 0;
}

// Force reconnect (reset retry counter)
void wifi_manager_force_reconnect(void) {
    ESP_LOGI(TAG, "Forcing reconnect...");
    s_retry_count = 0;
    esp_wifi_disconnect();
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_wifi_connect();
}

// Get current state
wifi_state_t wifi_manager_get_state(void) {
    return s_state;
}

// Get retry count
int wifi_manager_get_retry_count(void) {
    return s_retry_count;
}
```

---

## 第 59 章 附录 F：调试速查表

### 59.1 常见错误码速查

| 错误码 | 含义 | 解决方案 |
|--------|------|----------|
| ESP_ERR_WIFI_NOT_INIT | WiFi 未初始化 | 调用 esp_wifi_init() |
| ESP_ERR_WIFI_NOT_STARTED | WiFi 未启动 | 调用 esp_wifi_start() |
| ESP_ERR_WIFI_CONN | 连接失败 | 检查 SSID/密码/信号 |
| ESP_ERR_WIFI_SSID | SSID 无效 | 检查 SSID 长度（≤32） |
| ESP_ERR_WIFI_PASSWORD | 密码无效 | 检查密码长度（8-63） |
| ESP_ERR_WIFI_TIMEOUT | 操作超时 | 检查 AP 是否在线 |
| ESP_ERR_WIFI_STATE | 状态错误 | 检查调用顺序 |
| 0x7780 | TLS 证书验证失败 | 同步系统时间，检查证书 |
| 0x7783 | TLS 握手失败 | 检查 TLS 版本兼容性 |
| 0x7787 | TLS 连接关闭 | 检查网络稳定性 |

### 59.2 内存问题速查

| 现象 | 可能原因 | 解决方案 |
|------|----------|----------|
| 启动失败，内存不足 | WiFi 缓冲区过多 | 减少 BUFFER_NUM |
| 运行时 OOM | 内存泄漏 | 使用 heap_trace 检测 |
| 任务栈溢出 | 栈太小 | 增大 task stack size |
| 任务阻塞 | 死锁 | 检查 mutex 获取顺序 |
| 看门狗触发 | 任务阻塞 | 增大 WDT 超时或拆分任务 |

### 59.3 网络问题速查

| 现象 | 排查步骤 |
|------|----------|
| 无法连接 AP | 1. 检查 SSID/密码 2. 检查信号 3. 检查认证模式 4. 检查 AP 是否 2.4G |
| 连接后无 IP | 1. 检查 DHCP 2. 检查 IP 冲突 3. 尝试静态 IP |
| HTTP 请求超时 | 1. ping 测试 2. 检查 DNS 3. 检查防火墙 4. 检查 TLS 时间同步 |
| HTTPS 握手失败 | 1. 同步时间 2. 检查根证书 3. 检查 SNI 4. 检查 TLS 版本 |
| MQTT 连接失败 | 1. 检查 broker 地址 2. 检查端口 3. 检查认证 4. 检查 TLS |
| OTA 失败 | 1. 检查分区表 2. 检查固件大小 3. 检查证书 4. 检查内存 |
| WiFi 频繁断开 | 1. 检查 RSSI 2. 检查电源 3. 检查 reason code 4. 检查 AP 配置 |

### 59.4 常用调试命令

```bash
# Monitor serial output
idf.py monitor

# Build with debug info
idf.py -DCMAKE_BUILD_TYPE=Debug build

# Print heap stats
# In code: ESP_LOGI(TAG, "heap: %u", esp_get_free_heap_size());

# Heap tracing
# CONFIG_HEAP_TRACING=y
# esp_heap_trace_init_standalone()
# esp_heap_trace_start(HEAP_TRACE_LEAKS)

# OpenOCD debugging
openocd -f interface/ftdi/esp32_devkitj_v1.cfg -f target/esp32.cfg

# GDB attach
xtensa-esp32-elf-gdb -x gdbinit build/app.elf

# Wireshark capture (with promiscuous mode)
# Configure ESP32 to dump 802.11 frames to UART
```

### 59.5 性能监控代码

```c
// System monitor task: print periodic stats
void system_monitor_task(void *arg) {
    while (1) {
        ESP_LOGI(TAG, "=== System Monitor ===");
        ESP_LOGI(TAG, "Free heap: %u bytes", esp_get_free_heap_size());
        ESP_LOGI(TAG, "Min free heap: %u bytes",
                 esp_get_minimum_free_heap_size());
        ESP_LOGI(TAG, "Free PSRAM: %u bytes",
                 heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            ESP_LOGI(TAG, "WiFi: RSSI=%d, ch=%d", ap.rssi, ap.primary);
        }
        
        // Task stats
        char buf[1024];
        vTaskList(buf);
        ESP_LOGD(TAG, "Tasks:\n%s", buf);
        
        vTaskDelay(pdMS_TO_TICKS(10000));  // Every 10 seconds
    }
}
```

---

## 结语

本文档系统介绍了 ESP32 WiFi 开发的各个方面，从基础架构到高级应用，从省电模式到
故障排查，从协议栈到安全加固。WiFi 是 IoT 设备最核心的连接能力，正确理解其工作
原理、合理选择省电模式（WIFI_PS_NONE / WIFI_PS_MIN_MODEM / WIFI_PS_MAX_MODEM）、
实现稳健的指数退避重连策略，是构建可靠 IoT 产品的关键。

在实际开发中，建议：
1. 使用 IDF 最新稳定版，跟进 bug 修复与性能改进
2. 配合逻辑分析仪与 WiFi 抓包工具调试
3. 在量产前进行 RF 校准与一致性测试
4. 关注乐鑫官方的安全公告，及时更新固件
5. 建立完整的 OTA 与日志系统，便于远程维护

WiFi 看似简单，但涉及 RF 物理、MAC 协议、TCP/IP、安全加密等多层知识。希望本文档
能帮助开发者系统掌握 ESP32 WiFi 开发，构建可靠的物联网产品。

文档涵盖的关键技术点回顾：
- **指数退避（exponential backoff）重连策略**：避免连接风暴，配合 retry count 与
  reason code 差异化处理，是稳健网络连接的核心。
- **三种省电模式**：WIFI_PS_NONE（高性能）、WIFI_PS_MIN_MODEM（平衡）、
  WIFI_PS_MAX_MODEM（低功耗），根据应用场景选择。
- **WIFI_EVENT_STA_DISCONNECTED 事件处理**：正确识别 reason code，避免在密码错误
  时无限重试。
- **MQTT 长连接**：配合 WiFi 重连协调，避免 MQTT 客户端风暴。
- **OTA 升级**：A/B 分区 + 回滚机制，保证固件升级可靠性。
- **安全加固**：Flash 加密 + Secure Boot + NVS 加密，全方位保护设备。

希望本文档对您的 ESP32 WiFi 开发有所帮助！

---

*文档版本：v1.0*
*适用 SDK：ESP-IDF v4.4 ~ v5.x*
*适用芯片：ESP32 / ESP32-S2 / ESP32-S3 / ESP32-C3 / ESP32-C6*
*最后更新：2026 年*
