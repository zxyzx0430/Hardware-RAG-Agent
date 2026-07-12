# DOCX 入库测试报告

测试 KB: `kb-87d3f3af`
分块策略: hybrid / small_chunk_size=800
测试时间: 2026-06-27 23:09:46
测试文件数: 10

## 概览

| 文件 | chunks | 总字符 | min | max | avg | <100 | <50 | 空 | 代码块 | 表格 | 标题 | 截断代码 | 拆分表格 | 纯符号 |
|------|--------|--------|-----|-----|-----|------|-----|----|--------|------|------|---------|---------|--------|
| 10-stm32f103-reference-manual.docx | 16 | 7559 | 102 | 1170 | 472.4 | 0 | 0 | 0 | 3 | 6 | 15 | 0 | 0 | 0 |
| 11-esp32c3-datasheet-brief.docx | 10 | 3767 | 144 | 787 | 376.7 | 0 | 0 | 0 | 0 | 2 | 8 | 0 | 0 | 0 |
| 12-arduino-uno-quickstart.docx | 14 | 7272 | 42 | 689 | 519.4 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 13-raspberrypi-4-product-brief.docx | 9 | 3073 | 252 | 434 | 341.4 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 |
| 14-plc-s7-1200-manual.docx | 14 | 6301 | 111 | 759 | 450.1 | 0 | 0 | 0 | 2 | 8 | 13 | 0 | 0 | 0 |
| 15-mpu6050-tutorial-mixed-lang.docx | 8 | 5868 | 143 | 2013 | 733.5 | 0 | 0 | 0 | 3 | 2 | 5 | 0 | 0 | 0 |
| 16-jetson-nano-user-guide.docx | 9 | 3150 | 256 | 489 | 350.0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 |
| 17-hc-sr04-datasheet-minimal.docx | 5 | 913 | 125 | 246 | 182.6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 18-ws2812b-spec-table-heavy.docx | 10 | 1777 | 59 | 358 | 177.7 | 2 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| 19-legacy-chaotic-manual.docx | 6 | 1437 | 90 | 465 | 239.5 | 1 | 0 | 0 | 0 | 2 | 5 | 0 | 0 | 0 |

## 汇总统计

- 总 chunks: **101**
- 短 chunks (<100 chars): **4** (4.0%)
- 极短 chunks (<50 chars): **1** (1.0%)
- 空 chunks: **0**
- 代码块截断: **0**
- 表格跨 chunk 拆分: **0**
- 纯符号 chunks: **0**

## 每个文件详细分析

### 10-stm32f103-reference-manual.docx

- chunks: 16
- 字符总数: 7559 | min: 102 | max: 1170 | avg: 472.4
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 3 | 截断: 0 (indices: [])
- 表格 chunks: 6 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 15
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
STM32F103 系列参考手册
# 第1章 概述

STM32F103 系列微控制器基于 ARM Cortex-M3 内核，采用 32 位 RISC 架构，最高工作频率 72MHz，内置 Flash 存储器和 SRAM。该系列芯片专为高性能、低功耗、实时性要求较高的嵌入式应用而设计，广泛用于工业控制、消费电子、通信设备和医疗仪器等领域。
```

**末 chunk 预览**:
```
## 5.3 UART 配置

以下代码演示如何配置 USART1 为 115200 波特率的串口通信。
```
void USART1_Configuration(void)
{
    GPIO_InitTypeDef GPIO_InitStruct;
    USART_InitTypeDef USART_InitStruct;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_USART1, ENABLE);

    /* PA9 = TX, alternate function push-pull *
```

### 11-esp32c3-datasheet-brief.docx

- chunks: 10
- 字符总数: 3767 | min: 144 | max: 787 | avg: 376.7
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 2 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 8
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
ESP32-C3 Datasheet Brief
# 1. Overview

ESP32-C3 is a single-core Wi-Fi and Bluetooth LE (BLE 5.0) microcontroller based on the 32-bit RISC-V architecture. It operates at up to 160 MHz and integrates 400 KB SRAM and 384 KB ROM. The chip is designed for IoT applications requiring low power consumptio
```

**末 chunk 预览**:
```
## 5.2 Deep Sleep and Wake-up

The ESP32-C3 can enter deep sleep mode to reduce power consumption. Wake-up sources include RTC timer, GPIO (EXT0/EXT1), and UART. The following code shows how to enter deep sleep and wake up after 10 seconds.

#include "esp_sleep.h"

void app_main(void) {
    esp_slee
```

### 12-arduino-uno-quickstart.docx

- chunks: 14
- 字符总数: 7272 | min: 42 | max: 689 | avg: 519.4
- 短 chunks (<100): 1 (indices: [0])
- 极短 chunks (<50): 1
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 0 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 0
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
ARDUINO UNO QUICK START GUIDE
INTRODUCTION
```

**末 chunk 预览**:
```
If your sketch runs but behaves unexpectedly check your wiring and make sure components are connected to the correct pins. Verify that LEDs are oriented correctly with the longer leg being the anode positive. Check that resistors have appropriate values to limit current. Use the Serial Monitor in th
```

### 13-raspberrypi-4-product-brief.docx

- chunks: 9
- 字符总数: 3073 | min: 252 | max: 434 | avg: 341.4
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 0 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 8
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
Raspberry Pi 4 Model B Product Brief
# 1. Introduction

The Raspberry Pi 4 Model B is the latest generation of the popular single-board computer from the Raspberry Pi Foundation. It delivers a significant performance increase over previous generations while maintaining the same compact form factor a
```

**末 chunk 预览**:
```
## 6.1 Supported OS

The Raspberry Pi 4 officially supports Raspberry Pi OS (formerly Raspbian), a Debian-based Linux distribution optimized for the Raspberry Pi hardware. Additionally, Ubuntu, Manjaro, and several other Linux distributions provide official ARM 64-bit images. Windows 10 IoT Core and
```

### 14-plc-s7-1200-manual.docx

- chunks: 14
- 字符总数: 6301 | min: 111 | max: 759 | avg: 450.1
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 2 | 截断: 0 (indices: [])
- 表格 chunks: 8 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 13
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
SIMATIC S7-1200 PLC 系统手册
# 1. 系统概述

SIMATIC S7-1200 是西门子推出的一款紧凑型可编程逻辑控制器（PLC），专为中小型自动化系统设计。该系列 PLC 集成了 CPU、数字量和模拟量 I/O、高速计数器、运动控制功能以及PROFINET 通信接口，适用于离散控制、过程控制和运动控制等多种应用场景。
```

**末 chunk 预览**:
```
## 6.3 PROFINET IO 通讯配置

- 以下为 PROFINET IO 设备配置步骤：

- 在 TIA Portal 中添加 PROFINET IO 设备到网络视图

- 配置 IO 设备名称和 IP 地址（自动分配或手动设置）

- 为 IO 设备配置输入输出数据区（Input/Output 地址映射）

- 设置更新周期（默认 1ms，可根据需要调整）

- 编译并下载项目到 CPU

- 在在线模式下检查 IO 设备的连接状态和诊断信息
```

### 15-mpu6050-tutorial-mixed-lang.docx

- chunks: 8
- 字符总数: 5868 | min: 143 | max: 2013 | avg: 733.5
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 3 | 截断: 0 (indices: [])
- 表格 chunks: 2 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 5
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
MPU6050 六轴传感器教程
# 1. 模块介绍 Module Overview

MPU6050 是 InvenSense 公司推出的六轴运动跟踪器件，集成了三轴加速度计和三轴陀螺仪。The MPU6050 integrates a 3-axis gyroscope and a 3-axis accelerometer on a single silicon die. It communicates via I2C interface with a clock speed up to 400 kHz. The module operates at 3.3V logic level and 
```

**末 chunk 预览**:
```
# 6. 常见问题 FAQ

Q: 读取的数据全是 0 或 -1？
A: 检查 I2C 接线是否正确，确认 SCL 和 SDA 没有接反。运行 I2C 扫描程序确认设备地址。如果扫描不到设备，可能是上拉电阻缺失或电源问题。

Q: 陀螺仪数据有漂移？
A: 陀螺仪存在固有零漂。开机后保持静止 5 秒，读取当前陀螺仪输出作为零偏，后续读数减去零偏即可。高级方案可使用卡尔曼滤波。

Q: 加速度计角度有噪声？
A: 加速度计容易受振动影响。建议使用互补滤波器或卡尔曼滤波器融合加速度计和陀螺仪数据，可获得稳定的姿态角估计。
```

### 16-jetson-nano-user-guide.docx

- chunks: 9
- 字符总数: 3150 | min: 256 | max: 489 | avg: 350.0
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 0 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 8
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
NVIDIA Jetson Nano Developer Kit User Guide
# 1. Introduction

The NVIDIA Jetson Nano Developer Kit is a small, powerful computer that lets you run multiple neural networks in parallel for applications like image classification, object detection, segmentation, and speech processing. It is designed f
```

**末 chunk 预览**:
```
## 8.1 Display Output

The Jetson Nano provides two display output options. There is one HDMI 2.0 port supporting up to 4K resolution at 60 Hz and one DisplayPort 1.2 connector. Only one display can be active at a time. For headless operation you can connect via SSH over the network after the initia
```

### 17-hc-sr04-datasheet-minimal.docx

- chunks: 5
- 字符总数: 913 | min: 125 | max: 246 | avg: 182.6
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 0 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 0
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
HC-SR04 超声波测距模块说明书
产品简介：HC-SR04 超声波测距模块可提供 2cm 到 400cm 的非接触式距离测量功能，测距精度可达 3mm。模块包含超声波发射器、接收器与控制电路。该模块性能稳定，使用方便，广泛应用于机器人避障、液位检测、距离测量等场景。模块工作电压为 5V 直流，工作电流约 15mA，工作频率为 40kHz。模块设有 4 个引脚，分别为 VCC 电源正极、GND 电源地、Trig 触发信号输入和 Echo 回响信号输出。
```

**末 chunk 预览**:
```
注意事项：模块测距时需避免直接照射到斜面或过小物体，否则可能无法正确接收回波。建议两次测量间隔大于 60 毫秒以避免前次发射的回波干扰。模块的 Trig 和 Echo 引脚为 5V 电平，如连接 3.3V 微控制器需注意电平转换。多个模块同时使用时应避免超声波互相干扰，建议分时触发。模块不宜在强风环境中使用，风速会影响声速从而影响测量精度。
```

### 18-ws2812b-spec-table-heavy.docx

- chunks: 10
- 字符总数: 1777 | min: 59 | max: 358 | avg: 177.7
- 短 chunks (<100): 2 (indices: [0, 5])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 4 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 0
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
WS2812B Intelligent LED Specification
产品概述 Product Overview
```

**末 chunk 预览**:
```
应用注意事项 Application Notes
在 5V 供电时建议在 DIN 串联一个 300-500 欧电阻以防止信号反射。当级联数量超过 256 颗时，建议在数据线中间增加电平驱动器（如 74HCT125）以增强信号。电源线建议每隔 50 颗 LED 增加一个 1000uF 电容滤波。
```

### 19-legacy-chaotic-manual.docx

- chunks: 6
- 字符总数: 1437 | min: 90 | max: 465 | avg: 239.5
- 短 chunks (<100): 1 (indices: [0])
- 极短 chunks (<50): 0
- 空 chunks: 0
- 代码块 chunks: 0 | 截断: 0 (indices: [])
- 表格 chunks: 2 | 跨 chunk 拆分: 0 (indices: [])
- 标题 chunks: 5
- 纯符号 chunks: 0 (indices: [])

**首 chunk 预览**:
```
工业温度控制器 使用手册
# 1. 概述

本手册适用于 XMT-7000 系列工业温度控制器。该控制器采用 32 位微处理器，支持 PID 控制、自整定、多种热电偶和热电阻输入。
```

**末 chunk 预览**:
```
# 8. 附录

附录 A：热电偶分度表（K型）
   0 C -> 0.000 mV
   100 C -> 4.096 mV
   200 C -> 8.138 mV
   300 C -> 12.209 mV
   400 C -> 16.397 mV
   500 C -> 20.644 mV
   600 C -> 24.906 mV
   700 C -> 29.129 mV
   800 C -> 33.275 mV
   900 C -> 37.326 mV
   1000 C -> 41.276 mV
```
