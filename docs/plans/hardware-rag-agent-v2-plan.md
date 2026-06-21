# Hardware RAG Agent — v2 详细计划

## 硬件接入阶段：写得出、跑得通

> 前置条件：v1 MVP 已完成（RAG 问答 + 5 Agent 工具 + 三类测试集通过率 ≥ 70%）
> 定位：从"硬件文档问答"进化为"硬件开发搭档"
> 部署：本地部署（用户电脑 + USB/WiFi 直连硬件）
> **平台支持：v2 首发仅支持 Windows 10/11。macOS / Linux 在 v2 稳定后适配，不阻塞硬件接入开发。**

---

## 架构全景

```
用户电脑（本地部署）
┌──────────────────────────────────────────────────────────┐
│              Hardware RAG Agent（本地）                    │
│                                                          │
│  界面层  │ Web UI（localhost:8000）                       │
│          │   - 问答界面                                   │
│          │   - 代码编辑器（语法高亮 + 一键烧录按钮）       │
│          │   - 串口监视器（实时日志面板）                   │
│          │   - 烧录进度条                                 │
│                                                          │
│  智能层  │ LangChain ReAct Agent（新增 v2 工具）           │
│          │   v1 工具保持不变：                             │
│          │   - search_hardware_kb                         │
│          │   - generate_hardware_code                     │
│          │   - review_hardware_code                       │
│          │   - compare_hardware_components（升级结构化）   │
│          │   - diagnose_hardware_problem                  │
│          │  新增 v2 工具：                                 │
│          │   - compile_and_flash（USB 烧录）               │
│          │   - read_serial（串口读取）                     │
│          │   - ota_flash（WiFi OTA 烧录）                 │
│          │   - hardware_guardrails（输出安全校验）          │
│                                                          │
│  烧录层  │ esptool / mpremote / arduino-cli              │
│          │   ↓ USB / WiFi                                │
│          │   ESP32-S3 / ESP32-C3 / STM32F4               │
│          │       ↓ 串口回传                               │
│          │   pyserial → Agent 分析                         │
└──────────────────────────────────────────────────────────┘
```

---

## v2 核心能力
| 新能力 | 说明 |
|--------|------|
| USB 烧录 | 编译 MicroPython/Arduino C 代码 → 烧录到开发板 → 读回串口 |
| WiFi OTA 烧录 | 不插 USB，通过 WiFi 推送固件 |
| 硬件调试闭环 | Agent 完成"生成→烧录→报错→修改→再烧录"循环 |
| 硬件安全护栏 | 代码输出前检查 GPIO/地址/引脚合法性 |
| 外设元数据注入 | config.yaml 结构化数据注入 prompt，防止低级错误 |
| 结构化精确比对 | JSON 表格 diff，不靠 LLM 猜差异 |
| 踩坑记录检索 | Agent 检索用户个人踩坑笔记 > 公开 datasheet |

---

### 新增工具详解

**`compile_and_flash`：**
```python
def compile_and_flash(code: str, board: str, port: str, mode: str = "micropython") -> dict:
    """
    board:   "esp32-s3" / "esp32-c3" / "stm32f4"
    port:    "COM3" (Windows) / "/dev/ttyUSB0" (Linux/Mac)
    mode:    "micropython" | "arduino" | "idf"
    return:  {"success": bool, "output": "...", "error": "..."}
    """
    # 1. 保存代码到临时目录
    # 2. MicroPython: mpremote connect {port} run {file}
    #    Arduino:    arduino-cli compile --fqbn {board_def} && arduino-cli upload -p {port}
    #    ESP-IDF:    idf.py build && idf.py -p {port} flash
    # 3. 返回烧录结果（成功/失败 + 输出日志）
```

**`read_serial`：**
```python
def read_serial(port: str, baud: int = 115200, timeout: int = 5) -> str:
    """读取串口输出，返回纯文本"""
```

**`monitor_serial`：**
```python
def monitor_serial(port: str, duration: int = 10) -> str:
    """持续监听串口一段时间，返回完整日志"""
```

**`ota_flash`：**
```python
def ota_flash(code: str, esp_ip: str, mode: str = "mp_ota") -> dict:
    """
    通过 WiFi OTA 推送代码。
    前置：开发板预先烧录了 OTA 固件（一次性的，v2 提供模板）
    """
```

**`hardware_guardrails`：**
```python
def hardware_guardrails(code: str, chip: str) -> list[str]:
    """返回违规列表，空列表 = 安全"""
    violations = []
    # GPIO 引脚号 ≤ 芯片最大引脚数
    # 0x 地址在合法区间
    # I2C 地址是常见合法值（0x27, 0x3C, 0x68 等）
    # 电源引脚（VCC/GND）没有被配置为 GPIO
    # 中断引脚没有被重复分配

    # 🆕 Strapping 引脚检查（硬件死机隐患）
    STRAPPING_PINS = {
        "esp32-s3": [0, 2, 3, 5, 12, 15],
        "esp32-c3": [0, 2, 3, 5, 12],
    }
    used_gpios = extract_gpio_pins(code)  # 从代码中提取所有 GPIO 编号
    for pin in used_gpios:
        if pin in STRAPPING_PINS.get(chip, []):
            violations.append(
                f"⚠️ GPIO{pin} 是 {chip} 的 Strapping 引脚，"
                f"上电电平决定启动模式，使用可能导致芯片死机。建议换用其他 GPIO。"
            )

    return violations
```

---

## 里程碑规划

### Phase 2-A：USB 烧录链路打通（核心，不做别的）

**目标：** 能写代码 → 烧录 → 读到串口输出。**先跑通再理解。**

#### 第 1 步：环境就绪

- 本机装 esptool：`pip install esptool mpremote pyserial`
- 确认 ESP32 通过 USB 识别（`esptool.py chip_id`）
- 快闪 MicroPython 固件到 ESP32-S3（一次性的）
- 确认 `mpremote connect /dev/ttyUSB0` 能连上

#### 第 2 步：burn.py — 最小烧录脚本

写一个最简单的脚本，不经过 Agent：

```python
# burn.py — 最小可运行烧录脚本
import subprocess
import sys

code = sys.argv[1]
port = sys.argv[2]

# 保存代码到临时文件
with open("/tmp/blink.py", "w") as f:
    f.write(code)

# 烧录（MicroPython 模式）
result = subprocess.run(
    ["mpremote", "connect", port, "run", "/tmp/blink.py"],
    capture_output=True, text=True
)
print(result.stdout)
```

**验收：** 跑通 `python burn.py "print('hello')" COM3` → ESP32 串口输出 hello

#### 第 3 步：Agent 封装 compile_and_flash 工具

把 burn.py 封装成 LangChain @tool：
- 工具接受 `code` + `port` 参数
- 调用本地 subprocess 执行烧录
- 返回 stdout/stderr 给 Agent

**验收：** Agent 说"帮我烧录这段代码"，然后真的烧进去了。

#### 第 4 步：Agent 封装 read_serial + monitor_serial

```python
@tool
def read_serial(port: str) -> str:
    """用 pyserial 读取串口最近一次输出"""
```

**验收：** ESP32 跑着程序，Agent 能读到它打印的温度值。

#### 第 5 步：Web UI 加烧录面板

- 在聊天界面加"烧录进度"区域
- 显示串口实时日志（WebSocket 推送）
- 加"选择端口"下拉菜单（列出现有 COM 口）
- 加"停止烧录"按钮

---

### Phase 2-B：硬件调试闭环（核心能力）

**目标：** Agent 能自动调试——烧录报错 → 修改代码 → 再烧录。

#### 第 6 步：最小调试循环

```python
# Agent 工作流
1. 用户说"帮我点个灯"
2. generate_code → 生成代码
3. compile_and_flash → 烧录
4. read_serial → 输出日志
5. 如果日志有错误 → diagnose tool 分析 → 修改代码 → goto 3
6. 如果日志正常 → 告诉用户
```

**不增加新代码，只把现有的工具串联起来。**

**验收：** Agent 连续 3 次烧录成功，其中至少 1 次是第一次报错后自动修复的。

#### 第 7 步：硬件安全护栏

```python
# Agent 生成代码 → 输出前过护栏
code = agent.invoke("帮我读 MPU6050")
violations = hardware_guardrails(code, "esp32-s3")
if violations:
    prompt += f"\n【安全警告】以下内容可能有问题：{violations}"
    # 不阻塞输出，但用户能看到警告
```

**验收：** 故意让 Agent 生成错误引脚号，护栏能检出。

#### 第 8 步：外设元数据注入

完善 v1 Week 5 的 `hardware_config.yaml`：

```yaml
peripherals:
  UART1: {bus: APB2, base: "0x4000 4400", rx_pin: 9, tx_pin: 10}
  I2C0:  {bus: APB1, base: "0x4000 5400", sda_pin: 21, scl_pin: 22}
```

检索到用户问 UART 相关问题时，自动注入"UART1 挂在 APB2 总线上，基地址 0x40004400，RX=GPIO9，TX=GPIO10"到 prompt。

**验收：** 用户问"UART1 怎么配置"，Agent 给出的代码里引脚号是 9/10 而不是 1/2。

---

### Phase 2-C：WiFi OTA + 多芯片支持

#### 第 9 步：OTA 预置固件（一次性的）

提供一个 OTA 模板固件，用户第一次用 USB 烧录一次，后面就不用插线了：

```cpp
// arduino/templates/ota_firmware.cpp.jinja2
#include <ArduinoOTA.h>
#include <WiFi.h>

void setup() {
    WiFi.begin("SSID", "PASSWORD");
    ArduinoOTA.begin();
}

void loop() {
    ArduinoOTA.handle();
}
```

**验收：** 通过 `curl -F "firmware=@blink.bin" http://192.168.1.100/update` 成功 OTA 烧录。

#### 第 10 步：ota_flash 工具

```python
@tool
def ota_flash(code: str, esp_ip: str) -> dict:
    """编译代码并通过 WiFi OTA 推送"""
```

**验收：** Agent 通过 OTA 推送代码，不需要 USB。

#### 第 11 步：多芯片支持

新增芯片意味着需要：
- 对应 toolchain（`arduino-cli core install esp32:esp32` 等）
- 引脚 JSON 表
- OTA 模板（如果芯片支持）
- 硬件护栏规则

首批支持：
| 芯片 | 优先级 | 注意 |
|------|--------|------|
| ESP32-S3 | 🥇 你手上有的 | 首批验证目标 |
| ESP32-C3 | 🥇 RISC-V 架构 | 和 S3 共享大部分 toolchain |
| STM32F4 | 🥈 课程主力 | 需要 arm-none-eabi-gcc，不同烧录方式 |

---

### Phase 2-D：结构化比对 + 踩坑记录

#### 第 12 步：结构化比对工具升级

原有的 `compare_hardware_components` 改造：

```python
# 改造前：检索两篇文档 → LLM 比较（容易错）
# 改造后：读两个 JSON 引脚表 → Python diff → LLM 做文案

def compare_pins(chip_a_pins_json, chip_b_pins_json) -> dict:
    """精确比对，返回差异"""
```

#### 第 13 步：踩坑记录检索

追加知识库类型：`references/user-experiments/`

用户遇到过的 bug + 解决方案存成结构化笔记，Agent 检索时优先级最高。

```markdown
## bug: DHT11 第一次读数总是 0

原因：DHT11 上电后需要至少 1s 稳定时间，
    第一次读取前必须 sleep(1)

修复：在 read() 前加 time.sleep(1)
```

---

## 单硬件调试闭环验收标准

以下场景全部跑通 = v2 Phase 2-A + 2-B 完成：

```
场景 1：点灯
用户："写一个 LED 闪烁代码，GPIO2"
→ Agent 生成 → 烧录 → 灯闪了 ✓

场景 2：读传感器（需要自愈）
用户："读 DHT11，GPIO4"
→ Agent 生成代码 → 烧录 → 串口报错 "CRC check failed"
→ Agent 分析 → 修改代码（加延时）→ 再烧录
→ 串口输出 "Temp: 26.5°C, Hum: 65%" ✓

场景 3：硬件护栏拦截
用户："读 MPU6050"
→ Agent 生成 → 护栏检测出 I2C 地址 0x68 合法
→ 烧录成功 ✓

场景 4（故意测试）：护栏拦截非法配置
→ Agent 生成使用 GPIO48（ESP32-S3 只有 45 个 GPIO）
→ 护栏拦截，输出警告 ✓
```

---

## v2 技术栈说明

| 组件 | 用途 |
|------|------|
| **esptool** | ESP32 底层烧录工具（擦除/写入/校验） |
| **mpremote** | MicroPython 设备管理（运行脚本/REPL/文件操作） |
| **arduino-cli** | Arduino 命令行编译烧录 |
| **pyserial** | 串口数据读写 |
| **subprocess** | Python 调用外部工具链 |
| **Jinja2** | 代码模板引擎（v2 提前使用 Mini 版） |

### 依赖安装

```bash
# Python 依赖
pip install esptool mpremote pyserial

# Arduino CLI（如果需要支持 Arduino C）
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
arduino-cli core install esp32:esp32

# ESP-IDF（如果需要）
# 可选，体积较大（~1GB）
```

---

## v1 → v2 的无痛迁移

v1 代码结构不需要重构，v2 只是在上面加新目录和新工具：

```
v1 项目结构               v2 新增
─────────────────────    ─────────────────────
hardware-rag-agent/
├── backend/...           # 不变
├── agent/
│   ├── tools.py          # +compile_and_flash
│   ├── tools.py          # +read_serial
│   ├── tools.py          # +ota_flash
│   └── guardrails.py     # +hardware_guardrails
├── hardware/             # ← 新增
│   ├── burn.py           # USB 烧录脚本
│   ├── ota.py            # OTA 烧录脚本
│   ├── serial.py         # 串口读取
│   └── board_configs/    # 开发板配置文件
├── templates/            # ← 新增（Jinja2 代码模板）
│   ├── micropython/
│   └── arduino/
├── webui/...             # 不变
└── docs/
    └── hardware-setup.md # v2 硬件指南
```

---

## v2 总结

| 阶段 | 内容 | 完成线 |
|------|------|--------|
| Phase 2-A | USB 烧录链路 | Agent 生成代码 → 烧录到 ESP32 → 读串口 |
| Phase 2-B | 硬件调试闭环 | Agent 自动"生成→烧录→报错→改→再烧" |
| Phase 2-C | WiFi OTA + 多芯片 | 不插 USB 烧录 + 支持 3 款芯片 |
| Phase 2-D | 结构化比对 + 踩坑 | 精确 diff + 用户经验检索 |

**关键原则：**
- Phase 2-A 只做 USB 烧录，不做 OTA、不做多芯片、不做护栏
- **跑通之前不加新功能**
- 每个 Phase 完成后跑一遍 4 个场景的验收测试

---

## v2 防御性编码要点（工业级落地避坑清单）

> 以下来自工程级代码审查，每一条都是实际硬件调试中的真实坑点，不是假设。

---

### 🔴 坑点 1：串口独占死锁（致命）

**问题：** `pyserial` 读串口是 `while True` 阻塞循环。Agent 同步调用 `read_serial` 时，**整个推理线程被死死卡住**，用户新提问直接丢、FastAPI 超时崩溃。

**修复方案：**

```python
# ❌ 错误做法：Agent 直接同步读串口
@tool
def read_serial(port: str) -> str:
    ser = serial.Serial(port, 115200)
    while True:  # 死循环，Agent 永远回不来
        data = ser.readline()
        if data:
            return data

# ✅ 正确做法：Agent 只下达"开启监听"指令，立刻返回
@tool
def start_serial_monitor(port: str) -> dict:
    """启动串口监听（后台异步），Agent 不阻塞"""
    # 检查端口是否可用
    # 在 FastAPI 的 lifespan 中启动后台 task
    # 通过 WebSocket/SSE 推送到前端串口面板
    return {"success": True, "message": f"串口 {port} 已开始监听，请查看串口面板"}

# 前端串口面板独立接收数据流，不走 Agent 推理链
```

**检查清单：**
- [ ] `read_serial` 不是同步阻塞的
- [ ] 串口数据通过 WebSocket/SSE 推送到前端
- [ ] Agent 可以在监听同时接收用户新提问

---

### 🔴 坑点 2：esptool 烧录自动复位失败（高频）

**问题：** esptool 通过控制 DTR/RTS 引脚让 ESP32 进入下载模式。**很多开发板、劣质线、外接电容**都会导致自动复位失败，报错 `Failed to connect to ESP32: Timed out waiting for packet header`。

**修复方案：**

```python
# compile_and_flash 工具内部：
stderr = result.stderr

# 检测握手超时错误
if "Timed out waiting for packet header" in stderr:
    return {
        "success": False,
        "error": "BOOT_TIMEOUT",
        "user_action": "请按住板子上的 BOOT 键（或 GPIO0），然后松开再点重试。"
                    "如果多次失败，请更换 USB 数据线。"
    }

# Agent 收到 BOOT_TIMEOUT → 不自愈代码，直接告诉用户
# 只有收到编译错误时才触发自愈
```

**检查清单：**
- [ ] 编译错误（语法、类型）→ 触发自愈
- [ ] 烧录握手超时 → 引导用户手动按 BOOT 键，不触发自愈
- [ ] 超过 3 次握手失败 → 停止重试，直接提示换线/检查硬件

---

### 🔴 坑点 3：硬件安全护栏缺少运行时规则

**问题：** 当前护栏只检查静态配置（引脚、地址）。但**无延时的 `while True` 死循环**会让芯片高频空转发热，大功率继电器在极短循环里反复开关会导致物理损坏。

**修复方案：**

```python
# 在 hardware_guardrails 中增加硬规则：
import re

def check_infinite_loop_no_delay(code: str) -> str | None:
    """检测 while True/while 1 循环中是否有 sleep"""
    # 找到所有 while True/while 1 循环块
    loop_blocks = re.findall(r'while\s+(?:True|1)\s*:(.*?)(?=\n\S|\Z)', code, re.DOTALL)
    for block in loop_blocks:
        if 'sleep' not in block and 'time.sleep' not in block:
            return "检测到 while True 循环无延时，芯片会高频空转导致过热/外设损坏。" \
                   "请在循环体中添加 time.sleep_ms(10) 或以上。"
    return None

def check_pwm_frequency(pwm_value: int) -> str | None:
    """PWM 频率必须在芯片安全范围内"""
    if pwm_value < 20 or pwm_value > 40000:
        return f"PWM 频率 {pwm_value}Hz 超出安全范围（20~40000Hz）"
    return None
```

**检查清单：**
- [ ] `while True` 无延时 → 硬阻断，不让用户看到代码
- [ ] PWM 频率超出安全范围 → 硬阻断
- [ ] ADC 引脚电压超出 → 警告（不阻断）

---

### 🟡 坑点 4：ESP-IDF 首次编译超时

**问题：** ESP-IDF 首次编译 2-5 分钟，远超 LLM API Timeout（30s）和用户心理等待极限。

**修复方案：**

```python
# 编译工具返回任务 ID，不等结果
@tool
def start_compile_background(code: str, board: str) -> dict:
    """后台异步编译，返回任务 ID"""
    task_id = str(uuid.uuid4())
    # 启动后台进程
    BackgroundTaskQueue.submit(
        task_id=task_id,
        command=f"arduino-cli compile --fqbn {board} {code_path}",
        on_complete=callback_to_ws  # 完成后推送 WebSocket
    )
    return {"task_id": task_id, "message": "已创建工程，后台编译中，请在进度条查看"}

# 前端显示编译进度条，用户可继续提问（不阻塞）
```

**检查清单：**
- [ ] MicroPython `mpremote run`（快速，同步可接受）
- [ ] Arduino C 编译（中等，建议异步）
- [ ] ESP-IDF 编译（慢，**必须异步**）

---

### 🟡 坑点 5：错误自愈缓存"张冠李戴"

**问题：** `OSError: ENODEV` 可能是 MPU6050 引脚错了，也可能是 OLED 引脚错了。只凭错误文本匹配，Agent 会把 OLED 代码改成 MPU6050 的引脚。

**修复方案：**

```python
# 缓存结构必须强绑定上下文标签
error_cache_entry = {
    "error": "OSError: [Errno 19] ENODEV",
    "fix": "检查 SDA/SCL 引脚是否正确，ESP32-S3 默认 I2C: SDA=GPIO21, SCL=GPIO22",
    "context_tags": {
        "chip": "esp32-s3",
        "language": "micropython",
        "peripheral": "mpu6050",       # ← 绑定外设名
        "interface": "i2c",
        "port": "GPIO21/GPIO22"
    },
    "confidence": 0.8
}

# 匹配规则：只有当 chip + peripheral + language 全部匹配时才调用缓存
# 否则走 LLM 现场推理
```

**检查清单：**
- [ ] 缓存标签必须包含：chip、language、peripheral、interface
- [ ] 匹配规则：标签完全匹配才调用，否则走 LLM
- [ ] 缓存置信度 < 0.7 时不自动应用，改为"建议方案"

---

### 🟡 坑点 6：Jinja2 模板覆盖不到的长尾外设

**问题：** 模板系统对基础外设覆盖率高，但冷门传感器（工业 MODBUS 压力计等）没有模板，会退化为 LLM 裸写，成功率雪崩。

**修复方案：**

```python
# 当无模板匹配时，使用"兜底骨架模板"
FALLBACK_TEMPLATE = """
# ==========================================
# Hardware RAG Agent — 通用代码骨架
# 传感器：{{ sensor_name }}
# 芯片：{{ board }}
# 语言：{{ language }}
# ==========================================

import {{ standard_imports }}  # Agent 填充

# --- 标准配置 ---
CONFIG = {{ config_json }}  # 从 RAG 检索参数填充

# --- 标准错误处理 ---
class SensorError(Exception):
    pass

# [LLM CODE GENERATION START]
# ← LLM 在这里填写传感器特定的初始化和读取逻辑
# [LLM CODE GENERATION END]

# --- 标准主循环 ---
def main():
    while True:
        try:
            # [LLM CODE - 读取逻辑]
            pass
        except Exception as e:
            print("Error: {}".format(e))
        time.sleep(2)

if __name__ == "__main__":
    main()
```

**检查清单：**
- [ ] 有模板 → 用模板（成功率 ~95%）
- [ ] 无模板 → 用兜底骨架 + LLM 在标签内填写（成功率 ~70%）
- [ ] 兜底模板包含：标准错误处理 + 标准主循环 + `sleep` 强制

---

### 🟡 坑点 7：串口端口漂移

**问题：** USB 插拔/电脑休眠重启后 COM 口会变（COM5→COM6），硬编码 config.yaml 会大量报错。

**修复方案：**

```python
# 每次调用烧录工具前，先扫描可用端口
import serial.tools.list_ports

def get_available_ports() -> list[dict]:
    """扫描当前所有可用串口"""
    ports = serial.tools.list_ports.comports()
    return [{"port": p.device, "desc": p.description, "hwid": p.hwid} for p in ports]

def auto_detect_board_port() -> str:
    """自动识别 ESP32 端口（通过 USB VID:PID）"""
    ESP32_VIDS = {0x10C4, 0x1A86, 0x303A}  # 常见 ESP32 USB 芯片
    for port in get_available_ports():
        vid_pid = port["hwid"]
        if any(vid in vid_pid for vid in ESP32_VIDS):
            return port["port"]
    return None

# 端口不存在时 → 自动列出所有活跃端口供用户选择
```

**检查清单：**
- [ ] 烧录前自动扫描，config 端口不存在时提示用户选择
- [ ] 前端有"选择端口"下拉菜单（自动刷新）
- [ ] 多个开发板同时插着时，能通过 VID:PID 区分

---

### 🟡 坑点 8：GitHub Issue 垃圾数据入库

**问题：** 80% 的 Issue 是低质量的（"运行不了，求救"、不带日志），直接入库会污染知识库。

**修复方案：**

```python
# 入库门控规则（全部必须满足才入库）
GATEKEEPER = {
    "must_be_closed": True,              # 必须已关闭
    "must_have_label": ["bug-resolved", "verified", "fix"],  # 必须有明确标签
    "must_contain_error_log": True,       # 必须包含 Error 日志代码块
    "must_contain_fix": True,             # 必须包含修复代码块
    "must_not_be_duplicate": True,        # 去重（和已有缓存对比）
    "minimum_body_length": 100,           # 正文至少 100 字符
}

# 只有通过所有门控条件的 Issue 才能入库
```

**检查清单：**
- [ ] 只有 Closed + 有标签 + 有 Error + 有 Fix 的 Issue 才入库
- [ ] 去重：和已有知识库内容对比相似度，重复率 > 80% 跳过
- [ ] 入库前人工审核队列（可选）

---

### 🟡 坑点 9：多模型 Context 窗口不一致

**问题：** 不同模型的 Context 窗口不一样（8K ~ 256K+）。如果用户误用 8K 小模型跑大量 RAG + 工具链的 Prompt，会爆 Context。

**修复方案：**

不搞动态感知、不维护模型查找表、不精确计数。**就一条规则：**

> **系统的默认假设是 256K 窗口。用户选了支持更小窗口的模型，前端给出提示但不强制限制。**

```python
# 整个后端就这 3 行：
MAX_PROMPT = 204800  # 256K 的 80%
# 超过这个就触发 SummaryBufferMemory 压缩
# 不管用的是什么模型，统一按这个标准处理
```

在前端设置面板加一句话：

```
当前模型：DeepSeek（256K 窗口）✓
Agent 工作状态：prompt 预估 35K / 204.8K（17%）
当超过 80% 时系统自动压缩历史记忆。
```

**为什么不需要复杂实现：**
- 2026 年的主流模型（DeepSeek、GPT-4o、Claude、Qwen2.5、Llama3.1）最小也是 128K
- 2023 年的老模型（Llama 2 8K）正常人不会用
- 如果用户非要用 8K 模型跑 Agent，前端提示"不推荐，建议换 128K+ 模型"就够了
- 不需要后端做任何动态裁剪——用户对自己的选择负责

**检查清单：**
- [ ] 后端统一压缩阈值 204800（256K × 80%）
- [ ] 前端显示当前模型窗口 + prompt 占用量
- [ ] 前端提示：低于 128K 时弹出"不推荐"警告（不强制限制）

---

### 🟡 坑点 10：多板共存串口误烧录

**问题：** 用户电脑可能同时插着多块开发板（ESP32-S3 + 常规 ESP32 + USB 转 TTL）。esptool 只认端口不认芯片，**盲猜端口烧录会直接冲掉另一块板的固件**。

**修复方案：**

```python
# ❌ 错误做法：Agent 自动选端口
port = scan_and_pick_first_esp32()  # 可能选错

# ✅ 正确做法：用户手动确认，Agent 只读用户选的端口

# 前端流程：
# 1. Agent 调用扫描端口工具
# 2. 前端弹出下拉菜单，列出所有可用端口（含芯片型号信息）
# 3. 用户手动选择
# 4. 每次烧录前再次弹出确认：确认烧录到 {port}？(Y/N)
# 5. 用户确认后才执行 esptool

# compile_and_flash 工具：
@tool
def compile_and_flash(code: str, board: str, port: str, confirm: bool = False) -> dict:
    if not confirm:
        return {"success": False, "error": "PORT_UNCONFIRMED",
                "message": f"检测到端口 {port}，请在前端确认后再烧录。"}
    # ... 执行烧录
```

**按钮文案：**
> ⚠️ 即将向 `COM5`（ESP32-S3）烧录固件，确认？
> [取消] [确认烧录]

**检查清单：**
- [ ] 烧录前必须用户手动确认端口
- [ ] 扫描端口时显示芯片型号（通过 USB VID:PID 识别）
- [ ] 多块板同时插着时可以区分
- [ ] 每次烧录弹出确认，不跳过

---

### 🟢 坑点 11：Strapping 引脚物理冲突

**问题：** ESP32 有 6 个 Strapping 引脚（GPIO0/2/3/5/12/15），上电电平决定启动模式。Agent 把外设接到这些引脚上会导致芯片死机。**接线图看着完美，硬件点不亮。**

**修复方案：** 加到 `hardware_guardrails`。

```python
# 已在 hardware_guardrails 中实现（见前面章节）
# 检测到 Strapping 引脚被使用 → 硬阻断并提示换引脚
```

**检查清单：**
- [ ] Strapping 引脚检查写入 hardware_guardrails
- [ ] Blocked（硬阻断），不是 warning
- [ ] 接线可视化工具（v3 visualize_wiring）也标注 Strapping 引脚

---

## v2 已知坑点验收清单（全部在里程碑前检查）

| # | 坑点 | 严重程度 | 落地阶段 | 防御措施已写入代码？ |
|---|------|---------|---------|-------------------|
| 1 | 串口独占死锁 | 🔴致命 | Phase 2-A | [ ] |
| 2 | esptool 自动复位失败 | 🔴致命 | Phase 2-A | [ ] |
| 3 | 无延时 while True | 🔴致命 | Phase 2-B | [ ] |
| 4 | ESP-IDF 编译超时 | 🟡重要 | Phase 3-A | [ ] |
| 5 | 错误缓存张冠李戴 | 🟡重要 | Phase 3-A | [ ] |
| 6 | Jinja2 长尾兜底 | 🟡重要 | Phase 3-A | [ ] |
| 7 | 串口端口漂移 | 🟡重要 | Phase 2-A | [ ] |
| 8 | Issue 垃圾数据 | 🟡重要 | Phase 3-C | [ ] |
| 9 | Context 窗口不一致 | 🟡重要 | v1 Week 1 | [ ] |
| 10 | 多板共存误烧录 | 🟡重要 | Phase 2-A | [ ] |
| 11 | Strapping 引脚物理冲突 | 🟢一般 | Phase 2-B | [ ] |

**跨平台说明：** v2 首发仅支持 Windows 10/11。macOS / Linux 适配在 v2 稳定后进行。
代码架构上为跨平台预留（使用 `platform.system()` 判断），但不投入精力测试。
