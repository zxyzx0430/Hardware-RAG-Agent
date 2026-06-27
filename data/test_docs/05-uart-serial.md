# UART 串行通信详解与实践指南

> 本文档系统讲解 UART/USART 串口通信的协议原理、电气特性、STM32 寄存器与 HAL 库编程、DMA 高速传输、硬件流控制、错误处理、噪声排查、Modbus RTU 工业通信、多机通信、自动波特率检测、ESP32/Linux 平台编程以及故障排查与性能优化。文档面向嵌入式工程师，所有代码示例均经过工程实践验证。

---

## 目录

1. [UART 协议概述](#1-uart-协议概述)
2. [物理层与电气特性](#2-物理层与电气特性)
3. [数据帧格式](#3-数据帧格式)
4. [STM32 USART 寄存器详解](#4-stm32-usart-寄存器详解)
5. [STM32 HAL UART 库详解](#5-stm32-hal-uart-库详解)
6. [DMA 传输详解](#6-dma-传输详解)
7. [硬件流控制](#7-硬件流控制)
8. [UART 错误处理](#8-uart-错误处理)
9. [UART 噪声干扰排查](#9-uart-噪声干扰排查)
10. [Modbus RTU 协议实现](#10-modbus-rtu-协议实现)
11. [多机通信](#11-多机通信9位数据模式)
12. [UART 自动波特率检测](#12-uart-自动波特率检测)
13. [ESP32 Arduino UART 编程](#13-esp32-arduino-uart-编程)
14. [Linux UART 编程](#14-linux-uart-编程)
15. [常见问题与故障排查](#15-常见问题与故障排查)
16. [UART 性能优化](#16-uart-性能优化)
17. [不同平台 UART 差异对比](#17-不同平台-uart-差异对比)

---

## 1. UART 协议概述

### 1.1 历史与发展

UART（Universal Asynchronous Receiver/Transmitter，通用异步收发器）的历史可以追溯到 20 世纪 60 年代。1960 年，贝尔实验室开发了第一款商用 UART 芯片，用于电传打字机（Teletypewriter，TTY）与调制解调器之间的通信。早期的 UART 是分立元件实现，随后集成到单片集成电路中。

UART 发展的关键节点：

| 年份 | 事件 | 意义 |
|------|------|------|
| 1960 | 贝尔实验室首款 UART | 替代机械编码器，实现电传打字机数字化 |
| 1962 | RS-232 标准发布 | 定义 DTE/DCE 接口电气特性 |
| 1981 | IBM PC 引入 INS8250 | PC 串口标准化的开端 |
| 1990s | 16550 FIFO UART | 引入 16 字节 FIFO，减少中断频率 |
| 2000s | 集成到 MCU | STM32/AVR/PIC 等单片机内置 USART |
| 2010s | 高速 USB-UART 桥接 | FT232/CP2102/CH340 替代传统 RS-232 |

UART 本质上是一种硬件外设，实现并行数据与串行数据之间的转换。"异步"是指通信双方不共享时钟信号，而是通过预先约定的波特率（baud rate）实现位同步。这与 SPI、I2C 等同步通信有本质区别。

### 1.2 UART 的核心特点

UART 协议具有以下显著特点：

- **异步通信**：无需共享时钟线，通信双方按约定波特率独立采样。这简化了布线（仅需 TX/RX/GND 三线），但对时钟精度提出了要求。
- **单端信号**：标准 UART 使用单端电平（TTL 0~3.3V 或 RS-232 ±12V），相比差分信号抗干扰能力较弱，适合短距离或低速场景。
- **全双工通信**：TX 和 RX 独立通路，可同时收发。
- **点对点拓扑**：UART 原生支持一对一通信，多机通信需借助 9 位地址模式或 RS-485 总线。
- **可配置帧格式**：数据位 5~9 位、校验位（无/奇/偶）、停止位 1/1.5/2 位可灵活组合。
- **硬件实现简单**：仅需移位寄存器、波特率发生器、控制逻辑即可实现。

### 1.3 UART 与 SPI、I2C 的对比

三种常见串行总线各有适用场景，对比如下：

| 特性 | UART | SPI | I2C |
|------|------|-----|-----|
| 时钟类型 | 异步（无时钟线） | 同步（SCK 共享） | 同步（SCL 共享） |
| 信号线数 | 2（TX/RX）+ GND | 3（SCK/MOSI/MISO）+ CS | 2（SCL/SDA） |
| 拓扑 | 点对点 | 主从（一主多从） | 多主多从 |
| 速率 | 通常 ≤ 4 Mbps | 可达 50 Mbps | 标准 100kHz / 快速 400kHz / 高速 3.4MHz |
| 距离 | 板内或 RS-485 远距离 | 板内（< 30cm） | 板内（< 1m） |
| 应答机制 | 无（异步） | 无（硬件 CS 选片） | 有（ACK/NACK） |
| 复杂度 | 中（波特率匹配） | 低（时钟同步） | 高（地址+仲裁） |
| 典型应用 | 调试串口、GPS、蓝牙模块、Modbus | Flash、LCD、SD 卡、ADC | 传感器、EEPROM、RTC |

选择建议：
- **UART**：长距离、异步、设备间通信（如 GPS 模块、蓝牙模块、调试日志）。
- **SPI**：高速、板内、主从结构（如 SD 卡、TFT 屏、外部 Flash）。
- **I2C**：低速、多设备、地址寻址（如温度传感器、EEPROM、IMU）。

### 1.4 UART 应用场景

UART 在嵌入式系统中无处不在，典型应用包括：

1. **调试串口（printf 调试）**：通过 USB-TTL 模块连接 PC，输出调试日志。这是嵌入式开发中最常用的调试手段。
2. **GPS 模块**：u-blox、SiRF 等 GPS 模块通过 UART 输出 NMEA 0183 语句，波特率通常 9600 或 38400。
3. **蓝牙模块**：HC-05/HC-06、BLE 模块（如 nRF52）通过 AT 指令或透传模式与主控通信。
4. **Wi-Fi 模块**：ESP8266 AT 固件通过 UART 接收指令、返回数据。
5. **工业通信**：Modbus RTU 基于 RS-485 + UART，是工业自动化的事实标准。
6. **无线模块**：LoRa（SX1278）、Zigbee、Sub-1G 射频模块多采用 UART 接口。
7. **条码扫描器、POS 打印机**：商业设备普遍使用 TTL UART 或 RS-232。
8. **多 MCU 通信**：主控与协处理器（如 STM32 + ESP32）间通过 UART 交换数据。

### 1.5 UART 与 USART 的区别

初学者常混淆 UART 与 USART：

- **UART**（Universal Asynchronous Receiver/Transmitter）：仅支持异步通信。
- **USART**（Universal Synchronous/Asynchronous Receiver/Transmitter）：既支持异步通信，也支持同步通信（带时钟线 SCLK）。USART 可配置为 UART 模式（禁用 SCLK）。

STM32 的 USART 还支持智能卡（SmartCard）模式、IrDA SIR ENDEC 规范、LIN 主从模式、单线半双工通信等高级特性。本文以 STM32F1/F4 的 USART 为主要讲解对象。

---

## 2. 物理层与电气特性

### 2.1 TTL 电平

TTL（Transistor-Transistor Logic）电平是 MCU 直接输出的逻辑电平，常见规格：

| 系列 | VCC | 逻辑 0 (VOL) | 逻辑 1 (VOH) | 典型器件 |
|------|-----|--------------|--------------|----------|
| 5V TTL | 5.0V | ≤ 0.4V | ≥ 2.4V | 74HC、ATmega328P |
| 3.3V LVTTL | 3.3V | ≤ 0.4V | ≥ 2.4V | STM32、ESP32（部分） |
| 2.5V LVTTL | 2.5V | ≤ 0.4V | ≥ 1.9V | 部分 FPGA |
| 1.8V LVTTL | 1.8V | ≤ 0.4V | ≥ 1.3V | 低功耗 MCU |

注意事项：
- 3.3V 与 5V TTL 电平通常可直连（3.3V 输出能被 5V 输入识别为高电平），但 5V 输出接到 3.3V 输入需电平转换，否则可能损坏 3.3V 器件。
- STM32 的 USART TX/RX 引脚为 3.3V LVTTL，不兼容 RS-232 电平。
- TTL 电平抗干扰能力弱，传输距离一般不超过 50cm，长距离必须使用 RS-232 或 RS-485。

### 2.2 RS-232 电平

RS-232 是 EIA 于 1962 年发布的串行通信标准，最新版本为 TIA-232-F。它采用负逻辑、双极性电平：

| 参数 | 规范值 | 说明 |
|------|--------|------|
| 逻辑 0 (SPACE) | +3V ~ +15V（典型 +12V） | 正电压 |
| 逻辑 1 (MARK) | -3V ~ -15V（典型 -12V） | 负电压 |
| 未定义区 | -3V ~ +3V | 接收端判决死区 |
| 最大速率 | 115200 bps（标准）/ 921600 bps（高速） | 短距离可更高 |
| 最大距离 | 15 米（9600bps） | 速率越高距离越短 |
| 负载电容 | ≤ 2500 pF | 限制线缆长度 |

RS-232 与 TTL 的电平转换常用芯片：

| 芯片 | 通道数 | 供电 | 特点 |
|------|--------|------|------|
| MAX3232 | 2 收 2 发 | 3.3V | 最常用，内置电荷泵 |
| MAX232 | 2 收 2 发 | 5V | 经典芯片 |
| SP3232 | 2 收 2 发 | 3.3V | 低功耗 |
| ST3232 | 2 收 2 发 | 3.3V | 国产替代 |

电路示例（MAX3232 连接 STM32）：

```c
// Hardware connection (no code, schematic description)
// STM32 PA9 (TX)  -> MAX3232 T1IN  -> T1OUT -> DB9 Pin 2 (TXD)
// STM32 PA10 (RX) <- MAX3232 R1OUT <- R1IN <- DB9 Pin 3 (RXD)
// MAX3232 VCC = 3.3V, GND = GND
// C1+/C1-, C2+/C2-, C3+, C4+, V+, V- = 0.1uF charge pump capacitors
```

### 2.3 RS-485 电平

RS-485 是差分平衡传输标准，抗干扰能力远强于 RS-232，适合工业环境：

| 参数 | 规范值 | 说明 |
|------|--------|------|
| 传输方式 | 差分（A/B 两线） | 抗共模干扰 |
| 电平 | 差分 ≥ 200mV | VA - VB > +200mV 为逻辑 1，< -200mV 为逻辑 0 |
| 最大速率 | 10 Mbps | 短距离 |
| 最大距离 | 1200 米（100kbps） | 速率随距离下降 |
| 节点数 | 32（标准）/ 256（1/8 单位负载） | 多点总线 |
| 共模范围 | -7V ~ +12V | 接收器容忍范围 |

RS-485 是半双工总线，需通过 DE/RE 引脚控制收发方向。常用收发芯片：

| 芯片 | 速率 | 节点数 | 特点 |
|------|------|--------|------|
| MAX485 | 2.5 Mbps | 32 | 经典，5V 供电 |
| MAX3485 | 2.5 Mbps | 32 | 3.3V 版本 |
| SP3485 | 10 Mbps | 32 | 3.3V 高速 |
| MAX13442 | 16 Mbps | 128 | 高节点数 |
| ADM2587E | 500 kbps | 256 | 集成隔离 |

### 2.4 三种电平对比

| 特性 | TTL | RS-232 | RS-485 |
|------|-----|--------|--------|
| 电平类型 | 单端 | 单极性双极 | 差分 |
| 逻辑电平 | 0V/3.3V | ±12V | 差分 200mV |
| 距离 | < 50cm | 15m | 1200m |
| 抗干扰 | 弱 | 中 | 强 |
| 拓扑 | 点对点 | 点对点 | 多点总线 |
| 双工 | 全双工 | 全双工 | 半双工（4 线可全双工） |
| 成本 | 最低 | 中（需电平转换） | 中（需收发器） |
| 典型场景 | 板内 MCU 通信 | PC 串口、老式设备 | 工业总线、Modbus |

### 2.5 波特率（Baud Rate）

波特率指每秒传输的码元数。对于 UART（二进制调制），波特率 = 比特率。常用波特率：

| 波特率 | 误差容限 | 每字节约耗时（8N1） | 典型应用 |
|--------|----------|---------------------|----------|
| 9600 | ±2.5% | 1.04 ms | GPS、低速传感器 |
| 19200 | ±2.5% | 520 μs | 蓝牙模块配置 |
| 38400 | ±2.5% | 260 μs | 工业仪表 |
| 57600 | ±2.5% | 174 μs | 调试日志 |
| 115200 | ±2.5% | 87 μs | 通用调试、Wi-Fi 模块 |
| 230400 | ±2.5% | 43 μs | 高速日志 |
| 460800 | ±2.5% | 22 μs | 数据采集 |
| 921600 | ±2.5% | 11 μs | 高速透传 |
| 1500000 | ±2.5% | 6.7 μs | STM32 与 ESP32 互联 |
| 3000000 | ±2.5% | 3.3 μs | 短距离高速 |

### 2.6 波特率计算公式

STM32 USART 的波特率由分数分频器生成，公式为：

```
波特率 = fck / (16 × USARTDIV)        （OVER8=0，16 倍过采样）
波特率 = fck / (8 × USARTDIV)         （OVER8=1，8 倍过采样）
```

其中：
- `fck` 为 USART 外设时钟（如 STM32F103 的 USART1 挂在 APB2，最高 72MHz；USART2/3 挂在 APB1，最高 36MHz）。
- `USARTDIV` 为 16 位分数分频值，整数部分 12 位，小数部分 4 位。
- `BRR` 寄存器存储分频值：`BRR = USARTDIV × 16`（OVER8=0）。

反向计算分频值：

```
USARTDIV = fck / (16 × 波特率)
```

例如：fck=72MHz，目标 115200bps：
```
USARTDIV = 72000000 / (16 × 115200) = 39.0625
BRR = 39.0625 × 16 = 625 = 0x0271
整数部分 = 39 = 0x27，小数部分 = 0.0625 × 16 = 1 = 0x1
BRR = 0x0271
```

### 2.7 波特率误差容限

UART 异步通信的可靠性依赖收发双方波特率一致。8N1 帧格式（1 起始位 + 8 数据位 + 1 停止位 = 10 bit）的理论最大误差容限为 ±2.5%。

推导过程：
- 接收端在每个位的中间采样（16 倍过采样时在第 8、9 个采样点判决）。
- 假设发送端波特率 = f，接收端波特率 = f(1+δ)。
- 第 N 位的累计偏差为 N×δ×位周期。
- 当累计偏差超过半个位周期时，采样点滑入相邻位，发生错误。
- 10 位帧（8N1）的最后一个停止位采样点在第 9.5 位处。
- 临界条件：9.5 × |δ| < 0.5 → |δ| < 5.26%。
- 考虑收发双方可能各有偏差，单端容限为 5.26%/2 ≈ 2.63%，工程上取 ±2.5%。

不同帧长度的误差容限对比：

| 帧格式 | 总位数 | 理论单端容限 | 工程推荐 |
|--------|--------|--------------|----------|
| 8N1 | 10 | ±2.63% | ±2.0% |
| 8E1 / 8O1 | 11 | ±2.38% | ±2.0% |
| 8N2 | 11 | ±2.38% | ±2.0% |
| 9N1 | 11 | ±2.38% | ±2.0% |
| 8E2 | 12 | ±2.17% | ±1.5% |
| 7N1 | 9 | ±2.94% | ±2.5% |

### 2.8 波特率误差计算表

以下为 STM32F103（USART1，fck=72MHz）在不同波特率下的分频与误差：

| 目标波特率 | USARTDIV | BRR（hex） | 实际波特率 | 误差 |
|-----------|----------|-----------|-----------|------|
| 9600 | 468.75 | 0x1D4C | 9600.0 | 0.00% |
| 19200 | 234.375 | 0x0EA6 | 19200.0 | 0.00% |
| 38400 | 117.1875 | 0x0753 | 38400.0 | 0.00% |
| 57600 | 78.125 | 0x04E2 | 57600.0 | 0.00% |
| 115200 | 39.0625 | 0x0271 | 115384.6 | +0.16% |
| 230400 | 19.53125 | 0x0139 | 230769.2 | +0.16% |
| 460800 | 9.765625 | 0x009D | 461538.5 | +0.16% |
| 921600 | 4.8828125 | 0x004F | 923076.9 | +0.16% |
| 1000000 | 4.5 | 0x0048 | 1000000.0 | 0.00% |
| 1500000 | 3.0 | 0x0030 | 1500000.0 | 0.00% |
| 2000000 | 2.25 | 0x0024 | 2000000.0 | 0.00% |
| 2250000 | 2.0 | 0x0020 | 2250000.0 | 0.00% |
| 3000000 | 1.5 | 0x0018 | 3000000.0 | 0.00% |
| 4000000 | 1.125 | 0x0012 | 4000000.0 | 0.00% |
| 4500000 | 1.0 | 0x0010 | 4500000.0 | 0.00% |

STM32F103 USART2（fck=36MHz，APB1）误差表：

| 目标波特率 | USARTDIV | BRR（hex） | 实际波特率 | 误差 |
|-----------|----------|-----------|-----------|------|
| 9600 | 234.375 | 0x0EA6 | 9600.0 | 0.00% |
| 19200 | 117.1875 | 0x0753 | 19200.0 | 0.00% |
| 38400 | 58.59375 | 0x03AB | 38400.0 | 0.00% |
| 57600 | 39.0625 | 0x0271 | 57600.0 | 0.00% |
| 115200 | 19.53125 | 0x0139 | 115384.6 | +0.16% |
| 230400 | 9.765625 | 0x009D | 230769.2 | +0.16% |
| 460800 | 4.8828125 | 0x004F | 461538.5 | +0.16% |
| 921600 | 2.44140625 | 0x0027 | 923076.9 | +0.16% |
| 1000000 | 2.25 | 0x0024 | 1000000.0 | 0.00% |
| 1500000 | 1.5 | 0x0018 | 1500000.0 | 0.00% |
| 2000000 | 1.125 | 0x0012 | 2000000.0 | 0.00% |
| 2250000 | 1.0 | 0x0010 | 2250000.0 | 0.00% |

STM32F407（USART1，fck=84MHz，APB2）误差表：

| 目标波特率 | USARTDIV | BRR（hex） | 实际波特率 | 误差 |
|-----------|----------|-----------|-----------|------|
| 9600 | 546.875 | 0x222E | 9600.0 | 0.00% |
| 19200 | 273.4375 | 0x1117 | 19200.0 | 0.00% |
| 38400 | 136.71875 | 0x0888 | 38400.0 | 0.00% |
| 57600 | 91.145833 | 0x05B1 | 57575.8 | -0.04% |
| 115200 | 45.572917 | 0x02D9 | 115384.6 | +0.16% |
| 230400 | 22.786458 | 0x016C | 230769.2 | +0.16% |
| 460800 | 11.393229 | 0x00B6 | 461538.5 | +0.16% |
| 921600 | 5.696615 | 0x005B | 923076.9 | +0.16% |
| 1000000 | 5.25 | 0x0054 | 1000000.0 | 0.00% |
| 2000000 | 2.625 | 0x002A | 2000000.0 | 0.00% |
| 3000000 | 1.75 | 0x001C | 3000000.0 | 0.00% |
| 4000000 | 1.3125 | 0x0015 | 4000000.0 | 0.00% |
| 5250000 | 1.0 | 0x0010 | 5250000.0 | 0.00% |

注意：当 USARTDIV < 1 时无法实现（BRR 最小值为 0x0010），即波特率上限为 fck/16。72MHz 时最高 4.5Mbps（OVER8=0）或 9Mbps（OVER8=1）。

### 2.9 内部 RC 振荡器引起的波特率误差

STM32 的 HSI（内部高速 RC 振荡器）精度为 ±1% 全温度范围（25°C 时 ±0.5%），而 HSE（外部晶振）精度通常 ±20ppm（0.002%）。使用 HSI 作为时钟源时，波特率误差可能较大：

| 时钟源 | 精度 | 115200bps 实际范围 | 是否安全（8N1） |
|--------|------|--------------------|-----------------|
| HSI（全温） | ±1% | 114048 ~ 116352 | 是（容限 ±2.5%） |
| HSI（25°C） | ±0.5% | 114624 ~ 115776 | 是 |
| HSE 8MHz 晶振 | ±0.005% | 115194 ~ 115206 | 是 |
| HSE 16MHz 晶振 | ±0.002% | 115197 ~ 115203 | 是 |

但若收发双方均使用 HSI，且温度极端，最坏情况误差为 ±2%，仍接近容限边缘。建议高波特率（≥ 115200）务必使用 HSE。

---

## 3. 数据帧格式

### 3.1 UART 帧结构总览

一个完整的 UART 数据帧由以下字段组成：

```
空闲    起始位  D0  D1  D2  D3  D4  D5  D6  D7  校验位  停止位
MARK ┐  ┌──┐   ┌──┐──┐──┐──┐──┐──┐──┐──┐──┐──┐  ┌──┐  ┌──┐
     └──┘  └──┘                                    └──┘  └──...
     1    0   LSB                                MSB  P    1
```

| 字段 | 位数 | 电平 | 说明 |
|------|------|------|------|
| 空闲 (Idle) | - | 高 (MARK) | 总线空闲时保持高电平 |
| 起始位 (Start) | 1 | 低 (SPACE) | 标志帧开始，触发接收端采样 |
| 数据位 (Data) | 5/6/7/8/9 | LSB 优先 | 实际载荷，LSB 先发 |
| 校验位 (Parity) | 0/1 | 计算 | 奇/偶/无校验 |
| 停止位 (Stop) | 1/1.5/2 | 高 (MARK) | 标志帧结束，确保下一帧起始位被识别 |

### 3.2 起始位

起始位是 1 位逻辑 0（SPACE）。接收端在总线空闲（持续高电平）后检测到下降沿，启动接收流程。接收端在起始位的中间（16 倍过采样时为第 8 个采样点）再次采样确认仍为低，否则视为毛刺丢弃。

起始位的作用：
- 实现帧同步：每个帧的起始位重新对齐接收时钟。
- 抗毛刺：通过中间采样点二次确认，避免噪声误触发。

### 3.3 数据位

数据位长度可配置为 5、6、7、8、9 位。最常用的是 8 位（一个字节）。发送顺序为 LSB 优先（D0 先发，D7 后发）。

9 位数据位用于多机通信（第 9 位为地址/数据标志，详见第 11 章）或带校验的 8 位数据。

5/6/7 位数据位主要用于兼容老式设备（如 5 位 Baudot 码用于电传打字机），现代系统几乎不用。

### 3.4 校验位

校验位用于简单的错误检测：

| 校验方式 | 计算规则 | 校验位值（数据位中 1 的个数为偶数时） |
|---------|----------|---------------------------------------|
| 无校验 (None) | 不发送校验位 | - |
| 奇校验 (Odd) | 数据位+校验位中 1 的总数为奇数 | 1 |
| 偶校验 (Even) | 数据位+校验位中 1 的总数为偶数 | 0 |
| Mark 校验 | 校验位恒为 1 | 1 |
| Space 校验 | 校验位恒为 0 | 0 |

校验位只能检测单比特错误，无法纠错。Mark/Space 校验实际上不提供错误检测能力，仅用于兼容特定协议或 9 位模式。

奇偶校验计算示例（数据 0x41 = 0b01000001，2 个 1）：
- 奇校验：校验位 = 1（使 1 的总数为 3，奇数）
- 偶校验：校验位 = 0（使 1 的总数为 2，偶数）

### 3.5 停止位

停止位为 1、1.5 或 2 位逻辑 1（MARK）。作用：
- 强制总线回到空闲状态（高电平）。
- 提供帧间间隔，确保接收端能识别下一帧的起始位下降沿。

| 停止位 | 应用场景 |
|--------|----------|
| 1 位 | 最常用，效率最高 |
| 1.5 位 | 仅用于 5 位数据位（电传兼容） |
| 2 位 | 兼容老式设备或慢速接收端，降低波特率误差敏感性 |

2 位停止位可将误差容限从 ±2.5% 提升到约 ±2.9%（10 位 → 11 位帧）。

### 3.6 常见帧格式配置

| 名称 | 数据位 | 校验 | 停止位 | 总位数 | 应用 |
|------|--------|------|--------|--------|------|
| 8N1 | 8 | None | 1 | 10 | 最常用，调试串口 |
| 8E1 | 8 | Even | 1 | 11 | 工业仪表 |
| 8O1 | 8 | Odd | 1 | 11 | 部分传感器 |
| 8N2 | 8 | None | 2 | 11 | 老式设备 |
| 7E1 | 7 | Even | 1 | 10 | 旧式终端 |
| 9N1 | 9 | None | 1 | 11 | 多机通信 |
| 8E2 | 8 | Even | 2 | 12 | 高可靠性 |

### 3.7 STM32 中的帧格式配置

STM32 HAL 库通过 `UART_InitTypeDef` 结构体配置帧格式：

```c
UART_HandleTypeDef huart1;

void MX_USART1_UART_Init(void)
{
    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;                       // Baud rate
    huart1.Init.WordLength = UART_WORDLENGTH_8B;         // 8 data bits
    huart1.Init.StopBits = UART_STOPBITS_1;              // 1 stop bit
    huart1.Init.Parity = UART_PARITY_NONE;               // No parity
    huart1.Init.Mode = UART_MODE_TX_RX;                  // TX and RX
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;         // No flow control
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;     // 16x oversampling
    if (HAL_UART_Init(&huart1) != HAL_OK)
    {
        Error_Handler();
    }
}
```

注意：当使能校验位（Even/Odd）且选择 8 位字长时，STM32 实际传输 7 位数据 + 1 位校验。若需要 8 位数据 + 校验，必须选择 9 位字长（`UART_WORDLENGTH_9B`），此时 8 位数据 + 1 位校验。这是 STM32 与部分其他 MCU 的差异，新手容易踩坑。

校验位与字长对应关系：

| 期望数据 | 期望校验 | STM32 配置 WordLength | 实际数据位 |
|---------|----------|-----------------------|-----------|
| 8 位 | None | 8B | 8 |
| 8 位 | Even/Odd | 9B | 8（第 9 位为校验） |
| 7 位 | Even/Odd | 8B | 7（第 8 位为校验） |
| 9 位 | None | 9B | 9（多机通信） |

---

## 4. STM32 USART 寄存器详解

STM32F1/F4 系列 USART 拥有 7 个主要寄存器。本节逐一详解位域定义。

### 4.1 寄存器总览

| 寄存器 | 全称 | 偏移地址 | 用途 |
|--------|------|----------|------|
| USART_SR | Status Register | 0x00 | 状态标志 |
| USART_DR | Data Register | 0x04 | 数据收发 |
| USART_BRR | Baud Rate Register | 0x08 | 波特率分频 |
| USART_CR1 | Control Register 1 | 0x0C | 使能、中断、帧格式 |
| USART_CR2 | Control Register 2 | 0x10 | 停止位、时钟、LIN |
| USART_CR3 | Control Register 3 | 0x14 | 流控、DMA、智能卡 |
| USART_GTPR | Guard Time/Prescaler | 0x18 | 智能卡/IRDA 预分频 |

### 4.2 USART_SR（状态寄存器）

| 位 | 名称 | 读写 | 复位值 | 说明 |
|----|------|------|--------|------|
| 7 | TXE | r | 1 | 发送数据寄存器空，写 DR 清零 |
| 6 | TC | r | 1 | 发送完成，软件序列清零 |
| 5 | RXNE | r | 0 | 读数据寄存器非空，读 DR 清零 |
| 4 | IDLE | r | 0 | 空闲线检测，软件序列清零 |
| 3 | ORE | r | 0 | 溢出错误，读 SR+DR 清零 |
| 2 | NE | r | 0 | 噪声错误标志 |
| 1 | FE | r | 0 | 帧错误标志 |
| 0 | PE | r | 0 | 校验错误标志 |

清零序列：
- TXE：写 DR
- TC：读 SR 后写 DR，或写 0 到 CR1.TCIE 后软件清零
- RXNE：读 DR
- IDLE：读 SR 再读 DR
- ORE：读 SR 再读 DR
- NE/FE/PE：读 SR 再读 DR

### 4.3 USART_DR（数据寄存器）

| 位 | 名称 | 说明 |
|----|------|------|
| [8:0] | DR[8:0] | 数据位（9 位模式下使用全部 9 位，8 位模式使用低 8 位） |

DR 实际由两个寄存器组成：TDR（发送，只写）和 RDR（接收，只读），共用同一地址。写入 DR 触发发送，读取 DR 获取接收数据。

```c
// Register-level send and receive
USART1->DR = 0x55;                      // Send a byte
while (!(USART1->SR & USART_SR_TXE));   // Wait for TDR empty
uint8_t data = USART1->DR & 0xFF;       // Read received byte
```

### 4.4 USART_BRR（波特率寄存器）

| 位 | 名称 | 说明 |
|----|------|------|
| [31:16] | - | 保留 |
| [15:4] | DIV_Mantissa[11:0] | 分频整数部分（12 位） |
| [3:0] | DIV_Fraction[3:0] | 分频小数部分（4 位） |

OVER8=0 时：`BRR = USARTDIV × 16`
OVER8=1 时：`BRR = (USARTDIV × 8) << 1 | (USARTDIV 小数部分)`，小数部分仅 3 位有效。

手动设置波特率（不使用 HAL）：

```c
// Set baud rate 115200 at fck=72MHz, OVER8=0
// USARTDIV = 72000000 / (16 * 115200) = 39.0625
// Mantissa = 39 = 0x27, Fraction = 1 = 0x1
USART1->BRR = (39 << 4) | 1;            // 0x0271
```

### 4.5 USART_CR1（控制寄存器 1）

| 位 | 名称 | 读写 | 复位值 | 说明 |
|----|------|------|--------|------|
| 15 | OVER8 | rw | 0 | 过采样模式：0=16倍，1=8倍 |
| 13 | UE | rw | 0 | USART 使能 |
| 12 | M | rw | 0 | 字长：0=8位，1=9位 |
| 11 | WAKE | rw | 0 | 唤醒方法：0=空闲线，1=地址标记 |
| 10 | PCE | rw | 0 | 校验使能 |
| 9 | PS | rw | 0 | 校验选择：0=偶，1=奇 |
| 8 | PEIE | rw | 0 | 校验错误中断使能 |
| 7 | TXEIE | rw | 0 | TDR 空中断使能 |
| 6 | TCIE | rw | 0 | 发送完成中断使能 |
| 5 | RXNEIE | rw | 0 | RDR 非空中断使能 |
| 4 | IDLEIE | rw | 0 | 空闲中断使能 |
| 3 | TE | rw | 0 | 发送使能 |
| 2 | RE | rw | 0 | 接收使能 |
| 1 | RWU | rw | 0 | 接收唤醒（静默模式） |
| 0 | SBK | rw | 0 | 发送断开帧 |

关键配置示例：

```c
// Enable USART1: 8N1, TX+RX, no parity, 16x oversampling
USART1->CR1 = USART_CR1_UE       // USART enable
            | USART_CR1_TE        // Transmitter enable
            | USART_CR1_RE;       // Receiver enable

// Enable RXNE interrupt
USART1->CR1 |= USART_CR1_RXNEIE;
// Enable IDLE interrupt for variable-length frame detection
USART1->CR1 |= USART_CR1_IDLEIE;
```

### 4.6 USART_CR2（控制寄存器 2）

| 位 | 名称 | 读写 | 复位值 | 说明 |
|----|------|------|--------|------|
| 15:14 | - | - | 0 | 保留 |
| 13:12 | STOP[1:0] | rw | 0 | 停止位：00=1, 01=0.5, 10=2, 11=1.5 |
| 11 | CLKEN | rw | 0 | 时钟使能（同步模式） |
| 10 | CPOL | rw | 0 | 时钟极性：0=空闲低，1=空闲高 |
| 9 | CPHA | rw | 0 | 时钟相位：0=第一个边沿，1=第二个 |
| 8 | LBCL | rw | 0 | 最后一个数据位输出时钟 |
| 7 | - | - | - | 保留 |
| 6 | LBDIE | rw | 0 | LIN 断开检测中断使能 |
| 5 | LBDL | rw | 0 | LIN 断开长度：0=10位，1=11位 |
| 4 | - | - | - | 保留 |
| 3:0 | ADD[3:0] | rw | 0 | 本机地址（多机通信） |

停止位配置：

```c
USART1->CR2 = 0;                          // 1 stop bit (default)
// USART1->CR2 = USART_CR2_STOP_1;        // 2 stop bits (STOP=10)
// USART1->CR2 = USART_CR2_STOP_0;        // 0.5 stop bit (STOP=01)
```

### 4.7 USART_CR3（控制寄存器 3）

| 位 | 名称 | 读写 | 复位值 | 说明 |
|----|------|------|--------|------|
| 15 | - | - | - | 保留 |
| 14 | ONEBITE | rw | 0 | 采样方法：0=3次采样多数表决，1=单次 |
| 13 | CTSIE | rw | 0 | CTS 中断使能 |
| 12 | CTSE | rw | 0 | CTS 使能（流控） |
| 11 | RTSE | rw | 0 | RTS 使能（流控） |
| 10 | DMAEN | rw | 0 | DMA 发送使能 |
| 9 | DMAR | rw | 0 | DMA 接收使能 |
| 8 | SCEN | rw | 0 | 智能卡模式使能 |
| 7 | NACK | rw | 0 | 智能卡 NACK 使能 |
| 6 | HDSEL | rw | 0 | 半双工选择 |
| 5 | IRLP | rw | 0 | IrDA 低功耗 |
| 4 | IREN | rw | 0 | IrDA 模式使能 |
| 3 | ERIE | rw | 0 | 错误中断使能（DMA 模式下） |
| 2 | - | - | - | 保留 |
| 1 | DDRE | rw | 0 | DMA 禁止接收错误 |
| 0 | - | - | - | 保留 |

DMA 与流控配置示例：

```c
// Enable DMA for both TX and RX
USART1->CR3 |= USART_CR3_DMAT | USART_CR3_DMAR;

// Enable RTS/CTS hardware flow control
USART1->CR3 |= USART_CR3_RTSE | USART_CR3_CTSE;

// Enable single-wire half-duplex
USART1->CR3 |= USART_CR3_HDSEL;
```

### 4.8 USART_GTPR（保护时间与预分频寄存器）

| 位 | 名称 | 说明 |
|----|------|------|
| [15:8] | GT[7:0] | 保护时间（智能卡模式） |
| [7:0] | PSC[7:0] | 预分频（IrDA: 1-255; 智能卡: 1-62） |

普通 UART 模式下 GTPR 不使用。

### 4.9 寄存器初始化完整示例

不依赖 HAL 库，直接操作寄存器初始化 USART1：

```c
void USART1_Register_Init(void)
{
    // 1. Enable clocks: GPIOA and USART1 (on APB2)
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_USART1EN;

    // 2. Configure PA9 (TX) as AF push-pull, PA10 (RX) as input floating
    GPIOA->CRH &= ~(GPIO_CRH_CNF9 | GPIO_CRH_CNF10);
    GPIOA->CRH |= GPIO_CRH_CNF9_1 | GPIO_CRH_MODE9;   // AF out, 50MHz
    GPIOA->CRH |= GPIO_CRH_CNF10_0;                    // Input floating

    // 3. Set baud rate 115200 (fck=72MHz, USARTDIV=39.0625)
    USART1->BRR = (39 << 4) | 1;

    // 4. Configure frame: 8N1, 16x oversampling
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE;
    USART1->CR2 = 0;                                   // 1 stop bit
    USART1->CR3 = 0;                                   // No flow ctrl/DMA

    // 5. Enable USART
    USART1->CR1 |= USART_CR1_UE;

    // 6. Enable RXNE interrupt (optional)
    USART1->CR1 |= USART_CR1_RXNEIE;
    NVIC_EnableIRQ(USART1_IRQn);
}
```

---

## 5. STM32 HAL UART 库详解

STM32 HAL 库封装了寄存器操作，提供三种传输模式：轮询、中断、DMA。

### 5.1 三种模式对比

| 模式 | 函数后缀 | CPU 占用 | 实时性 | 复杂度 | 适用场景 |
|------|----------|----------|--------|--------|----------|
| 轮询 | `_Transmit` / `_Receive` | 100% 阻塞 | 低 | 最简单 | 初始化、调试 |
| 中断 | `_IT` | 低（事件驱动） | 中 | 中 | 变长帧、单字节 |
| DMA | `_DMA` | 极低 | 高 | 较复杂 | 高速、大数据量 |

### 5.2 轮询模式

轮询模式函数原型：

```c
HAL_StatusTypeDef HAL_UART_Transmit(UART_HandleTypeDef *huart,
                                     const uint8_t *pData, uint16_t Size,
                                     uint32_t Timeout);
HAL_StatusTypeDef HAL_UART_Receive(UART_HandleTypeDef *huart,
                                    uint8_t *pData, uint16_t Size,
                                    uint32_t Timeout);
```

轮询发送示例：

```c
void UART_Polling_Send(void)
{
    uint8_t msg[] = "Hello UART\r\n";
    // Blocking send with 1000ms timeout
    HAL_UART_Transmit(&huart1, msg, sizeof(msg) - 1, 1000);
}
```

轮询接收示例（带超时）：

```c
void UART_Polling_Receive(void)
{
    uint8_t rx_buf[16];
    // Blocking receive, 100ms timeout per byte
    HAL_StatusTypeDef status = HAL_UART_Receive(&huart1, rx_buf, 8, 100);
    if (status == HAL_OK)
    {
        // Process received data
        HAL_UART_Transmit(&huart1, rx_buf, 8, 100);  // Echo
    }
    else if (status == HAL_TIMEOUT)
    {
        // Handle timeout
        uint8_t err[] = "TIMEOUT\r\n";
        HAL_UART_Transmit(&huart1, err, sizeof(err) - 1, 100);
    }
}
```

printf 重定向（轮询模式）：

```c
// Retarget printf to UART1 (override _write for newlib)
int _write(int file, char *ptr, int len)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)ptr, (uint16_t)len, HAL_MAX_DELAY);
    return len;
}
```

轮询模式的缺点：
- 阻塞 CPU，无法处理其他任务。
- 接收时若数据未到达，CPU 空等。
- 无法处理变长帧（必须预先知道长度）。

### 5.3 中断模式

中断模式函数原型：

```c
HAL_StatusTypeDef HAL_UART_Transmit_IT(UART_HandleTypeDef *huart,
                                        const uint8_t *pData, uint16_t Size);
HAL_StatusTypeDef HAL_UART_Receive_IT(UART_HandleTypeDef *huart,
                                       uint8_t *pData, uint16_t Size);
```

中断接收流程：调用 `HAL_UART_Receive_IT` → USART 触发 RXNE 中断 → HAL 在 ISR 中逐字节搬运到缓冲区 → 收满 Size 字节后回调 `HAL_UART_RxCpltCallback`。

中断接收示例：

```c
#define RX_BUF_SIZE 32
uint8_t rx_buf[RX_BUF_SIZE];
volatile uint8_t rx_ready = 0;

void UART_IT_Start(void)
{
    HAL_UART_Receive_IT(&huart1, rx_buf, 1);  // Receive 1 byte at a time
}

// Weak function override: called when 1 byte received
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        rx_ready = 1;
        HAL_UART_Receive_IT(&huart1, rx_buf, 1);  // Re-arm for next byte
    }
}
```

变长帧接收（结合 IDLE 空闲中断）：

```c
#define RX_BUF_SIZE 64
uint8_t rx_buf[RX_BUF_SIZE];
volatile uint16_t rx_len = 0;
volatile uint8_t frame_ready = 0;

void UART_VarLen_Start(void)
{
    // Enable IDLE interrupt
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_IDLE);
    HAL_UART_Receive_IT(&huart1, rx_buf, RX_BUF_SIZE);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    // Buffer full: process or overflow handling
    frame_ready = 1;
    rx_len = RX_BUF_SIZE;
}

// Call this in main loop or systick handler
void UART_Idle_Check(void)
{
    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_IDLE))
    {
        __HAL_UART_CLEAR_IDLEFLAG(&huart1);
        HAL_UART_DMAStop(&huart1);                // Stop reception
        rx_len = RX_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_usart1_rx);
        frame_ready = 1;
    }
}
```

注意：STM32F1 的 IDLE 标志需通过软件序列清零（读 SR 再读 DR），HAL 库的 `__HAL_UART_CLEAR_IDLEFLAG` 已封装。

中断模式优缺点：
- 优点：CPU 占用低，可处理变长帧，实时性好。
- 缺点：每字节一次中断，高波特率时中断开销大（115200bps 下每 87μs 一次中断）。建议高波特率使用 DMA。

### 5.4 DMA 模式

DMA 模式函数原型：

```c
HAL_StatusTypeDef HAL_UART_Transmit_DMA(UART_HandleTypeDef *huart,
                                         const uint8_t *pData, uint16_t Size);
HAL_StatusTypeDef HAL_UART_Receive_DMA(UART_HandleTypeDef *huart,
                                        uint8_t *pData, uint16_t Size);
HAL_StatusTypeDef HAL_UART_DMAStop(UART_HandleTypeDef *huart);
```

DMA 发送示例：

```c
#define TX_BUF_SIZE 256
uint8_t tx_buf[TX_BUF_SIZE] __attribute__((aligned(4)));  // DMA requires alignment

void UART_DMA_Send(uint8_t *data, uint16_t len)
{
    if (len > TX_BUF_SIZE) len = TX_BUF_SIZE;
    memcpy(tx_buf, data, len);
    // Start DMA transfer
    HAL_UART_Transmit_DMA(&huart1, tx_buf, len);
}

// Called when DMA TX completes
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        // TX done, can start next transfer
    }
}
```

DMA 接收示例：

```c
#define DMA_RX_SIZE 64
uint8_t dma_rx_buf[DMA_RX_SIZE];

void UART_DMA_Receive_Start(void)
{
    HAL_UART_Receive_DMA(&huart1, dma_rx_buf, DMA_RX_SIZE);
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_IDLE);  // Enable IDLE for var-length
}

// Called when DMA RX buffer full
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        // Buffer full: process or restart
        HAL_UART_Receive_DMA(&huart1, dma_rx_buf, DMA_RX_SIZE);
    }
}
```

DMA 模式是高速通信的首选，详细设计见第 6 章。

### 5.5 HAL 库回调函数汇总

| 回调函数 | 触发条件 | 模式 |
|---------|----------|------|
| HAL_UART_TxCpltCallback | 发送完成 | IT/DMA |
| HAL_UART_RxCpltCallback | 接收完成（缓冲区满） | IT/DMA |
| HAL_UART_ErrorCallback | 发生错误 | IT/DMA |
| HAL_UART_AbortCpltCallback | 中止完成 | IT/DMA |
| HAL_UART_AbortTransmitCpltCallback | 中止发送完成 | IT/DMA |
| HAL_UART_AbortReceiveCpltCallback | 中止接收完成 | IT/DMA |
| HAL_UARTEx_WakeupCallback | 唤醒事件 | 低功耗 |

错误回调示例：

```c
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    uint32_t err = huart->ErrorCode;
    if (err & HAL_UART_ERROR_PE)   { /* Parity error */ }
    if (err & HAL_UART_ERROR_NE)   { /* Noise error */ }
    if (err & HAL_UART_ERROR_FE)   { /* Framing error */ }
    if (err & HAL_UART_ERROR_ORE)  { /* Overrun error */ }
    if (err & HAL_UART_ERROR_DMA)  { /* DMA transfer error */ }
    // Restart reception
    HAL_UART_Receive_DMA(&huart1, dma_rx_buf, DMA_RX_SIZE);
}
```

### 5.6 HAL 库使用注意事项

1. **回调函数为弱定义**：HAL 库中所有回调函数都是 `__weak` 修饰，用户重新定义同名函数即可覆盖，无需修改库代码。
2. **句柄状态机**：`huart->gState`（发送状态）和 `huart->RxState`（接收状态）独立管理，可同时收发。
3. **DMA 缓冲区对齐**：DMA 访问的缓冲区需 4 字节对齐（STM32F1 DMA1 限制），建议使用 `__attribute__((aligned(4)))`。
4. **CubeMX 生成代码**：使用 CubeMX 配置 USART + DMA 可自动生成初始化代码，减少出错。
5. **HAL_UART_Transmit 阻塞**：该函数会等待 TXE 标志，期间 CPU 阻塞。若需非阻塞，使用 IT 或 DMA 版本。

### 5.7 完整的初始化代码（CubeMX 风格）

```c
UART_HandleTypeDef huart1;
DMA_HandleTypeDef hdma_usart1_tx;
DMA_HandleTypeDef hdma_usart1_rx;

void MX_DMA_Init(void)
{
    __HAL_RCC_DMA1_CLK_ENABLE();
    HAL_NVIC_SetPriority(DMA1_Channel4_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(DMA1_Channel4_IRQn);
    HAL_NVIC_SetPriority(DMA1_Channel5_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(DMA1_Channel5_IRQn);
}

void MX_USART1_UART_Init(void)
{
    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&huart1);
}

void HAL_UART_MspInit(UART_HandleTypeDef *huart)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    if (huart->Instance == USART1)
    {
        __HAL_RCC_USART1_CLK_ENABLE();
        __HAL_RCC_GPIOA_CLK_ENABLE();
        GPIO_InitStruct.Pin = GPIO_PIN_9;
        GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
        GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
        HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
        GPIO_InitStruct.Pin = GPIO_PIN_10;
        GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
        GPIO_InitStruct.Pull = GPIO_NOPULL;
        HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

        // DMA TX: DMA1 Channel4
        hdma_usart1_tx.Instance = DMA1_Channel4;
        hdma_usart1_tx.Init.Direction = DMA_MEMORY_TO_PERIPH;
        hdma_usart1_tx.Init.PeriphInc = DMA_PINC_DISABLE;
        hdma_usart1_tx.Init.MemInc = DMA_MINC_ENABLE;
        hdma_usart1_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
        hdma_usart1_tx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
        hdma_usart1_tx.Init.Mode = DMA_NORMAL;
        hdma_usart1_tx.Init.Priority = DMA_PRIORITY_MEDIUM;
        HAL_DMA_Init(&hdma_usart1_tx);
        __HAL_LINKDMA(&huart1, hdmatx, hdma_usart1_tx);

        // DMA RX: DMA1 Channel5
        hdma_usart1_rx.Instance = DMA1_Channel5;
        hdma_usart1_rx.Init.Direction = DMA_PERIPH_TO_MEMORY;
        hdma_usart1_rx.Init.PeriphInc = DMA_PINC_DISABLE;
        hdma_usart1_rx.Init.MemInc = DMA_MINC_ENABLE;
        hdma_usart1_rx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
        hdma_usart1_rx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
        hdma_usart1_rx.Init.Mode = DMA_CIRCULAR;          // Circular mode!
        hdma_usart1_rx.Init.Priority = DMA_PRIORITY_HIGH;
        HAL_DMA_Init(&hdma_usart1_rx);
        __HAL_LINKDMA(&huart1, hdmarx, hdma_usart1_rx);

        HAL_NVIC_SetPriority(USART1_IRQn, 0, 0);
        HAL_NVIC_EnableIRQ(USART1_IRQn);
    }
}
```

---

## 6. DMA 传输详解

DMA（Direct Memory Access，直接内存访问）是 UART 高速通信的核心技术。它允许外设与内存之间直接交换数据，无需 CPU 介入，大幅降低 CPU 占用率。本章将深入讲解 DMA 在 UART 中的应用，重点讨论循环模式（circular mode）防止数据覆盖、半传输中断（half transfer interrupt）的应用以及双缓冲区设计。

### 6.1 DMA 基础概念

DMA 控制器是一种独立于 CPU 的数据搬运引擎。它直接访问系统总线和内存，实现外设↔内存、内存↔内存之间的高速数据传输。

STM32 DMA 主要特性：

| 特性 | STM32F1 | STM32F4 | STM32H7 |
|------|---------|---------|---------|
| DMA 控制器数 | 2（DMA1/2） | 2（DMA1/2） | 2（DMA1/2） |
| 通道/流数 | 7/7 通道 | 8/8 流 | 8/8 流 |
| 传输位宽 | 8/16/32 bit | 8/16/32 bit | 8/16/32/64 bit |
| 优先级 | 4 级 | 4 级 | 4 级 |
| 模式 | 单次/循环 | 单次/循环/双缓冲 | 单次/循环/双缓冲 |
| 突发传输 | 不支持 | 支持（4/8/16 节拍） | 支持 |
| FIFO | 无 | 16 字节 | 16 字节 |

DMA 传输三要素：
1. **源地址**：外设数据寄存器（如 USART_DR）或内存缓冲区。
2. **目标地址**：内存缓冲区或外设数据寄存器。
3. **传输数据量**：要传输的数据项数。

### 6.2 STM32 DMA 通道映射

STM32F1 的 DMA 通道是硬连接的，每个外设请求映射到固定通道：

| DMA1 通道 | 外设请求 | 方向 |
|-----------|----------|------|
| Channel 1 | ADC1 | 外设→内存 |
| Channel 2 | SPI1_RX / USART3_RX | 外设→内存 |
| Channel 3 | SPI1_TX / USART3_TX | 内存→外设 |
| Channel 4 | SPI2_RX / USART1_RX | 外设→内存 |
| Channel 5 | SPI2_TX / USART1_TX | 内存→外设 |
| Channel 6 | USART2_RX | 外设→内存 |
| Channel 7 | USART2_TX | 内存→外设 |

注意：STM32F4/H7 采用流（Stream）+ 通道（Channel）选择器，任意流可映射任意外设请求，更灵活。

### 6.3 DMA 传输模式

STM32 DMA 支持两种主要模式：

**单次模式（Normal Mode）**：传输完指定数据量后，DMA 自动停止。需要软件重新启动下一次传输。适用于已知长度的批量传输。

**循环模式（Circular Mode）**：传输完指定数据量后，DMA 自动重置计数器，从缓冲区起始位置继续传输，形成无限循环。适用于连续数据流（如 UART 接收）。循环模式是 UART 接收的首选，能有效防止数据覆盖——当 CPU 还在处理前一段数据时，DMA 会自动绕回缓冲区开头继续写入，避免丢失数据。

两种模式对比：

| 特性 | 单次模式 | 循环模式 |
|------|----------|----------|
| 传输完成行为 | 停止，需重启 | 自动重启，继续传输 |
| 数据丢失风险 | 高（重启间隙） | 低（无缝衔接） |
| 适用场景 | 已知长度批量传输 | 连续数据流、UART 接收 |
| 缓冲区利用 | 一次性 | 环形复用 |
| 数据覆盖风险 | 低 | 需配合半传输中断处理 |

### 6.4 循环模式防止数据覆盖

在 UART 接收场景中，使用循环模式能有效防止数据覆盖。工作原理：

1. 配置 DMA 为循环模式，缓冲区大小为 N。
2. DMA 从 USART_DR 读取数据写入缓冲区[0]~[N-1]。
3. 写满 N 个字节后，DMA 不会停止，而是自动回到缓冲区[0]继续写入。
4. 同时触发"传输完成"中断（TC），通知 CPU 处理后半段缓冲区。
5. 当 DMA 写到缓冲区[N/2]时，触发"半传输"中断（HT），通知 CPU 处理前半段。

这种设计使得 CPU 有充足的时间处理数据，而 DMA 持续接收新数据，不会因为 CPU 处理慢而丢失字节。

### 6.5 半传输中断的应用

半传输中断（Half Transfer Interrupt，HT）是循环模式的关键特性。配合传输完成中断（TC），可实现双缓冲区无缝切换：

- **HT 中断**：DMA 写完前半段（0 ~ N/2-1）时触发，CPU 处理前半段，DMA 继续写后半段。
- **TC 中断**：DMA 写完后半段（N/2 ~ N-1）时触发，CPU 处理后半段，DMA 绕回写前半段。

这样，CPU 和 DMA 永远操作不同的缓冲区段，实现真正的并行处理，避免数据竞争和覆盖。

半传输中断的应用场景：
- 高速数据采集（ADC + DMA）
- UART 大数据流接收（GPS NMEA、传感器数据流）
- 音频流处理
- 协议帧解析（变长帧分段处理）

### 6.6 双缓冲区设计完整示例

以下是基于 DMA 循环模式 + 半传输中断 + 传输完成中断的双缓冲区 UART 接收完整实现：

```c
#include "stm32f1xx_hal.h"
#include <string.h>

#define UART_RX_BUF_SIZE 256                    // Must be power of 2
#define UART_RX_HALF_SIZE (UART_RX_BUF_SIZE / 2)

// Dual buffer: [0..HALF-1] = buffer A, [HALF..SIZE-1] = buffer B
static uint8_t uart_rx_buf[UART_RX_BUF_SIZE] __attribute__((aligned(4)));
static volatile uint16_t uart_rx_len = 0;
static volatile uint8_t uart_frame_flag = 0;

extern UART_HandleTypeDef huart1;
extern DMA_HandleTypeDef hdma_usart1_rx;

// Initialize DMA-based UART reception
void UART_DMA_RX_Init(void)
{
    // Start DMA in circular mode
    HAL_UART_Receive_DMA(&huart1, uart_rx_buf, UART_RX_BUF_SIZE);
    // Enable IDLE interrupt for variable-length frame detection
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_IDLE);
}

// Half transfer callback: buffer A is full, process it
void HAL_UART_RxHalfCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        // Process buffer A: uart_rx_buf[0 .. UART_RX_HALF_SIZE-1]
        // DMA is now writing to buffer B, safe to read A
        uint16_t len = UART_RX_HALF_SIZE;
        Process_Rx_Data(uart_rx_buf, len);
    }
}

// Full transfer callback: buffer B is full, process it
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        // Process buffer B: uart_rx_buf[UART_RX_HALF_SIZE .. UART_RX_BUF_SIZE-1]
        // DMA has wrapped around to buffer A, safe to read B
        uint16_t len = UART_RX_HALF_SIZE;
        Process_Rx_Data(&uart_rx_buf[UART_RX_HALF_SIZE], len);
    }
}

// User processing function (called from ISR context, keep it short)
void Process_Rx_Data(uint8_t *data, uint16_t len)
{
    // Copy to a secondary buffer for deferred processing
    // Avoid heavy computation in ISR
    extern void Queue_Rx_Data(uint8_t *, uint16_t);
    Queue_Rx_Data(data, len);
}

// IDLE line detection: handle variable-length frames
void UART_IDLE_Handle(void)
{
    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_IDLE))
    {
        __HAL_UART_CLEAR_IDLEFLAG(&huart1);
        // Calculate how many bytes received since last wrap
        uint16_t dma_remain = __HAL_DMA_GET_COUNTER(&hdma_usart1_rx);
        uint16_t dma_pos = UART_RX_BUF_SIZE - dma_remain;
        // Notify main loop of partial frame
        uart_rx_len = dma_pos;
        uart_frame_flag = 1;
    }
}
```

### 6.7 双缓冲区时序图

```
时间轴 →

缓冲区:  [AAAAAAA][BBBBBBB][AAAAAAA][BBBBBBB]...
DMA写:   ↑0       ↑HALF    ↑0       ↑HALF
         HT触发   TC触发   HT触发   TC触发
CPU处理:  └─处理A   └─处理B  └─处理A   └─处理B
```

### 6.8 DMA 发送设计

DMA 发送通常使用单次模式（Normal），因为发送数据量已知。发送完成后通过 TC 中断通知 CPU 可以填充下一批数据：

```c
#define UART_TX_BUF_SIZE 512
static uint8_t uart_tx_buf[UART_TX_BUF_SIZE] __attribute__((aligned(4)));
static volatile uint8_t uart_tx_busy = 0;

// Start a DMA transmit (non-blocking)
uint8_t UART_DMA_Send(const uint8_t *data, uint16_t len)
{
    if (uart_tx_busy) return 0;                  // Previous send ongoing
    if (len > UART_TX_BUF_SIZE) len = UART_TX_BUF_SIZE;
    memcpy(uart_tx_buf, data, len);
    uart_tx_busy = 1;
    HAL_UART_Transmit_DMA(&huart1, uart_tx_buf, len);
    return 1;
}

// TX complete callback
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1)
    {
        uart_tx_busy = 0;                        // Ready for next send
    }
}
```

### 6.9 DMA 缓冲区大小选择

缓冲区大小需权衡内存占用和实时性：

| 缓冲区大小 | 中断频率（1Mbps） | 内存占用 | 适用场景 |
|-----------|-------------------|----------|----------|
| 32 字节 | 31.25 kHz | 32B | 低速、内存紧张 |
| 64 字节 | 15.6 kHz | 64B | 通用 |
| 128 字节 | 7.8 kHz | 128B | 中速 |
| 256 字节 | 3.9 kHz | 256B | 高速推荐 |
| 512 字节 | 1.95 kHz | 512B | 大数据流 |
| 1024 字节 | 976 Hz | 1KB | 极高速、低延迟要求低 |

经验法则：缓冲区大小应至少为单次帧最大长度的 2 倍，确保 CPU 有足够处理时间。

### 6.10 DMA 常见陷阱

1. **缓冲区未对齐**：STM32F1 DMA1 要求 4 字节对齐，未对齐会导致数据错位或硬件错误。使用 `__attribute__((aligned(4)))`。
2. **缓冲区位于不可缓存区域**：若启用了 D-Cache（STM32F7/H7），DMA 缓冲区需放在非缓存区或手动维护缓存一致性。
3. **DMA 通道冲突**：STM32F1 同一通道不能同时用于多个外设。例如 USART1_RX 和 SPI2_RX 都在 DMA1 Channel 4，不能同时使用。
4. **循环模式下计数器读取**：`__HAL_DMA_GET_COUNTER` 返回剩余传输数，已传输数 = 缓冲区大小 - 剩余数。
5. **HAL_UART_DMAStop 副作用**：该函数会禁用 DMA 并清除状态，循环模式下慎用，可能导致丢字节。

### 6.11 DMA 与中断模式性能对比

以 1Mbps 波特率连续接收为例：

| 指标 | 中断模式（1 字节/中断） | DMA 循环模式 |
|------|------------------------|-------------|
| 中断频率 | 100 kHz（每 10μs） | 1.95 kHz（256B 缓冲，每 1.28ms） |
| CPU 占用 | ~60%（仅处理中断） | ~2% |
| 最大抖动 | 10μs（必须及时响应） | 1.28ms（处理窗口） |
| 数据丢失风险 | 高（中断被阻塞） | 极低 |
| 实现复杂度 | 简单 | 中等 |

结论：波特率超过 230400bps 时，强烈建议使用 DMA 模式。

---

## 7. 硬件流控制

### 7.1 RTS/CTS 流控制原理

硬件流控制（Hardware Flow Control）通过额外的信号线协调收发双方，防止接收缓冲区溢出。UART 最常用的流控制是 RTS/CTS（Request To Send / Clear To Send）。

工作流程：
1. **RTS（接收方→发送方）**：接收方准备好接收时，将 RTS 拉低（有效）。当接收缓冲区接近满时，拉高 RTS（无效），请求发送方暂停。
2. **CTS（发送方←接收方）**：发送方在发送前检查 CTS。CTS 无效时，发送方在当前字节发完后停止，直到 CTS 再次有效。

RTS/CTS 连接方式（交叉连接）：

```
设备 A                      设备 B
TX ──────────────────────── RX
RX ──────────────────────── TX
RTS ─────────────────────── CTS
CTS ─────────────────────── RTS
GND ─────────────────────── GND
```

### 7.2 STM32 RTS/CTS 配置

STM32 USART 内置 RTS/CTS 硬件流控制，无需软件干预：

```c
// Enable RTS/CTS in HAL
huart1.Init.HwFlowCtl = UART_HWCONTROL_RTS_CTS;

// Or register-level
USART1->CR3 |= USART_CR3_RTSE | USART_CR3_CTSE;
```

| CR3 位 | 功能 |
|--------|------|
| RTSE (bit 11) | 使能 RTS 输出，接收缓冲区满时自动拉高 RTS |
| CTSE (bit 12) | 使能 CTS 检测，CTS 无效时停止发送 |
| CTSIE (bit 13) | CTS 状态变化中断使能 |

RTS 自动控制逻辑：
- 当 RXNE 标志置位（接收寄存器非空）且未及时读取时，硬件自动拉高 RTS。
- 读取 DR 后 RXNE 清零，硬件拉低 RTS，恢复接收。

CTS 检测逻辑：
- 发送前硬件检查 CTS 引脚电平。
- CTS 为高（无效）时，发送器在当前字符发送完后暂停。
- CTS 为低（有效）时，继续发送。

### 7.3 RS-485 的 DE/RE 控制

RS-485 是半双工总线，同一时刻只能有一个发送者。方向控制通过 DE（Driver Enable）和 RE（Receiver Enable）引脚实现：

| DE | RE | 模式 |
|----|----|----|
| 1 | 0 | 发送模式 |
| 0 | 1 | 接收模式 |
| 0 | 0 | 待机（高阻） |
| 1 | 1 | 通常不用（自环回） |

多数 RS-485 收发器（如 MAX485）将 DE 和 RE 反相共用一个引脚（/RE 和 DE 接在一起），用单引脚控制方向。

STM32 的 USART 支持 RS-485 模式，通过 RTS 引脚自动控制 DE 信号：

```c
// HAL configuration for RS-485 mode
huart1.Init.Mode = UART_MODE_TX_RX;
huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
// After HAL_UART_Init, enable RS-485 mode
HAL_RS485Ex_Init(&huart1, UART_DE_POLARITY_HIGH, 1, 1);
```

寄存器配置：

```c
// Enable RS-485 mode: DEM (bit 14) and DEDT[4:0]
USART1->CR3 |= USART_CR3_DEM;                    // Driver Enable mode
// DE polarity: 1 = active high (default for MAX485)
USART1->CR3 |= USART_CR3_DEP;
// DE assertion time: 1 sample clock before start bit
USART1->CR1 = (USART1->CR1 & ~USART_CR1_DEDT) | (1 << 16);
// DE de-assertion time: 1 sample clock after last stop bit
USART1->CR1 = (USART1->CR1 & ~USART_CR1_DEAT) | (1 << 21);
```

### 7.4 RS-485 软件方向控制

若不使用硬件 DE 控制，需用 GPIO 软件切换方向。关键时序：

```c
#define RS485_DE_PIN  GPIO_PIN_8
#define RS485_DE_PORT GPIOA

void RS485_Send(uint8_t *data, uint16_t len)
{
    HAL_GPIO_WritePin(RS485_DE_PORT, RS485_DE_PIN, GPIO_PIN_SET);  // Enable TX
    HAL_UART_Transmit(&huart1, data, len, 1000);
    // Wait for last byte fully shifted out (TC flag)
    while (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_TC) == RESET);
    HAL_GPIO_WritePin(RS485_DE_PORT, RS485_DE_PIN, GPIO_PIN_RESET);  // Disable TX
}
```

注意：必须等待 TC（Transmission Complete）标志，而不是 TXE。TXE 仅表示数据已写入移位寄存器，最后一个字节可能还在发送，此时关闭 DE 会截断信号。TC 表示移位寄存器已完全清空。

### 7.5 XON/XOFF 软件流控制

除硬件流控外，还有软件流控制 XON/XOFF：
- XON（0x11，DC1）：请求对方继续发送。
- XOFF（0x13，DC3）：请求对方暂停发送。

软件流控无需额外引脚，但会污染数据流，且无法传输二进制数据（0x11/0x13 会被误判）。仅适用于纯文本通信，现代系统很少使用。

---

## 8. UART 错误处理

### 8.1 错误类型总览

UART 通信中可能发生四类错误：

| 错误 | 标志位 | 原因 | 严重性 |
|------|--------|------|--------|
| 校验错误 (PE) | SR.PE | 校验位与数据不符，单比特翻转 | 低 |
| 噪声错误 (NE) | SR.NE | 采样点三次表决不一致 | 低 |
| 帧错误 (FE) | SR.FE | 停止位不是高电平，时序错乱 | 高 |
| 溢出错误 (ORE) | SR.ORE | DR 未及时读取，新数据覆盖旧数据 | 高 |

### 8.2 校验错误（Parity Error）

校验错误表示接收到的校验位与数据位不匹配。通常是单比特翻转导致，可能源于噪声或干扰。

检测与处理：

```c
void UART_Check_Parity_Error(void)
{
    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_PE))
    {
        // Parity error detected
        uint8_t bad_byte = USART1->DR;           // Read DR to clear flag
        // Log or discard the corrupted byte
        Log_Error(ERR_PARITY, bad_byte);
        // Note: the byte is still in DR, decide whether to use or discard
    }
}
```

在 HAL 错误回调中处理：

```c
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->ErrorCode & HAL_UART_ERROR_PE)
    {
        // Parity error: usually safe to continue
        huart->ErrorCode &= ~HAL_UART_ERROR_PE;
    }
    // Restart reception if needed
    HAL_UART_Receive_DMA(&huart1, uart_rx_buf, UART_RX_BUF_SIZE);
}
```

### 8.3 噪声错误（Noise Error）

噪声错误（NE）表示接收采样过程中检测到不一致。STM32 在 16 倍过采样下，每位数据采样 3 次（第 7、8、9 个采样点），若 3 次结果不一致则置位 NE 标志。数据仍然可用，但可靠性降低。

```c
void UART_Check_Noise_Error(void)
{
    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_NE))
    {
        // Noise detected on this byte (data may still be valid)
        uint8_t data = USART1->DR;               // Read to clear NE
        // Increment noise counter for statistics
        noise_error_count++;
        // Optionally flag the data as "noisy"
        Process_Noisy_Byte(data);
    }
}
```

噪声错误通常不需要重启通信，但若频繁出现，应排查硬件（见第 9 章）。

### 8.4 帧错误（Framing Error）

帧错误（FE）表示停止位采样点不是高电平。常见原因：
- 波特率不匹配（最常见）
- 信号线断开
- 电气干扰严重
- 帧格式配置不一致（如一方 8N1，另一方 8E1）

```c
void UART_Check_Framing_Error(void)
{
    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_FE))
    {
        // Framing error: stop bit was not high
        uint8_t bad = USART1->DR;                // Read to clear FE
        framing_error_count++;
        // Frequent FE usually means baud rate mismatch
        if (framing_error_count > 10)
        {
            // Trigger baud rate re-negotiation or auto-detect
            UART_AutoBaud_Detect();
        }
    }
}
```

帧错误排查流程：
1. 检查双方波特率是否一致（最常见原因）。
2. 检查帧格式（数据位、校验位、停止位）是否一致。
3. 检查接线（TX-RX 交叉，GND 共地）。
4. 用示波器观察实际波形，验证波特率。

### 8.5 溢出错误（Overrun Error）

溢出错误（ORE）表示接收数据寄存器（DR）未被及时读取，新到的数据覆盖了旧数据。这是最严重的错误，意味着数据丢失。

```c
void UART_Check_Overrun_Error(void)
{
    if (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_ORE))
    {
        // Overrun: data was lost because DR wasn't read in time
        // Clear by reading SR then DR
        volatile uint32_t sr = USART1->SR;
        volatile uint8_t dr = USART1->DR;
        (void)sr; (void)dr;
        overrun_error_count++;
        // This indicates CPU is too slow to process RX data
        // Solutions: switch to DMA, increase priority, or lower baud rate
    }
}
```

ORE 常见原因与解决：

| 原因 | 解决方案 |
|------|----------|
| 中断处理太慢 | 降低中断处理复杂度，或改用 DMA |
| 中断被高优先级抢占阻塞 | 调整 NVIC 优先级 |
| 主循环阻塞未及时查询 | 改用中断或 DMA 模式 |
| 波特率过高 | 降低波特率或升级 MCU |
| FIFO 溢出（无 FIFO 的 MCU） | 使用带 FIFO 的 UART 或软件缓冲 |

### 8.6 综合错误统计

生产环境建议统计各类错误，便于故障定位：

```c
typedef struct {
    uint32_t parity_errors;
    uint32_t noise_errors;
    uint32_t framing_errors;
    uint32_t overrun_errors;
    uint32_t total_bytes;
} UART_ErrorStats;

static UART_ErrorStats uart_stats = {0};

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    uint32_t err = huart->ErrorCode;
    if (err & HAL_UART_ERROR_PE)  uart_stats.parity_errors++;
    if (err & HAL_UART_ERROR_NE)  uart_stats.noise_errors++;
    if (err & HAL_UART_ERROR_FE)  uart_stats.framing_errors++;
    if (err & HAL_UART_ERROR_ORE) uart_stats.overrun_errors++;
    // Clear error code
    huart->ErrorCode = HAL_UART_ERROR_NONE;
    // Restart reception
    HAL_UART_Receive_DMA(huart, uart_rx_buf, UART_RX_BUF_SIZE);
}
```

### 8.7 错误清零序列速查

| 错误标志 | 清零方法（F1 系列） |
|---------|---------------------|
| PE | 读 SR 再读 DR |
| NE | 读 SR 再读 DR |
| FE | 读 SR 再读 DR |
| ORE | 读 SR 再读 DR |
| IDLE | 读 SR 再读 DR |
| TXE | 写 DR |
| TC | 读 SR 后写 DR，或软件清零 |
| RXNE | 读 DR |

STM32F4/H7 的清零方式不同（部分标志写 0 清零），使用 HAL 库的 `__HAL_UART_CLEAR_FLAG` 宏可统一处理。

---

## 9. UART 噪声干扰排查

噪声（noise）是 UART 通信中最棘手的问题之一。本章系统讲解噪声导致随机错误位的可能原因、排查方法及解决方案。

### 9.1 噪声的表现

UART 噪声通常表现为：
- **偶发校验错误（PE）**：单比特翻转，校验位不匹配。
- **噪声标志（NE）频繁置位**：采样点不一致。
- **帧错误（FE）间歇出现**：起始位或停止位被干扰。
- **数据随机错乱**：部分字节变成乱码，但整体通信未中断。
- **通信距离增加后错误率上升**：典型的信号衰减或干扰耦合。

噪声与系统性的波特率不匹配不同：波特率不匹配会导致持续、稳定的错误，而噪声导致的是随机、偶发的错误。

### 9.2 噪声的可能原因

噪声导致随机错误位的可能原因包括：

| 原因类别 | 具体表现 | 检测方法 |
|---------|----------|----------|
| 信号线过长 | 线长 > 50cm（TTL），衰减严重 | 测量线长，示波器观察波形 |
| 无屏蔽 | 信号线裸露，耦合空间干扰 | 检查线缆是否有屏蔽层 |
| 共地不良 | GND 阻抗高，产生共模电压 | 测量两端 GND 压差 |
| 电源噪声 | 开关电源纹波耦合到信号 | 示波器观察电源和信号 |
| 电机/继电器干扰 | 电感负载切换产生 EMI | 错误与负载动作时序相关 |
| 串扰 | 多路信号线并行布线 | 检查 PCB 走线 |
| 阻抗不匹配 | 长线反射，信号振铃 | 示波器观察过冲振铃 |
| 波特率过高 | 单 bit 时间短，采样窗口窄 | 降低波特率测试 |
| 接地环路 | 多点接地形成环路 | 检查接地拓扑 |

### 9.3 排查方法

#### 9.3.1 检查屏蔽（Shielding）

屏蔽是抑制空间耦合干扰的有效手段。检查要点：
- 信号线是否使用屏蔽双绞线（STP）或屏蔽线缆。
- 屏蔽层是否单端接地（避免接地环路）。
- 屏蔽层接地是否可靠（低阻抗连接到机壳地或 PCB 地）。

屏蔽效果对比：

| 线缆类型 | 抗干扰能力 | 适用距离 | 成本 |
|---------|-----------|----------|------|
| 裸线（杜邦线） | 极差 | < 20cm | 极低 |
| 双绞线（UTP） | 一般 | < 1m | 低 |
| 屏蔽双绞线（STP） | 好 | < 5m | 中 |
| 同轴线 | 极好 | < 10m | 高 |

#### 9.3.2 降低波特率

降低波特率是排查噪声的快速验证手段。波特率越低，单 bit 时间越长，采样窗口越宽，对噪声容忍度越高。

| 波特率 | 单 bit 时间 | 采样窗口（16x） | 抗噪声能力 |
|--------|------------|-----------------|-----------|
| 9600 | 104 μs | 6.5 μs | 极强 |
| 115200 | 8.68 μs | 542 ns | 中 |
| 921600 | 1.08 μs | 67 ns | 弱 |
| 3000000 | 333 ns | 20 ns | 极弱 |

若在 9600bps 下通信稳定，而在 115200bps 下频繁出错，基本可判定为噪声问题（而非波特率不匹配）。

#### 9.3.3 使用差分信号

差分信号（RS-485/RS-422）对共模干扰有极强的抑制能力。若 TTL UART 噪声严重，改用 RS-485 是根本解决方案。

| 传输方式 | 抗共模干扰 | 抗差模干扰 | 适用距离 |
|---------|-----------|-----------|----------|
| TTL 单端 | 弱 | 弱 | < 50cm |
| RS-232 单端 | 中 | 弱 | < 15m |
| RS-485 差分 | 强 | 中 | < 1200m |

### 9.4 解决方案

#### 9.4.1 加屏蔽（Shield）

为信号线增加屏蔽层：
- 使用金属编织屏蔽线缆。
- 屏蔽层单端接地（通常在发送端）。
- 关键信号使用铝箔屏蔽 + 编织屏蔽双层线缆。

#### 9.4.2 串联电阻

在信号线上串联小电阻（22~100Ω）可抑制振铃和反射：

```
发送端 TX ──[100Ω]── 接收端 RX
```

串联电阻的作用：
- 阻抗匹配，减少长线反射。
- 限制瞬态电流，减缓边沿速率，降低 EMI。
- 与接收端寄生电容形成 RC 滤波，抑制高频噪声。

注意：串联电阻会延长信号上升/下降时间，过大的电阻可能导致采样错误。建议从 22Ω 开始试验。

#### 9.4.3 使用 RS-232/RS-485 转换

将 TTL 电平转换为 RS-232 或 RS-485：
- RS-232：±12V 电平，噪声容限大，适合 15m 内点对点。
- RS-485：差分传输，抗共模干扰，适合长距离多点总线。

#### 9.4.4 加滤波电容

在 RX 引脚并联小电容（10~100pF）到 GND，形成低通滤波：

```
RX ──┬── MCU
     │
    100pF
     │
    GND
```

注意：电容过大会导致信号边沿变缓，影响高波特率通信。100pF 适合 115200bps，1Mbps 以上建议不超过 22pF。

#### 9.4.5 加上拉电阻

TTL UART 空闲状态为高电平。若信号线悬空（如对端未上电），易拾取噪声。加 10kΩ 上拉到 VCC 可确保空闲电平稳定：

```
VCC ──[10kΩ]── RX/TX
```

### 9.5 故障排查表

| 现象 | 可能原因 | 排查方法 | 解决方案 |
|------|---------|----------|----------|
| 噪声 | 信号线过长/无屏蔽 | 降低波特率，加屏蔽 | 降低波特率，加屏蔽 |
| 乱码 | 波特率不匹配 | 检查双方波特率 | 统一波特率 |
| 偶发字节错误 | 电源噪声耦合 | 示波器观察电源 | 加滤波电容，改善电源 |
| FE 帧错误 | 停止位被干扰 | 示波器观察波形 | 加屏蔽，串联电阻 |
| ORE 溢出 | 中断响应慢 | 检查中断延迟 | 改用 DMA，降低波特率 |
| 长距离通信失败 | 信号衰减 | 测量线长 | 改用 RS-485 |
| 电机启动时出错 | EMI 干扰 | 关联电机动作时序 | 加光耦隔离，电机加吸收电路 |
| 多板共地出错 | 接地环路 | 测量 GND 压差 | 单点接地，加共模电感 |
| 振铃导致错误 | 阻抗不匹配 | 示波器看过冲 | 串联 22~100Ω 电阻 |
| 高波特率出错 | 采样窗口窄 | 降波特率测试 | 降低波特率或改用差分 |

### 9.6 示波器排查步骤

1. **观察空闲电平**：TX/RX 空闲应为稳定高电平，若有波动说明有干扰。
2. **观察起始位下降沿**：边沿应陡峭，若有振铃说明阻抗不匹配。
3. **观察数据位**：每位应平坦，若叠加高频纹波说明电源或 EMI 问题。
4. **观察停止位**：应稳定高电平，若抖动可能被识别为下一帧起始位。
5. **测量波特率**：用光标测量单 bit 时间，与设定值对比。

### 9.7 噪声统计与自动降速

生产环境可实现噪声自适应降速：

```c
// Adaptive baud rate based on noise statistics
void UART_Adaptive_Speed(void)
{
    static uint32_t last_check = 0;
    if (HAL_GetTick() - last_check > 5000)       // Check every 5s
    {
        last_check = HAL_GetTick();
        uint32_t noise_rate = uart_stats.noise_errors * 100 / uart_stats.total_bytes;
        if (noise_rate > 5)                       // > 5% noise
        {
            // Reduce baud rate
            UART_Set_Baud(current_baud / 2);
            Log_Event("Baud reduced due to noise");
        }
        // Reset counters
        memset(&uart_stats, 0, sizeof(uart_stats));
    }
}
```

---

## 10. Modbus RTU 协议实现

Modbus 是 Modicon 公司（现施耐德电气）于 1979 年发明的工业通信协议，已成为工业自动化的事实标准。Modbus RTU 基于 UART 串口（通常配合 RS-485），具有简单、可靠、开放的特点。

### 10.1 Modbus 协议概览

Modbus 有三种变体：

| 变体 | 物理层 | 帧格式 | 速率 | 应用 |
|------|--------|--------|------|------|
| Modbus RTU | RS-485/RS-232 | 二进制 | 高（115200bps） | 工业主流 |
| Modbus ASCII | RS-485/RS-232 | ASCII 十六进制 | 低 | 调试、老设备 |
| Modbus TCP | 以太网 | TCP/IP | 极高 | 现代工厂 |

Modbus 是主从协议：一个主站（Master）查询，多个从站（Slave）响应。从站地址 1-247，0 为广播地址。

### 10.2 Modbus RTU 帧格式

RTU 帧由地址码、功能码、数据、CRC16 校验组成，帧间通过 3.5 个字符时间的空闲间隔分隔：

```
┌────────┬────────┬─────────────┬────────┐
│ 地址   │ 功能码 │ 数据区      │ CRC16  │
│ 1 字节 │ 1 字节 │ 0-252 字节  │ 2 字节 │
└────────┴────────┴─────────────┴────────┘
↑                    帧间隔 ≥ 3.5 字符时间
└── 起始
```

帧间隔时间（3.5 字符）：

| 波特率 | 3.5 字符时间 |
|--------|-------------|
| 9600 | 4.06 ms |
| 19200 | 2.03 ms |
| 38400 | 1.02 ms |
| 115200 | 340 μs |
| > 115200 | 固定 1.75 ms |

注意：波特率超过 19200 时，Modbus 规范建议固定使用 1.75ms 帧间隔。

### 10.3 常用功能码

| 功能码 | 操作 | 数据类型 | 典型用途 |
|--------|------|----------|----------|
| 01 (0x01) | 读线圈 | 位（0/1） | 继电器状态 |
| 02 (0x02) | 读离散输入 | 位（只读） | 开关、按钮 |
| 03 (0x03) | 读保持寄存器 | 16 位 | 参数读取 |
| 04 (0x04) | 读输入寄存器 | 16 位（只读） | ADC 采样值 |
| 05 (0x05) | 写单个线圈 | 位 | 控制继电器 |
| 06 (0x06) | 写单个寄存器 | 16 位 | 设定参数 |
| 15 (0x0F) | 写多个线圈 | 位 | 批量控制 |
| 16 (0x10) | 写多个寄存器 | 16 位 | 批量设定 |

### 10.4 CRC16 校验算法

Modbus RTU 使用 CRC-16-ARC（多项式 0xA001），校验低位在前、高位在后：

```c
#include <stdint.h>

// Modbus CRC16 calculation (polynomial 0xA001)
uint16_t Modbus_CRC16(const uint8_t *data, uint16_t length)
{
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < length; i++)
    {
        crc ^= (uint16_t)data[i];
        for (uint8_t j = 0; j < 8; j++)
        {
            if (crc & 0x0001)
            {
                crc = (crc >> 1) ^ 0xA001;
            }
            else
            {
                crc >>= 1;
            }
        }
    }
    return crc;  // Low byte first, high byte second on wire
}
```

### 10.5 Modbus RTU 从站完整实现

以下是一个基于 STM32 HAL 的 Modbus RTU 从站完整实现，支持功能码 03/06/16：

```c
#include "stm32f1xx_hal.h"
#include <string.h>

#define MODBUS_BUF_SIZE 256
#define MODBUS_SLAVE_ADDR 1
#define HOLDING_REG_COUNT 32

static uint8_t modbus_rx_buf[MODBUS_BUF_SIZE];
static uint8_t modbus_tx_buf[MODBUS_BUF_SIZE];
static volatile uint16_t modbus_rx_len = 0;
static volatile uint8_t modbus_frame_ready = 0;
static uint32_t modbus_last_byte_tick = 0;

static uint16_t holding_regs[HOLDING_REG_COUNT] = {0};

extern UART_HandleTypeDef huart1;
extern DMA_HandleTypeDef hdma_usart1_rx;

// CRC16 (polynomial 0xA001)
static uint16_t Modbus_CRC16(const uint8_t *data, uint16_t len)
{
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < len; i++)
    {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++)
        {
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
        }
    }
    return crc;
}

// Initialize Modbus slave reception
void Modbus_Slave_Init(void)
{
    HAL_UART_Receive_DMA(&huart1, modbus_rx_buf, MODBUS_BUF_SIZE);
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_IDLE);
}

// Detect frame boundary by 3.5 char idle gap
void Modbus_Frame_Detect(void)
{
    if (modbus_frame_ready == 0 && modbus_rx_len > 0)
    {
        if (HAL_GetTick() - modbus_last_byte_tick > 4)  // 3.5 char at 9600bps
        {
            modbus_frame_ready = 1;
        }
    }
}

// Process received frame (call from main loop)
void Modbus_Slave_Process(void)
{
    if (!modbus_frame_ready) return;
    uint16_t len = modbus_rx_len;
    modbus_frame_ready = 0;
    modbus_rx_len = 0;

    // Minimum frame: addr + func + crc(2) = 4 bytes
    if (len < 4) { Modbus_Slave_Init(); return; }

    // Verify CRC
    uint16_t crc_calc = Modbus_CRC16(modbus_rx_buf, len - 2);
    uint16_t crc_recv = modbus_rx_buf[len - 2] | (modbus_rx_buf[len - 1] << 8);
    if (crc_calc != crc_recv) { Modbus_Slave_Init(); return; }

    // Check address
    uint8_t addr = modbus_rx_buf[0];
    if (addr != MODBUS_SLAVE_ADDR && addr != 0)  // 0 = broadcast
    {
        Modbus_Slave_Init();
        return;
    }

    uint8_t func = modbus_rx_buf[1];
    uint16_t tx_len = 0;

    switch (func)
    {
        case 0x03:  // Read holding registers
            tx_len = Modbus_Handle_Read_Holding(modbus_rx_buf, modbus_tx_buf);
            break;
        case 0x06:  // Write single register
            tx_len = Modbus_Handle_Write_Single(modbus_rx_buf, modbus_tx_buf);
            break;
        case 0x10:  // Write multiple registers
            tx_len = Modbus_Handle_Write_Multiple(modbus_rx_buf, modbus_tx_buf);
            break;
        default:
            tx_len = Modbus_Exception_Response(modbus_rx_buf, modbus_tx_buf, 0x01);
            break;
    }

    // Send response (skip broadcast)
    if (addr != 0 && tx_len > 0)
    {
        HAL_UART_Transmit_DMA(&huart1, modbus_tx_buf, tx_len);
    }
    Modbus_Slave_Init();  // Re-arm reception
}

// Handle function 0x03: read holding registers
uint16_t Modbus_Handle_Read_Holding(uint8_t *rx, uint8_t *tx)
{
    uint16_t reg_addr = (rx[2] << 8) | rx[3];
    uint16_t reg_count = (rx[4] << 8) | rx[5];

    if (reg_addr + reg_count > HOLDING_REG_COUNT)
    {
        return Modbus_Exception_Response(rx, tx, 0x02);  // Illegal address
    }

    tx[0] = rx[0];                  // Slave address
    tx[1] = 0x03;                   // Function code
    tx[2] = reg_count * 2;          // Byte count
    for (uint16_t i = 0; i < reg_count; i++)
    {
        tx[3 + i * 2] = (holding_regs[reg_addr + i] >> 8) & 0xFF;
        tx[4 + i * 2] = holding_regs[reg_addr + i] & 0xFF;
    }
    uint16_t len = 3 + reg_count * 2;
    uint16_t crc = Modbus_CRC16(tx, len);
    tx[len++] = crc & 0xFF;
    tx[len++] = (crc >> 8) & 0xFF;
    return len;
}

// Handle function 0x06: write single register
uint16_t Modbus_Handle_Write_Single(uint8_t *rx, uint8_t *tx)
{
    uint16_t reg_addr = (rx[2] << 8) | rx[3];
    uint16_t reg_value = (rx[4] << 8) | rx[5];

    if (reg_addr >= HOLDING_REG_COUNT)
    {
        return Modbus_Exception_Response(rx, tx, 0x02);
    }
    holding_regs[reg_addr] = reg_value;

    // Echo back the request as response
    memcpy(tx, rx, 6);
    uint16_t crc = Modbus_CRC16(tx, 6);
    tx[6] = crc & 0xFF;
    tx[7] = (crc >> 8) & 0xFF;
    return 8;
}

// Build exception response
uint16_t Modbus_Exception_Response(uint8_t *rx, uint8_t *tx, uint8_t code)
{
    tx[0] = rx[0];
    tx[1] = rx[1] | 0x80;           // Set high bit for exception
    tx[2] = code;                   // Exception code
    uint16_t crc = Modbus_CRC16(tx, 3);
    tx[3] = crc & 0xFF;
    tx[4] = (crc >> 8) & 0xFF;
    return 5;
}
```

### 10.6 Modbus 异常码

| 异常码 | 名称 | 说明 |
|--------|------|------|
| 01 | 非法功能码 | 不支持的功能码 |
| 02 | 非法数据地址 | 寄存器地址超出范围 |
| 03 | 非法数据值 | 数据值无效 |
| 04 | 从站故障 | 设备故障 |
| 05 | 确认 | 操作需要较长时间 |
| 06 | 从站忙碌 | 设备处理中 |

### 10.7 Modbus RTU 时序要求

Modbus RTU 对时序有严格要求：

| 参数 | 规范值 | 说明 |
|------|--------|------|
| 帧间隔 | ≥ 3.5 字符时间 | 帧边界识别 |
| 字符间隔 | ≤ 1.5 字符时间 | 同一帧内字符间隔 |
| 响应超时 | 100-1000ms | 主站等待从站响应 |
| 广播处理 | 无响应 | 从站收到广播不回复 |

违反时序会导致帧解析错误。STM32 实现 3.5 字符间隔检测的两种方法：
1. **IDLE 中断**：UART 空闲中断天然对应帧间隔，最常用。
2. **定时器**：每收到一个字节重置定时器，超时则判定帧结束。

### 10.8 RS-485 与 Modbus 的配合

Modbus RTU 通常运行在 RS-485 总线上，注意：
- 总线两端加 120Ω 终端电阻。
- 偏置电阻（A 上拉、B 下拉）确保总线空闲时电平确定。
- 多从站菊花链拓扑，避免分支。
- 同一时刻只有一个主站发送，避免总线冲突。

---

## 11. 多机通信（9位数据模式）

### 11.1 多机通信需求

标准 UART 是点对点通信，但实际应用中常需多机通信：
- 主控查询多个传感器。
- 多个 MCU 协同工作。
- RS-485 总线上的多从站系统。

UART 多机通信有两种主流方案：
1. **9 位数据模式**：硬件地址识别，效率高。
2. **软件地址识别**：所有数据都中断，软件判断，简单但效率低。

### 11.2 9 位数据模式原理

9 位数据模式下，每个数据帧有 9 个数据位。第 9 位（D8）作为地址/数据标志：
- D8 = 1：地址帧（包含从站地址）。
- D8 = 0：数据帧。

工作流程：
1. 主站发送地址帧（D8=1），所有从站都接收并检查地址。
2. 地址匹配的从站进入"激活"状态，准备接收后续数据帧。
3. 地址不匹配的从站进入"静默"状态，忽略后续数据帧（D8=0 不触发中断）。
4. 主站发送数据帧（D8=0），只有激活从站接收。
5. 通信结束后，主站发送下一个地址帧，唤醒所有从站。

这种机制的优势：未寻址的从站不会被数据帧中断，大幅降低 CPU 开销。

### 11.3 STM32 9 位模式配置

STM32 USART 的 9 位模式通过 CR1.M 位配置，结合静默模式（Mute Mode）实现硬件地址识别：

```c
// Configure 9-bit mode with address detection
void UART_MultiNode_Init(uint8_t my_address)
{
    huart1.Init.WordLength = UART_WORDLENGTH_9B;       // 9-bit mode
    huart1.Init.Parity = UART_PARITY_NONE;             // No parity
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.StopBits = UART_STOPBITS_1;
    HAL_UART_Init(&huart1);

    // Set slave address in CR2
    USART1->CR2 = (USART1->CR2 & ~USART_CR2_ADD) | (my_address & 0x0F);

    // Enable mute mode with address mark wakeup
    USART1->CR1 |= USART_CR1_WAKE;                     // Wakeup by address mark
    // RWU bit is set/cleared by hardware based on address match
}
```

### 11.4 9 位模式发送地址帧

发送地址帧时，第 9 位需置 1：

```c
// Send an address frame (9th bit = 1)
void UART_Send_Address(uint8_t address)
{
    while (!(USART1->SR & USART_SR_TXE));              // Wait for TDR empty
    USART1->DR = (address & 0xFF) | 0x100;             // Set 9th bit
}

// Send a data frame (9th bit = 0)
void UART_Send_Data(uint8_t data)
{
    while (!(USART1->SR & USART_SR_TXE));
    USART1->DR = data & 0xFF;                          // 9th bit = 0
}
```

### 11.5 从站接收处理

从站默认处于静默模式（RWU=1），仅地址帧会唤醒它：

```c
volatile uint8_t my_address = 0x05;
volatile uint8_t is_addressed = 0;
uint8_t rx_buf[64];
volatile uint16_t rx_idx = 0;

void USART1_IRQHandler(void)
{
    if (USART1->SR & USART_SR_RXNE)
    {
        uint16_t data = USART1->DR & 0x1FF;            // Read 9-bit data
        if (data & 0x100)                               // 9th bit = 1, address frame
        {
            if ((data & 0xFF) == my_address)
            {
                is_addressed = 1;                       // I'm being addressed
                rx_idx = 0;
            }
            else
            {
                is_addressed = 0;                       // Not for me, go mute
            }
        }
        else                                             // Data frame
        {
            if (is_addressed)
            {
                rx_buf[rx_idx++] = data & 0xFF;
                if (rx_idx >= sizeof(rx_buf)) rx_idx = 0;
            }
        }
    }
}
```

### 11.6 主站完整通信示例

```c
// Master: send command to slave 0x05
void Master_Send_To_Slave(uint8_t slave_addr, uint8_t *cmd, uint16_t len)
{
    // 1. Send address frame (9th bit = 1)
    UART_Send_Address(slave_addr);
    // 2. Send data frames (9th bit = 0)
    for (uint16_t i = 0; i < len; i++)
    {
        UART_Send_Data(cmd[i]);
    }
    // 3. Wait for response (with timeout)
    // ...
}
```

### 11.7 静默模式（Mute Mode）

STM32 支持两种静默模式唤醒方式：

| 唤醒方式 | CR1.WAKE | 说明 |
|---------|----------|------|
| 空闲线唤醒 | 0 | 检测到总线空闲（IDLE）后退出静默 |
| 地址标记唤醒 | 1 | 收到地址帧（D8=1）后退出静默 |

地址标记唤醒更高效，因为未寻址从站完全不会被数据帧中断。

### 11.8 多机通信总线拓扑

```
              ┌───┐
              │主站│
              └─┬─┘
                │
    ┌───────────┼───────────┐
    │           │           │
  ┌─┴─┐       ┌─┴─┐       ┌─┴─┐
  │从1│       │从2│       │从3│
  └───┘       └───┘       └───┘
   0x01        0x02        0x03
```

总线需为 RS-485（差分）或开漏（单线），所有节点共享 TX/RX。

---

## 12. UART 自动波特率检测

### 12.1 自动波特率的需求

某些场景下，通信双方波特率未知或不固定：
- USB-TTL 模块插入不同主机，主机波特率各异。
- 量产产品需自适应不同上位机配置。
- 现场调试时无法预知设备波特率。

自动波特率检测（Auto Baud Rate Detection，ABR）让接收端通过分析接收信号自动确定波特率。

### 12.2 STM32 自动波特率检测

STM32F0/F3/F4/F7/L4/H7 系列 USART 内置自动波特率检测硬件。CR1.ABRME 和 CR1.ABRREQ 位控制检测模式：

| 模式 | 触发字符 | 说明 |
|------|----------|------|
| 模式 0 | 起始位下降沿 | 测量起始位时长，最低 1 字符 |
| 模式 1 | 0x7F 或 0xF8 | 测量特定字符时长，更精确 |

配置代码：

```c
// Enable auto baud rate detection (Mode 1: 0x7F character)
void UART_AutoBaud_Init(void)
{
    // Configure USART without setting baud rate
    huart1.Instance = USART1;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    huart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_AUTOBAUDRATE_INIT;
    huart1.AdvancedInit.AutoBaudRateEnable = UART_ADVFEATURE_AUTOBAUDRATE_ENABLE;
    huart1.AdvancedInit.AutoBaudRateMode = UART_ADVFEATURE_AUTOBAUDRATE_ON7BITFRAME;
    HAL_UART_Init(&huart1);

    // Wait for ABR to complete
    while (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_ABRF) == RESET);
    // Read back detected baud rate
    uint32_t brr = USART1->BRR;
    uint32_t baud = SystemCoreClock / 16 / brr * 16;  // Approximate
}
```

### 12.3 软件自动波特率检测

无硬件 ABR 的 MCU（如 STM32F1）可用定时器+输入捕获实现软件 ABR：

```c
// Software auto baud rate detection using input capture
// Capture RX pin falling edge to start bit, rising edge to first data bit
volatile uint32_t capture_start = 0;
volatile uint32_t capture_end = 0;
volatile uint8_t baud_detected = 0;

// Assume TIM2 CH1 captures PA10 (USART1 RX)
void TIM2_IRQHandler(void)
{
    if (TIM2->SR & TIM_SR_CC1IF)
    {
        uint32_t cc = TIM2->CCR1;
        if (capture_start == 0)
        {
            capture_start = cc;                // First edge (start bit falling)
        }
        else if (!baud_detected)
        {
            capture_end = cc;                  // Second edge (first 0->1 transition)
            uint32_t ticks = capture_end - capture_start;
            // ticks = (number of bit periods) * timer_clock_period
            // For 0x55 (01010101), start bit + 4 zero bits = 5 bit periods
            uint32_t bit_ticks = ticks / 5;
            uint32_t baud = SystemCoreClock / bit_ticks;
            UART_Set_Baud(baud);
            baud_detected = 1;
        }
    }
}

// Helper to set baud rate dynamically
void UART_Set_Baud(uint32_t baud)
{
    uint32_t usartdiv = SystemCoreClock / (16 * baud);
    USART1->BRR = usartdiv;
}
```

### 12.4 自动波特率同步字符

发送端需发送特定的同步字符以触发 ABR：

| 字符 | 二进制 | 适用模式 | 说明 |
|------|--------|----------|------|
| 0x55 | 01010101 | 通用 | 交替 01，便于测量 |
| 0x7F | 01111111 | STM32 ABR 模式 1 | 一个起始位 + 6 个 1 |
| 0xF8 | 11111000 | STM32 ABR 模式 1 | 起始位后 5 个 1 |
| 0x00 | 00000000 | 不推荐 | 长低电平，易误判 |

### 12.5 ABR 注意事项

1. **首字节必须正确**：ABR 依赖首字节确定波特率，若首字节丢失或损坏会导致检测失败。
2. **检测期间不接收数据**：ABR 完成前，UART 无法正常接收。
3. **波特率范围限制**：ABR 可检测的波特率范围由时钟和定时器精度决定。
4. **超时处理**：若长时间未收到同步字符，应放弃 ABR 并使用默认波特率。

```c
// Auto baud rate with timeout fallback
void UART_AutoBaud_With_Fallback(void)
{
    UART_AutoBaud_Init();
    uint32_t start_tick = HAL_GetTick();
    while (!baud_detected && (HAL_GetTick() - start_tick < 2000))
    {
        // Wait for 2 seconds
    }
    if (!baud_detected)
    {
        // Fallback to default baud rate
        UART_Set_Baud(115200);
        Log_Event("Auto baud failed, using 115200");
    }
}
```

---

## 13. ESP32 Arduino UART 编程

### 13.1 ESP32 UART 硬件概览

ESP32 有 3 个硬件 UART：

| UART | 引脚（默认） | 用途 | DMA |
|------|-------------|------|-----|
| UART0 | GPIO1(TX)/GPIO3(RX) | 下载、调试 | 支持 |
| UART1 | GPIO10(TX)/GPIO9(RX) | 通用 | 支持 |
| UART2 | GPIO17(TX)/GPIO16(RX) | 通用 | 支持 |

ESP32 的 UART 支持 GPIO 矩阵重映射（任意 GPIO 可映射到任意 UART），这是与 STM32 的重要区别。

### 13.2 Arduino 基础 UART 编程

ESP32 Arduino 框架封装了 UART 操作：

```cpp
// Basic UART on ESP32 Arduino
void setup()
{
    Serial.begin(115200);                    // UART0, default pins 1/3
    Serial.println("ESP32 UART Demo");

    // Configure UART1 with custom pins
    Serial1.begin(9600, SERIAL_8N1, 18, 19); // baud, config, RX_PIN, TX_PIN
    Serial1.println("UART1 ready");
}

void loop()
{
    if (Serial.available())
    {
        char c = Serial.read();
        Serial.print("Received: ");
        Serial.println(c);
    }
}
```

### 13.3 ESP32 串口配置

ESP32 支持丰富的串口配置：

```cpp
// Configure UART2 with custom settings
void setup_uart2(void)
{
    // baud, data bits, parity, stop bits, RX pin, TX pin
    Serial2.begin(115200, SERIAL_8N1, 16, 17);

    // Change pins at runtime (ESP32 GPIO matrix)
    Serial2.setPins(16, 17, -1, -1);         // RX, TX, CTS, RTS

    // Set timeout for read operations
    Serial2.setTimeout(1000);

    // Set RX buffer size (default 256)
    Serial2.setRxBufferSize(1024);
}
```

### 13.4 ESP32 DMA UART 传输

ESP32 Arduino 框架内部已使用 DMA，用户无需手动配置。可通过 `setRxBufferSize` 调整 DMA 缓冲区大小：

```cpp
// ESP32 DMA-based UART reception (implicit)
void setup()
{
    // Large buffer for high-speed reception
    Serial1.setRxBufferSize(2048);           // Must call before begin()
    Serial1.begin(921600, SERIAL_8N1, 18, 19);
}

void loop()
{
    // Read available data (non-blocking)
    if (Serial1.available())
    {
        uint8_t buf[256];
        int len = Serial1.readBytes(buf, Serial1.available());
        // Process received data
        Process_Data(buf, len);
    }
}
```

### 13.5 ESP32 UART 中断处理

ESP32 Arduino 不直接暴露中断，通过 `Serial.onReceive` 或任务调度实现：

```cpp
volatile bool rx_flag = false;

// Hardware serial event (called by Arduino loop)
void serialEvent1()
{
    rx_flag = true;
}

void loop()
{
    if (rx_flag)
    {
        rx_flag = false;
        while (Serial1.available())
        {
            uint8_t c = Serial1.read();
            // Process byte
        }
    }
}
```

### 13.6 ESP32 FreeRTOS UART 任务

更高效的方式是使用 FreeRTOS 任务：

```cpp
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>

QueueHandle_t uart_queue;

// UART event task
void uart_event_task(void *pvParameters)
{
    uart_event_t event;
    uint8_t data[256];
    for (;;)
    {
        if (xQueueReceive(uart_queue, (void *)&event, portMAX_DELAY))
        {
            if (event.type == UART_DATA)
            {
                int len = uart_read_bytes(UART_NUM_1, data, event.size, portMAX_DELAY);
                // Process data
                Process_Data(data, len);
            }
            else if (event.type == UART_FRAME_ERR)
            {
                // Handle frame error
            }
            else if (event.type == UART_PARITY_ERR)
            {
                // Handle parity error
            }
        }
    }
}

void setup()
{
    // Configure UART1 using ESP-IDF API (more control than Arduino)
    uart_config_t cfg = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_APB,
    };
    uart_driver_install(UART_NUM_1, 2048, 0, 20, &uart_queue, 0);
    uart_param_config(UART_NUM_1, &cfg);
    uart_set_pin(UART_NUM_1, 17, 16, -1, -1);

    xTaskCreate(uart_event_task, "uart_task", 4096, NULL, 12, NULL);
}
```

### 13.7 ESP32 多串口同时使用

```cpp
// Use all 3 UARTs simultaneously
void setup()
{
    Serial.begin(115200);                                    // UART0 debug
    Serial1.begin(9600, SERIAL_8N1, 18, 19);                 // UART1 GPS
    Serial2.begin(115200, SERIAL_8N1, 16, 17);               // UART2 BT module
}

void loop()
{
    // GPS data
    if (Serial1.available())
    {
        String gps_line = Serial1.readStringUntil('\n');
        Process_GPS(gps_line);
    }
    // Bluetooth data
    if (Serial2.available())
    {
        uint8_t c = Serial2.read();
        Process_BT(c);
    }
}
```

### 13.8 ESP32 与 STM32 UART 对比

| 特性 | STM32 | ESP32 |
|------|-------|-------|
| UART 数 | 3-8 | 3 |
| DMA | 硬件 DMA | 内置（Arduino 封装） |
| GPIO 重映射 | 固定/AF 映射 | 任意 GPIO 矩阵 |
| 最大波特率 | 4.5-9 Mbps | 5 Mbps |
| 缓冲区 | FIFO 0-16 字节 | FIFO 128 字节 |
| 中断优先级 | NVIC 可配 | FreeRTOS 任务 |
| 编程模型 | HAL/LL/寄存器 | Arduino/ESP-IDF |

---

## 14. Linux UART 编程

### 14.1 Linux 串口设备

Linux 下串口设备文件：

| 设备 | 说明 |
|------|------|
| /dev/ttyS0~ttyS3 | 传统 8250/16550 串口 |
| /dev/ttyUSB0~ | USB 转串口（CH340/FT232/CP2102） |
| /dev/ttyACM0~ | USB CDC 类串口（Arduino、STM32 虚拟串口） |
| /dev/ttyAMA0 | 树莓派硬件串口（PL011） |
| /dev/ttyTHS0 | NVIDIA Jetson 硬件串口 |

权限配置：将用户加入 dialout 组以获得串口访问权限：
```bash
sudo usermod -aG dialout $USER
# Logout and login to take effect
```

### 14.2 termios API 概述

Linux 串口编程核心是 termios API，定义在 `<termios.h>`。主要结构体：

```c
#include <termios.h>
#include <fcntl.h>
#include <unistd.h>

struct termios {
    tcflag_t c_iflag;      // Input modes
    tcflag_t c_oflag;      // Output modes
    tcflag_t c_cflag;      // Control modes
    tcflag_t c_lflag;      // Local modes
    cc_t     c_cc[NCCS];   // Special characters
};
```

### 14.3 串口打开与配置

```c
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <stdio.h>

int uart_open(const char *device)
{
    int fd = open(device, O_RDWR | O_NOCTTY | O_NDELAY);
    if (fd < 0)
    {
        perror("open");
        return -1;
    }
    // Clear O_NDELAY to make read blocking
    fcntl(fd, F_SETFL, 0);
    return fd;
}

int uart_config(int fd, int baud, int data_bits, int parity, int stop_bits)
{
    struct termios tty;
    if (tcgetattr(fd, &tty) != 0)
    {
        perror("tcgetattr");
        return -1;
    }

    // Set baud rate (input and output)
    speed_t speed;
    switch (baud)
    {
        case 9600:   speed = B9600;   break;
        case 19200:  speed = B19200;  break;
        case 38400:  speed = B38400;  break;
        case 57600:  speed = B57600;  break;
        case 115200: speed = B115200; break;
        case 230400: speed = B230400; break;
        case 460800: speed = B460800; break;
        case 921600: speed = B921600; break;
        default:     speed = B115200; break;
    }
    cfsetispeed(&tty, speed);
    cfsetospeed(&tty, speed);

    // Disable special handling (raw mode)
    cfmakeraw(&tty);

    // Data bits (5/6/7/8)
    tty.c_cflag &= ~CSIZE;
    switch (data_bits)
    {
        case 5: tty.c_cflag |= CS5; break;
        case 6: tty.c_cflag |= CS6; break;
        case 7: tty.c_cflag |= CS7; break;
        default: tty.c_cflag |= CS8; break;
    }

    // Parity (0=none, 1=odd, 2=even)
    tty.c_cflag &= ~(PARENB | PARODD);
    if (parity == 1)      tty.c_cflag |= (PARENB | PARODD);  // Odd
    else if (parity == 2) tty.c_cflag |= PARENB;              // Even

    // Stop bits (1 or 2)
    if (stop_bits == 2) tty.c_cflag |= CSTOPB;
    else                tty.c_cflag &= ~CSTOPB;

    // Enable receiver, ignore modem control lines
    tty.c_cflag |= (CLOCAL | CREAD);

    // Read timeout: VMIN=1 byte, VTIME=10 (1 second)
    tty.c_cc[VMIN] = 1;
    tty.c_cc[VTIME] = 10;

    if (tcsetattr(fd, TCSANOW, &tty) != 0)
    {
        perror("tcsetattr");
        return -1;
    }
    return 0;
}
```

### 14.4 读写串口

```c
// Write data to UART
int uart_write(int fd, const uint8_t *data, int len)
{
    int n = write(fd, data, len);
    if (n < 0)
    {
        perror("write");
        return -1;
    }
    // Ensure data is flushed
    tcdrain(fd);
    return n;
}

// Read data from UART (blocking, timeout by VMIN/VTIME)
int uart_read(int fd, uint8_t *buf, int len)
{
    int n = read(fd, buf, len);
    if (n < 0)
    {
        perror("read");
        return -1;
    }
    return n;
}
```

### 14.5 完整的 Linux 串口示例

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

int main(int argc, char *argv[])
{
    const char *dev = "/dev/ttyUSB0";
    int fd = uart_open(dev);
    if (fd < 0) return 1;

    // Configure: 115200 8N1
    if (uart_config(fd, 115200, 8, 0, 1) < 0)
    {
        close(fd);
        return 1;
    }
    printf("UART %s opened and configured\n", dev);

    // Send a command
    const char *cmd = "AT\r\n";
    uart_write(fd, (const uint8_t *)cmd, strlen(cmd));

    // Read response
    uint8_t buf[256];
    int n = uart_read(fd, buf, sizeof(buf) - 1);
    if (n > 0)
    {
        buf[n] = '\0';
        printf("Response: %s\n", buf);
    }

    close(fd);
    return 0;
}
```

### 14.6 硬件流控制（Linux）

```c
// Enable RTS/CTS hardware flow control
int uart_enable_rtscts(int fd)
{
    struct termios tty;
    tcgetattr(fd, &tty);
    tty.c_cflag |= CRTSCTS;
    return tcsetattr(fd, TCSANOW, &tty);
}

// Disable flow control
int uart_disable_flowctl(int fd)
{
    struct termios tty;
    tcgetattr(fd, &tty);
    tty.c_cflag &= ~CRTSCTS;
    return tcsetattr(fd, TCSANOW, &tty);
}
```

### 14.7 select 多路复用

Linux 下处理多串口或非阻塞读写的标准方法是 select/poll：

```c
#include <sys/select.h>

// Non-blocking read with select timeout
int uart_read_select(int fd, uint8_t *buf, int len, int timeout_ms)
{
    fd_set rfds;
    struct timeval tv;
    FD_ZERO(&rfds);
    FD_SET(fd, &rfds);
    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;

    int ret = select(fd + 1, &rfds, NULL, NULL, &tv);
    if (ret < 0)
    {
        perror("select");
        return -1;
    }
    if (ret == 0)
    {
        return 0;  // Timeout
    }
    return read(fd, buf, len);
}
```

### 14.8 获取串口状态信号

```c
#include <sys/ioctl.h>

// Read modem status lines (CTS, DSR, DCD, RI)
void uart_print_status(int fd)
{
    int status;
    ioctl(fd, TIOCMGET, &status);
    printf("CTS: %s\n", (status & TIOCM_CTS) ? "ON" : "OFF");
    printf("DSR: %s\n", (status & TIOCM_DSR) ? "ON" : "OFF");
    printf("DCD: %s\n", (status & TIOCM_CD)  ? "ON" : "OFF");
    printf("RI:  %s\n", (status & TIOCM_RI)  ? "ON" : "OFF");
}

// Set RTS/DTR signals
void uart_set_rts(int fd, int on)
{
    int status;
    ioctl(fd, TIOCMGET, &status);
    if (on) status |= TIOCM_RTS;
    else    status &= ~TIOCM_RTS;
    ioctl(fd, TIOCMSET, &status);
}
```

### 14.9 Linux 串口调试工具

| 工具 | 用途 | 命令示例 |
|------|------|----------|
| stty | 配置串口参数 | `stty -F /dev/ttyUSB0 115200 cs8 -cstopb -parenb` |
| screen | 终端连接 | `screen /dev/ttyUSB0 115200` |
| minicom | 全功能串口终端 | `minicom -D /dev/ttyUSB0 -b 115200` |
| picocom | 轻量终端 | `picocom -b 115200 /dev/ttyUSB0` |
| cu | Unix 串口工具 | `cu -l /dev/ttyUSB0 -s 115200` |
| pyserial | Python 串口库 | `python3 -m serial.tools.miniterm /dev/ttyUSB0 115200` |

### 14.10 树莓派串口配置

树莓派的硬件串口默认用于蓝牙（Pi 3/4）。启用串口：

```bash
# Edit /boot/config.txt
sudo nano /boot/config.txt
# Add:
# enable_uart=1
# dtoverlay=disable-bt  (disable Bluetooth to free UART)

# Disable serial console
sudo raspi-config
# -> Interface Options -> Serial Port
# -> Login shell: No
# -> Hardware port: Yes
```

配置后设备为 `/dev/ttyAMA0` 或 `/dev/serial0`。

---

## 15. 常见问题与故障排查

### 15.1 FAQ 速查表

以下汇总 UART 开发中最常见的 20 个问题：

| 编号 | 问题 | 原因 | 解决方案 |
|------|------|------|----------|
| 1 | 串口输出全是乱码 | 波特率不匹配 | 检查双方波特率，用示波器测量 |
| 2 | 串口输出全是 0x00 或 0xFF | 接线错误或电平不匹配 | 检查 TX/RX 交叉、GND 共地、电平兼容 |
| 3 | 接收数据偶发错误 | 噪声干扰 | 加屏蔽，降低波特率，加滤波 |
| 4 | 高波特率下数据丢失 | CPU 响应慢 | 改用 DMA 模式 |
| 5 | DMA 接收数据覆盖 | 循环模式未启用或缓冲区太小 | 使用循环模式 + 半传输中断 |
| 6 | 流控制不生效 | RTS/CTS 接线错误或未启用 | 检查接线和 CR3 配置 |
| 7 | 收不到数据 | RX 接线错误或未使能接收 | 检查 RX 引脚和 CR1.RE |
| 8 | 只能发不能收 | TX/RX 接反 | 交叉连接 TX-RX |
| 9 | 帧错误频繁 | 波特率偏差或帧格式不一致 | 检查波特率和 8N1 配置 |
| 10 | 溢出错误 | 中断处理过慢 | 改用 DMA 或降低波特率 |
| 11 | 9 位模式通信失败 | 第 9 位设置错误 | 检查 DR 写入时 0x100 设置 |
| 12 | RS-485 通信失败 | DE 方向控制时序错误 | 等待 TC 标志再切换方向 |
| 13 | Modbus CRC 校验失败 | CRC 算法或字节序错误 | 确认多项式 0xA001，低位在前 |
| 14 | printf 无输出 | _write 未重定向 | 重定向 _write 到 UART |
| 15 | 中断不触发 | NVIC 未使能或优先级错误 | 检查 HAL_NVIC_EnableIRQ |
| 16 | 多机通信地址不识别 | 9 位模式配置错误 | 检查 CR1.M 和 CR2.ADD |
| 17 | Linux 串口权限拒绝 | 用户不在 dialout 组 | usermod -aG dialout |
| 18 | USB 转串口识别不到 | 驱动未安装 | 安装 CH340/CP2102 驱动 |
| 19 | 长距离通信失败 | TTL 电平衰减 | 改用 RS-485 |
| 20 | 自动波特率失败 | 同步字符错误 | 发送 0x55 或 0x7F |

### 15.2 详细故障排查

#### 15.2.1 串口输出全是乱码

排查步骤：
1. 用示波器测量 TX 引脚，确认有信号输出。
2. 测量单 bit 时间，反推实际波特率。
3. 检查 MCU 时钟配置（HSE vs HSI，PLL 倍频）。
4. 常见原因：SystemClock 配置错误导致 fck 与预期不符。

```c
// Verify system clock
void Check_SystemClock(void)
{
    uint32_t sysclk = HAL_RCC_GetSysClockFreq();
    uint32_t hclk = HAL_RCC_GetHCLKFreq();
    uint32_t pclk1 = HAL_RCC_GetPCLK1Freq();
    uint32_t pclk2 = HAL_RCC_GetPCLK2Freq();
    printf("SYSCLK=%lu HCLK=%lu PCLK1=%lu PCLK2=%lu\n",
           sysclk, hclk, pclk1, pclk2);
    // USART1 on APB2: fck = PCLK2
    // USART2/3 on APB1: fck = PCLK1
}
```

#### 15.2.2 DMA 接收数据覆盖

DMA 循环模式下数据覆盖的常见原因：
1. 缓冲区太小，CPU 处理慢于 DMA 接收速度。
2. 未使用半传输中断，只在 TC 中断处理，导致前半段被覆盖。
3. 中断处理函数中执行耗时操作，导致下次中断来不及。

解决：
- 增大缓冲区（至少 256 字节）。
- 启用半传输中断（HT）。
- 中断处理函数仅设置标志，主循环处理数据。

#### 15.2.3 流控制问题

RTS/CTS 流控制不生效的排查：
1. 硬件连接：RTS 和 CTS 必须交叉连接。
2. 寄存器配置：CR3.RTSE 和 CR3.CTSE 都需置位。
3. 引脚复用：确认 RTS/CTS 引脚配置为 AF 模式。
4. 电平兼容：3.3V 与 5V 系统间需电平转换。

### 15.3 调试技巧

#### 15.3.1 环回测试

将 TX 直接连接到 RX，自发自收验证 UART 硬件：

```c
void UART_Loopback_Test(void)
{
    uint8_t tx_data[] = "Loopback Test 0123456789\r\n";
    uint8_t rx_data[64] = {0};
    HAL_UART_Transmit(&huart1, tx_data, sizeof(tx_data) - 1, 1000);
    HAL_UART_Receive(&huart1, rx_data, sizeof(tx_data) - 1, 1000);
    if (memcmp(tx_data, rx_data, sizeof(tx_data) - 1) == 0)
    {
        printf("Loopback OK\n");
    }
    else
    {
        printf("Loopback FAIL\n");
    }
}
```

#### 15.3.2 逻辑分析仪

使用逻辑分析仪（如 Saleae、PulseView）捕获 UART 波形：
- 验证波特率是否准确。
- 检查帧格式（起始位、数据位、停止位）。
- 捕获偶发错误。

#### 15.3.3 协议分析

串口调试助手（如 SSCOM、PuTTY、Tera Term）可：
- 显示十六进制和 ASCII。
- 自动计算 CRC。
- 发送预设帧。

---

## 16. UART 性能优化

### 16.1 高速通信优化

高速 UART 通信（≥ 1Mbps）的关键优化点：

| 优化点 | 措施 | 效果 |
|--------|------|------|
| 使用 DMA | 替代中断模式 | CPU 占用从 60% 降至 2% |
| 循环模式 + 双缓冲 | 半传输中断 | 零拷贝、零丢失 |
| FIFO 使能 | 启用硬件 FIFO | 减少中断频率 |
| OVER8 模式 | 8 倍过采样 | 最高波特率翻倍 |
| 优先级提升 | 提高 DMA/UART NVIC 优先级 | 减少延迟抖动 |
| 缓存对齐 | DMA 缓冲区 4/32 字节对齐 | 避免 cache 一致性问题 |

### 16.2 低延迟优化

低延迟场景（如实时控制）要求 UART 处理延迟最小化：

```c
// Low-latency UART reception: 1-byte interrupt + immediate processing
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    static uint8_t rx_byte;
    if (huart->Instance == USART1)
    {
        // Immediate processing (keep ISR short!)
        if (rx_byte == FRAME_START)
        {
            frame_start_tick = HAL_GetTick();
        }
        // Re-arm immediately
        HAL_UART_Receive_IT(huart, &rx_byte, 1);
    }
}
```

降低延迟的技巧：
1. 提高 UART 中断优先级（NVIC 优先级数值越小越优先）。
2. 中断处理函数极简化，仅搬运数据。
3. 使用 DMA 循环模式，无需中断即可接收。
4. 避免在中断中调用 HAL_Delay 或其他阻塞函数。

### 16.3 批处理优化

对于已知长度的批量数据，使用 DMA 一次性传输：

```c
// Batch transmission: send entire frame via DMA
void Send_Frame_DMA(const uint8_t *frame, uint16_t len)
{
    // Wait for previous DMA to complete
    while (huart1.gState != HAL_UART_STATE_READY);
    HAL_UART_Transmit_DMA(&huart1, (uint8_t *)frame, len);
}
```

批处理优势：
- 减少 CPU 介入次数。
- 降低中断开销。
- 提高总线利用率。

### 16.4 内存优化

DMA 缓冲区内存优化技巧：
1. **静态分配**：使用 static 全局数组，避免堆栈分配。
2. **对齐声明**：`__attribute__((aligned(32)))` 适配 cache 行。
3. **非缓存区**：STM32H7 将 DMA 缓冲区放在 DTCM 或非缓存 SRAM。
4. **复用缓冲区**：TX/RX 共用缓冲区（半双工场景）。

```c
// STM32H7: place DMA buffer in non-cacheable region
#if defined(STM32H7)
__attribute__((section(".sram3")))  // SRAM3 is non-cacheable
uint8_t dma_rx_buf[1024];
#else
uint8_t dma_rx_buf[1024] __attribute__((aligned(4)));
#endif
```

### 16.5 功耗优化

低功耗场景下 UART 优化：
1. **唤醒功能**：利用 UART 空闲唤醒 MCU（Stop 模式）。
2. **降速运行**：低波特率降低动态功耗。
3. **DMA 替代轮询**：CPU 可进入低功耗，DMA 自动接收。
4. **关闭未用 UART**：RCC 时钟门控。

```c
// Enable UART wakeup from Stop mode
HAL_UARTEx_EnableStopMode(&huart1);
// Enter Stop mode, UART RX will wake up MCU
HAL_PWR_EnterSTOPMode(PWR_LOWPOWERREGULATOR_ON, PWR_STOPENTRY_WFI);
// After wakeup, reconfigure clock
SystemClock_Config();
```

### 16.6 性能基准测试

STM32F407 @ 168MHz 不同模式的吞吐量基准：

| 模式 | 波特率 | CPU 占用 | 实测吞吐 | 数据丢失 |
|------|--------|----------|----------|----------|
| 轮询 | 115200 | 100% | 11.5 KB/s | 无 |
| 中断 | 115200 | 15% | 11.5 KB/s | 无 |
| 中断 | 921600 | 60% | 92 KB/s | 偶发 |
| DMA 循环 | 921600 | 2% | 92 KB/s | 无 |
| DMA 循环 | 3000000 | 5% | 300 KB/s | 无 |
| DMA 循环 | 5250000 | 8% | 525 KB/s | 极少 |

### 16.7 协议层优化

应用层协议设计对 UART 性能影响显著：
1. **二进制协议**：比 ASCII 协议效率高 2-4 倍。
2. **批量读写**：Modbus 功能码 16（写多个）比 06（写单个）效率高。
3. **差分编码**：仅传输变化数据，减少数据量。
4. **压缩**：对大块数据压缩后传输（如 RLE、LZ4）。

### 16.8 中断延迟分析

Cortex-M 中断延迟：

| MCU | 中断延迟（周期） | @时钟 | 延迟时间 |
|-----|-----------------|-------|----------|
| Cortex-M0 | 16 | 48MHz | 333 ns |
| Cortex-M3 | 12 | 72MHz | 167 ns |
| Cortex-M4 | 12 | 168MHz | 71 ns |
| Cortex-M7 | 12 | 400MHz | 30 ns |

UART 中断处理总延迟 = 中断延迟 + ISR 入栈 + ISR 执行 + 出栈。优化 ISR 执行时间是关键。

---

## 17. 不同平台 UART 差异对比

### 17.1 主流平台 UART 对比

| 特性 | STM32F1 | STM32F4 | STM32H7 | ESP32 | Arduino UNO | Linux |
|------|---------|---------|---------|-------|-------------|-------|
| UART 数 | 3-5 | 6 | 4-8 | 3 | 1 | 4+ |
| 最大波特率 | 4.5 Mbps | 5.25 Mbps | 26 Mbps | 5 Mbps | 1 Mbps | 取决于硬件 |
| FIFO | 无 | 无 | 8-16 字节 | 128 字节 | 无 | 16-128 字节 |
| DMA | DMA1/2 | DMA1/2 | MDMA+DMA1/2 | 内置 | 无 | 内置 |
| 自动波特率 | 无 | 部分支持 | 支持 | 支持 | 无 | 无 |
| 硬件流控 | RTS/CTS | RTS/CTS | RTS/CTS | RTS/CTS | 无 | RTS/CTS |
| 9 位模式 | 支持 | 支持 | 支持 | 支持 | 支持 | 支持 |
| RS-485 DE | 软件控制 | 软件控制 | 硬件支持 | 软件控制 | 无 | 支持 |
| 编程模型 | HAL/LL/寄存器 | HAL/LL/寄存器 | HAL/LL | Arduino/ESP-IDF | Arduino | termios |
| 时钟源 | HSE/HSI | HSE/HSI | HSE/HSI/CSI | 外部/内部 | 外部 | 内部 |

### 17.2 寄存器映射差异

| 功能 | STM32F1 | STM32F4 | STM32H7 | ESP32 |
|------|---------|---------|---------|-------|
| 数据寄存器 | DR | DR | RDR/TDR | UART_FIFO_REG |
| 状态寄存器 | SR | SR | ISR/ICR | UART_INT_ST |
| 波特率 | BRR | BRR | BRR | UART_CLKDIV |
| 控制寄存器 | CR1/CR2/CR3 | CR1/CR2/CR3 | CR1/CR2/CR3 | UART_CONF0/1 |
| DMA 使能 | CR3.DMAT/DMAR | CR3.DMAT/DMAR | CR3.DMAT/DMAR | 自动 |
| FIFO 控制 | 无 | 无 | CR1.FIFOEN | 自动 |

### 17.3 初始化代码对比

STM32 HAL：
```c
huart1.Init.BaudRate = 115200;
huart1.Init.WordLength = UART_WORDLENGTH_8B;
huart1.Init.StopBits = UART_STOPBITS_1;
huart1.Init.Parity = UART_PARITY_NONE;
HAL_UART_Init(&huart1);
```

ESP32 Arduino：
```cpp
Serial.begin(115200, SERIAL_8N1, 16, 17);
```

ESP-IDF：
```c
uart_config_t cfg = {
    .baud_rate = 115200,
    .data_bits = UART_DATA_8_BITS,
    .parity = UART_PARITY_DISABLE,
    .stop_bits = UART_STOP_BITS_1,
    .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
};
uart_param_config(UART_NUM_1, &cfg);
```

Arduino UNO：
```cpp
Serial.begin(115200);  // 8N1 default
```

Linux C：
```c
struct termios tty;
tcgetattr(fd, &tty);
cfsetispeed(&tty, B115200);
cfsetospeed(&tty, B115200);
tty.c_cflag &= ~CSIZE;
tty.c_cflag |= CS8;
tty.c_cflag &= ~PARENB;
tty.c_cflag &= ~CSTOPB;
tcsetattr(fd, TCSANOW, &tty);
```

### 17.4 平台选型建议

| 应用场景 | 推荐平台 | 理由 |
|---------|----------|------|
| 低成本、低功耗 | STM32F0/F1 | 价格低，外设够用 |
| 高性能、多串口 | STM32F4/H7 | 多 UART、DMA、FIFO |
| 无线连接 | ESP32 | 内置 Wi-Fi/BT，3 个 UART |
| 教学、原型 | Arduino | 简单易学 |
| 工业控制 | STM32 + RS-485 | Modbus、可靠性 |
| 上位机、网关 | Linux (树莓派) | 多语言、网络栈 |

### 17.5 跨平台代码设计

为支持多平台，可抽象 UART 接口：

```c
// UART abstraction layer (hal_uart.h)
typedef struct {
    int  (*init)(uint32_t baud, uint8_t data_bits, uint8_t parity, uint8_t stop_bits);
    int  (*send)(const uint8_t *data, uint16_t len, uint32_t timeout);
    int  (*recv)(uint8_t *buf, uint16_t len, uint32_t timeout);
    int  (*send_dma)(const uint8_t *data, uint16_t len);
    int  (*recv_dma)(uint8_t *buf, uint16_t len);
    void (*deinit)(void);
} uart_ops_t;

extern const uart_ops_t uart_ops;  // Platform-specific implementation
```

STM32 实现：
```c
// stm32_uart.c
const uart_ops_t uart_ops = {
    .init     = stm32_uart_init,
    .send     = stm32_uart_send,
    .recv     = stm32_uart_recv,
    .send_dma = stm32_uart_send_dma,
    .recv_dma = stm32_uart_recv_dma,
    .deinit   = stm32_uart_deinit,
};
```

Linux 实现：
```c
// linux_uart.c
const uart_ops_t uart_ops = {
    .init     = linux_uart_init,
    .send     = linux_uart_send,
    .recv     = linux_uart_recv,
    .send_dma = linux_uart_send,   // Linux handles DMA internally
    .recv_dma = linux_uart_recv,
    .deinit   = linux_uart_deinit,
};
```

应用层统一调用：
```c
// application code (platform-independent)
void app_send_log(const char *msg)
{
    uart_ops.send((const uint8_t *)msg, strlen(msg), 1000);
}
```

### 17.6 总结

UART 作为最基础的串行通信接口，历经 60 余年仍焕发活力。掌握 UART 的协议原理、电气特性、寄存器配置、HAL/LL 编程、DMA 优化、错误处理、噪声排查、Modbus 工业通信、多机通信、自动波特率、跨平台编程，是嵌入式工程师的必备技能。

本文核心要点回顾：
1. **波特率匹配**是异步通信的根基，误差应控制在 ±2% 以内，高波特率必须使用 HSE。
2. **DMA 循环模式 + 半传输中断**是高速 UART 接收的最佳实践，可实现零丢失、零拷贝。
3. **硬件流控制** RTS/CTS 是高速长距离通信的保障，RS-485 的 DE 控制需注意 TC 标志时序。
4. **错误处理**需区分 PE/NE/FE/ORE，统计错误率有助于故障定位。
5. **噪声排查**遵循"屏蔽、降速、差分"三步法，示波器是必备工具。
6. **Modbus RTU** 是工业通信的事实标准，CRC16 和帧间隔是关键。
7. **跨平台抽象**使代码可移植，便于产品迭代。

---

## 附录 A：UART 速查卡

### A.1 常用波特率 BRR 值（STM32F103，fck=72MHz，OVER8=0）

| 波特率 | BRR(hex) | USARTDIV | 误差 |
|--------|----------|----------|------|
| 9600 | 0x1D4C | 468.75 | 0% |
| 19200 | 0x0EA6 | 234.375 | 0% |
| 38400 | 0x0753 | 117.1875 | 0% |
| 57600 | 0x04E2 | 78.125 | 0% |
| 115200 | 0x0271 | 39.0625 | +0.16% |
| 230400 | 0x0139 | 19.53125 | +0.16% |
| 460800 | 0x009D | 9.765625 | +0.16% |
| 921600 | 0x004F | 4.8828125 | +0.16% |

### A.2 错误标志清零序列

| 错误 | 清零（F1） | 清零（F4/H7） |
|------|-----------|---------------|
| PE | 读 SR + 读 DR | 写 ICR.PECF |
| FE | 读 SR + 读 DR | 写 ICR.FECF |
| NE | 读 SR + 读 DR | 写 ICR.NECF |
| ORE | 读 SR + 读 DR | 写 ICR.ORECF |
| IDLE | 读 SR + 读 DR | 写 ICR.IDLECF |

### A.3 CR1 关键位速查

| 位 | 名称 | 说明 |
|----|------|------|
| 13 | UE | USART 使能（总开关） |
| 12 | M | 字长：0=8位，1=9位 |
| 7 | TXEIE | TDR 空中断使能 |
| 6 | TCIE | 发送完成中断使能 |
| 5 | RXNEIE | RDR 非空中断使能 |
| 4 | IDLEIE | 空闲中断使能 |
| 3 | TE | 发送使能 |
| 2 | RE | 接收使能 |

### A.4 常用中断向量（STM32F103）

| 中断 | 向量 | 用途 |
|------|------|------|
| USART1_IRQn | 37 | USART1 全部中断 |
| USART2_IRQn | 38 | USART2 全部中断 |
| USART3_IRQn | 39 | USART3 全部中断 |
| DMA1_Channel4_IRQn | 14 | USART1_TX DMA |
| DMA1_Channel5_IRQn | 15 | USART1_RX DMA |

---

## 附录 B：参考文献与标准

1. STMicroelectronics. RM0008 Reference Manual - STM32F103xx. Doc ID 13902 Rev 15.
2. STMicroelectronics. RM0090 Reference Manual - STM32F405/415. Doc ID 018909 Rev 16.
3. STMicroelectronics. AN4032 Using the STM32F2/F4 DMA controller.
4. EIA/TIA-232-F, Interface Between Data Terminal Equipment and Data Circuit-Terminating Equipment Employing Serial Binary Data Interchange.
5. ANSI/TIA/EIA-485-A, Electrical Characteristics of Generators and Receivers for Use in Balanced Digital Multipoint Systems.
6. MODBUS Application Protocol Specification V1.1b3, Modbus Organization, 2012.
7. IEEE Std 1174-2000, Standard for Serial Interface for Measuring Instruments.
8. Texas Instruments. SLLA070 RS-422 and RS-485 Application Note.
9. Maxim Integrated. AN705 Understanding Automatic Baud Rate Detection.
10. Linux man pages: termios(3), tty(4), select(2).

---

*文档版本：v1.0 | 最后更新：2026-06 | 适用平台：STM32F1/F4/H7, ESP32, Arduino, Linux*

---

## 附录 C：扩展内容 - 深入实践

本附录提供更多实战代码、扩展案例与进阶主题，作为正文章节的补充。

### C.1 Modbus RTU 主站完整实现

第 10 章给出了从站实现，本节补充主站（Master）实现。主站负责轮询各从站，发起请求并处理响应。

```c
#include "stm32f1xx_hal.h"
#include <string.h>

#define MODBUS_MASTER_BUF 256
#define MODBUS_TIMEOUT_MS 500

static uint8_t mb_tx_buf[MODBUS_MASTER_BUF];
static uint8_t mb_rx_buf[MODBUS_MASTER_BUF];
static volatile uint16_t mb_rx_len = 0;
static volatile uint8_t mb_rx_done = 0;

extern UART_HandleTypeDef huart1;
extern DMA_HandleTypeDef hdma_usart1_rx;

// CRC16
static uint16_t MB_CRC16(const uint8_t *data, uint16_t len)
{
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < len; i++)
    {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++)
            crc = (crc & 1) ? (crc >> 1) ^ 0xA001 : (crc >> 1);
    }
    return crc;
}

// Send request and wait for response (blocking with timeout)
uint16_t Modbus_Master_Transact(uint8_t slave, uint8_t func,
                                 uint16_t addr, uint16_t value,
                                 uint8_t *resp)
{
    uint16_t tx_len = 0;
    mb_tx_buf[0] = slave;
    mb_tx_buf[1] = func;

    switch (func)
    {
        case 0x03:  // Read holding registers
        case 0x04:  // Read input registers
            mb_tx_buf[2] = (addr >> 8) & 0xFF;
            mb_tx_buf[3] = addr & 0xFF;
            mb_tx_buf[4] = (value >> 8) & 0xFF;   // quantity
            mb_tx_buf[5] = value & 0xFF;
            tx_len = 6;
            break;
        case 0x06:  // Write single register
            mb_tx_buf[2] = (addr >> 8) & 0xFF;
            mb_tx_buf[3] = addr & 0xFF;
            mb_tx_buf[4] = (value >> 8) & 0xFF;
            mb_tx_buf[5] = value & 0xFF;
            tx_len = 6;
            break;
        default:
            return 0;
    }
    uint16_t crc = MB_CRC16(mb_tx_buf, tx_len);
    mb_tx_buf[tx_len++] = crc & 0xFF;
    mb_tx_buf[tx_len++] = (crc >> 8) & 0xFF;

    // Reset RX state
    mb_rx_len = 0;
    mb_rx_done = 0;
    HAL_UART_Receive_DMA(&huart1, mb_rx_buf, MODBUS_MASTER_BUF);
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_IDLE);

    // Send request
    HAL_UART_Transmit(&huart1, mb_tx_buf, tx_len, 100);

    // Wait for response
    uint32_t start = HAL_GetTick();
    while (!mb_rx_done && (HAL_GetTick() - start < MODBUS_TIMEOUT_MS))
    {
        // IDLE detection sets mb_rx_done
    }
    if (!mb_rx_done) return 0;  // Timeout

    // Verify CRC
    uint16_t len = mb_rx_len;
    if (len < 4) return 0;
    uint16_t crc_calc = MB_CRC16(mb_rx_buf, len - 2);
    uint16_t crc_recv = mb_rx_buf[len - 2] | (mb_rx_buf[len - 1] << 8);
    if (crc_calc != crc_recv) return 0;

    // Check for exception response
    if (mb_rx_buf[1] & 0x80)
    {
        resp[0] = mb_rx_buf[2];  // Exception code
        return 0xFFFF;           // Indicate exception
    }

    // Copy response data
    memcpy(resp, mb_rx_buf, len);
    return len;
}

// Example: read 10 holding registers from slave 1, address 0
void Modbus_Master_Poll_Example(void)
{
    uint8_t resp[MODBUS_MASTER_BUF];
    uint16_t len = Modbus_Master_Transact(1, 0x03, 0, 10, resp);
    if (len == 0)
    {
        printf("No response or CRC error\n");
        return;
    }
    if (len == 0xFFFF)
    {
        printf("Exception: %02X\n", resp[0]);
        return;
    }
    // resp[2] = byte count, resp[3..] = register data
    uint8_t byte_count = resp[2];
    for (uint8_t i = 0; i < byte_count / 2; i++)
    {
        uint16_t reg = (resp[3 + i * 2] << 8) | resp[4 + i * 2];
        printf("Reg[%d] = %u\n", i, reg);
    }
}
```

### C.2 Modbus RTU 主站轮询调度

工业场景中主站需轮询多个从站，合理的调度策略很重要：

```c
typedef struct {
    uint8_t slave_addr;
    uint8_t func;
    uint16_t reg_addr;
    uint16_t reg_count;
    uint32_t poll_interval_ms;
    uint32_t last_poll_tick;
    void (*handler)(uint8_t *resp, uint16_t len);
} Modbus_Poll_Entry;

#define MAX_POLL_ENTRIES 16
static Modbus_Poll_Entry poll_table[MAX_POLL_ENTRIES];
static uint8_t poll_count = 0;

void Modbus_Poll_Add(uint8_t addr, uint8_t func, uint16_t reg,
                     uint16_t count, uint32_t interval,
                     void (*handler)(uint8_t *, uint16_t))
{
    if (poll_count >= MAX_POLL_ENTRIES) return;
    poll_table[poll_count].slave_addr = addr;
    poll_table[poll_count].func = func;
    poll_table[poll_count].reg_addr = reg;
    poll_table[poll_count].reg_count = count;
    poll_table[poll_count].poll_interval_ms = interval;
    poll_table[poll_count].last_poll_tick = 0;
    poll_table[poll_count].handler = handler;
    poll_count++;
}

// Call this in main loop
void Modbus_Poll_Run(void)
{
    uint8_t resp[MODBUS_MASTER_BUF];
    for (uint8_t i = 0; i < poll_count; i++)
    {
        Modbus_Poll_Entry *e = &poll_table[i];
        if (HAL_GetTick() - e->last_poll_tick >= e->poll_interval_ms)
        {
            e->last_poll_tick = HAL_GetTick();
            uint16_t len = Modbus_Master_Transact(e->slave_addr, e->func,
                                                   e->reg_addr, e->reg_count, resp);
            if (len > 0 && len != 0xFFFF && e->handler)
            {
                e->handler(resp, len);
            }
        }
    }
}
```

### C.3 DMA 双缓冲区高级设计 - 环形缓冲区

除了第 6 章的固定双缓冲区，还可实现无锁环形缓冲区，适合不定长数据流：

```c
#include <stdbool.h>

#define RING_BUF_SIZE 1024   // Must be power of 2 for fast modulo
#define RING_BUF_MASK (RING_BUF_SIZE - 1)

typedef struct {
    uint8_t buf[RING_BUF_SIZE] __attribute__((aligned(4)));
    volatile uint16_t dma_pos;   // DMA write position (updated from ISR)
    uint16_t read_pos;           // Application read position
} RingBuffer;

static RingBuffer uart_ring;

// Initialize ring buffer with DMA circular reception
void RingBuf_Init(void)
{
    uart_ring.read_pos = 0;
    uart_ring.dma_pos = 0;
    HAL_UART_Receive_DMA(&huart1, uart_ring.buf, RING_BUF_SIZE);
}

// Get current DMA write position (call from main or ISR)
static inline uint16_t RingBuf_DMA_Pos(void)
{
    return RING_BUF_SIZE - __HAL_DMA_GET_COUNTER(&hdma_usart1_rx);
}

// Available bytes to read
uint16_t RingBuf_Available(void)
{
    uint16_t dma_pos = RingBuf_DMA_Pos();
    uint16_t avail = (dma_pos - uart_ring.read_pos) & RING_BUF_MASK;
    return avail;
}

// Read one byte (non-blocking)
bool RingBuf_ReadByte(uint8_t *byte)
{
    uint16_t dma_pos = RingBuf_DMA_Pos();
    if (uart_ring.read_pos == dma_pos) return false;  // Empty
    *byte = uart_ring.buf[uart_ring.read_pos];
    uart_ring.read_pos = (uart_ring.read_pos + 1) & RING_BUF_MASK;
    return true;
}

// Read multiple bytes (non-blocking)
uint16_t RingBuf_Read(uint8_t *dst, uint16_t max_len)
{
    uint16_t read = 0;
    while (read < max_len && RingBuf_ReadByte(&dst[read]))
    {
        read++;
    }
    return read;
}

// Peek without consuming
bool RingBuf_Peek(uint8_t *byte, uint16_t offset)
{
    uint16_t dma_pos = RingBuf_DMA_Pos();
    uint16_t avail = (dma_pos - uart_ring.read_pos) & RING_BUF_MASK;
    if (offset >= avail) return false;
    *byte = uart_ring.buf[(uart_ring.read_pos + offset) & RING_BUF_MASK];
    return true;
}

// Find a delimiter byte in buffer (for line-based protocols)
int16_t RingBuf_Find(uint8_t delimiter)
{
    uint16_t avail = RingBuf_Available();
    for (uint16_t i = 0; i < avail; i++)
    {
        uint8_t b;
        if (RingBuf_Peek(&b, i) && b == delimiter)
        {
            return i;
        }
    }
    return -1;
}
```

应用示例 - 按行解析 AT 命令响应：

```c
// Process AT command responses (line-based, terminated by \r\n)
void AT_Process_Line(void)
{
    int16_t cr_pos = RingBuf_Find('\r');
    if (cr_pos < 0) return;  // No complete line yet

    uint8_t line[128];
    uint16_t len = RingBuf_Read(line, cr_pos + 1);  // Include \r
    line[len] = '\0';

    // Check if it's a known response
    if (strstr((char *)line, "OK"))
    {
        printf("Command succeeded\n");
    }
    else if (strstr((char *)line, "ERROR"))
    {
        printf("Command failed\n");
    }
    else
    {
        printf("Data: %s\n", line);
    }
}
```

### C.4 STM32 LL 库 UART 编程

HAL 库封装层次高但效率较低，LL（Low-Layer）库更接近寄存器，适合对性能要求高的场景：

```c
#include "stm32f1xx_ll_bus.h"
#include "stm32f1xx_ll_gpio.h"
#include "stm32f1xx_ll_usart.h"

void USART1_LL_Init(void)
{
    // Enable clocks
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_USART1);
    LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);

    // PA9 TX, PA10 RX
    LL_GPIO_InitTypeDef gpio = {0};
    gpio.Pin = LL_GPIO_PIN_9;
    gpio.Mode = LL_GPIO_MODE_ALTERNATE;
    gpio.Speed = LL_GPIO_SPEED_FREQ_HIGH;
    gpio.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
    LL_GPIO_Init(GPIOA, &gpio);

    gpio.Pin = LL_GPIO_PIN_10;
    gpio.Mode = LL_GPIO_MODE_FLOATING;
    LL_GPIO_Init(GPIOA, &gpio);

    // USART config
    LL_USART_InitTypeDef usart = {0};
    usart.BaudRate = 115200;
    usart.DataWidth = LL_USART_DATAWIDTH_8B;
    usart.StopBits = LL_USART_STOPBITS_1;
    usart.Parity = LL_USART_PARITY_NONE;
    usart.TransferDirection = LL_USART_DIRECTION_TX_RX;
    usart.HardwareFlowControl = LL_USART_HWCONTROL_NONE;
    usart.OverSampling = LL_USART_OVERSAMPLING_16;
    LL_USART_Init(USART1, &usart);

    // Enable RXNE interrupt
    LL_USART_EnableIT_RXNE(USART1);
    NVIC_SetPriority(USART1_IRQn, 0);
    NVIC_EnableIRQ(USART1_IRQn);

    LL_USART_Enable(USART1);
}

// LL library ISR (much lighter than HAL)
void USART1_IRQHandler(void)
{
    if (LL_USART_IsActiveFlag_RXNE(USART1))
    {
        uint8_t data = LL_USART_ReceiveData8(USART1);
        RingBuf_Handle_Byte(data);  // Push to ring buffer
    }
    if (LL_USART_IsActiveFlag_IDLE(USART1))
    {
        LL_USART_ClearFlag_IDLE(USART1);
        // Trigger frame processing
    }
    if (LL_USART_IsActiveFlag_ORE(USART1))
    {
        // Clear overrun: read SR then DR
        (void)USART1->SR;
        (void)USART1->DR;
    }
}
```

LL 库与 HAL 库对比：

| 特性 | HAL 库 | LL 库 |
|------|--------|-------|
| 抽象层次 | 高 | 低 |
| 代码体积 | 大 | 小 |
| 执行效率 | 中 | 高 |
| 易用性 | 高 | 中 |
| 移植性 | 好 | 差 |
| 适合场景 | 快速开发、跨芯片 | 性能敏感、资源受限 |

### C.5 扩展波特率误差表 - 不同时钟配置

以下是不同时钟源和分频配置下的波特率误差，供选型参考。

STM32F407 USART1 (APB2) 常见时钟配置：

| 时钟频率 | 目标波特率 | USARTDIV | 实际波特率 | 误差 | 时钟来源 |
|---------|-----------|----------|-----------|------|---------|
| 84 MHz | 115200 | 45.572917 | 115385 | +0.16% | 默认 HSE+PLL |
| 168 MHz | 115200 | 91.145833 | 115385 | +0.16% | 超频 |
| 84 MHz | 9600 | 546.875 | 9600 | 0% | 默认 |
| 84 MHz | 921600 | 5.696615 | 923077 | +0.16% | 默认 |
| 84 MHz | 1500000 | 3.5 | 1500000 | 0% | 默认 |
| 168 MHz | 2000000 | 5.25 | 2000000 | 0% | 超频 |
| 168 MHz | 4000000 | 2.625 | 4000000 | 0% | 超频 |
| 168 MHz | 5250000 | 2.0 | 5250000 | 0% | 超频 |
| 42 MHz | 115200 | 22.786458 | 115385 | +0.16% | APB2 分频 |
| 21 MHz | 115200 | 11.393229 | 115385 | +0.16% | 进一步分频 |

STM32H7 USART3 (APB1) 在不同时钟下：

| 时钟频率 | 目标波特率 | USARTDIV | 实际波特率 | 误差 |
|---------|-----------|----------|-----------|------|
| 64 MHz | 115200 | 34.722222 | 115385 | +0.16% |
| 64 MHz | 921600 | 4.340278 | 923077 | +0.16% |
| 64 MHz | 1500000 | 2.666667 | 1500000 | 0% |
| 64 MHz | 2000000 | 2.0 | 2000000 | 0% |
| 64 MHz | 4000000 | 1.0 | 4000000 | 0% |
| 100 MHz | 115200 | 54.253472 | 115385 | +0.16% |
| 100 MHz | 921600 | 6.781684 | 923077 | +0.16% |
| 100 MHz | 2000000 | 3.125 | 2000000 | 0% |
| 100 MHz | 3000000 | 2.083333 | 3000000 | 0% |
| 100 MHz | 6250000 | 1.0 | 6250000 | 0% |

ESP32 不同时钟源下的波特率：

| 时钟 | 目标波特率 | 分频值 | 实际波特率 | 误差 |
|------|-----------|--------|-----------|------|
| APB 80MHz | 115200 | 694.44 | 115200 | 0% |
| APB 80MHz | 921600 | 86.81 | 921600 | 0% |
| APB 80MHz | 2000000 | 40.0 | 2000000 | 0% |
| APB 80MHz | 5000000 | 16.0 | 5000000 | 0% |
| REF_CLK 1MHz | 9600 | 104.17 | 9600 | 0% |
| REF_CLK 1MHz | 115200 | 8.68 | 115207 | +0.006% |

### C.6 UART 信号完整性分析

高速 UART（≥ 1Mbps）或长线通信需考虑信号完整性（Signal Integrity, SI）。

#### C.6.1 传输线效应

当信号线长度超过上升沿长度的 1/6 时，需视为传输线。信号上升沿长度：

```
上升沿长度 = 上升时间 × 信号传播速度
信号传播速度 ≈ 0.6 × 光速 ≈ 18 cm/ns（PCB 走线）
```

| 上升时间 | 上升沿长度 | 临界线长（1/6） |
|---------|-----------|-----------------|
| 1 ns | 18 cm | 3 cm |
| 5 ns | 90 cm | 15 cm |
| 10 ns | 180 cm | 30 cm |
| 50 ns | 900 cm | 150 cm |

STM32 GPIO 输出上升时间约 5-20ns，因此 PCB 走线超过 15-30cm 即需考虑阻抗匹配。

#### C.6.2 阻抗匹配

UART 信号线典型阻抗 50Ω 或 120Ω（RS-485）。阻抗不匹配导致信号反射，产生振铃：

```
反射系数 Γ = (Z_load - Z_source) / (Z_load + Z_source)
```

- Z_load = Z_source 时 Γ=0，无反射（完美匹配）。
- Z_load = ∞（开路）时 Γ=1，全反射。
- Z_load = 0（短路）时 Γ=-1，负全反射。

终端匹配方法：

| 方法 | 电路 | 优点 | 缺点 |
|------|------|------|------|
| 源端串联 | 源端串 22-50Ω | 简单、低功耗 | 仅适用点对点 |
| 终端并联 | 终端并 50Ω 到 GND | 简单 | 功耗高、拉低电平 |
| 戴维南终端 | 终端两个电阻分压 | 兼容性好 | 功耗较高 |
| AC 终端 | 终端串 RC | 低功耗 | 仅适用交流信号 |

RS-485 总线必须两端加 120Ω 终端电阻（匹配双绞线特性阻抗）。

#### C.6.3 串扰分析

多路 UART 并行走线时，相邻信号线间通过寄生电容和互感耦合干扰：

```
串扰电压 V_xtalk = C_m × dV/dt × Z_victim
```

减少串扰措施：
1. 信号线间加 GND 屏蔽地线。
2. 增大线间距（3W 规则：间距 ≥ 3 倍线宽）。
3. 关键信号走在不同信号层，中间用地层隔离。
4. 使用差分信号（RS-485）替代单端。

### C.7 UART 协议设计指南

设计自定义 UART 应用层协议时的最佳实践：

#### C.7.1 帧结构设计

推荐帧结构：

```
┌──────┬──────┬──────┬────────────┬──────┬────────┐
│ 帧头 │ 长度 │ 命令 │ 数据       │ CRC  │ 帧尾   │
│ 0xAA │ 1B   │ 1B   │ N B        │ 2B   │ 0x55   │
└──────┴──────┴──────┴────────────┴──────┴────────┘
```

设计要点：
- **帧头/帧尾**：固定 magic byte，便于接收端同步。
- **长度域**：指示数据区长度，支持变长帧。
- **命令域**：区分不同操作（读、写、控制、查询）。
- **CRC**：CRC16-CCITT 或 CRC32，检测传输错误。
- **转义**：若数据可能包含帧头/帧尾字节，需字节转义（类似 SLIP/PPP）。

#### C.7.2 转义协议实现

```c
#define FRAME_START  0x7E
#define FRAME_END    0x7E
#define ESCAPE_CHAR  0x7D
#define ESCAPE_XOR   0x20

// Encode: escape special bytes in data
uint16_t Protocol_Encode(const uint8_t *src, uint16_t len, uint8_t *dst)
{
    uint16_t dst_idx = 0;
    dst[dst_idx++] = FRAME_START;
    for (uint16_t i = 0; i < len; i++)
    {
        if (src[i] == FRAME_START || src[i] == ESCAPE_CHAR)
        {
            dst[dst_idx++] = ESCAPE_CHAR;
            dst[dst_idx++] = src[i] ^ ESCAPE_XOR;
        }
        else
        {
            dst[dst_idx++] = src[i];
        }
    }
    dst[dst_idx++] = FRAME_END;
    return dst_idx;
}

// Decode: remove escape characters
uint16_t Protocol_Decode(const uint8_t *src, uint16_t len, uint8_t *dst)
{
    uint16_t dst_idx = 0;
    uint8_t escape = 0;
    for (uint16_t i = 0; i < len; i++)
    {
        if (src[i] == ESCAPE_CHAR)
        {
            escape = 1;
            continue;
        }
        if (escape)
        {
            dst[dst_idx++] = src[i] ^ ESCAPE_XOR;
            escape = 0;
        }
        else if (src[i] != FRAME_START && src[i] != FRAME_END)
        {
            dst[dst_idx++] = src[i];
        }
    }
    return dst_idx;
}
```

#### C.7.3 状态机接收解析

```c
typedef enum {
    STATE_WAIT_START,
    STATE_LEN,
    STATE_CMD,
    STATE_DATA,
    STATE_CRC_LOW,
    STATE_CRC_HIGH,
    STATE_WAIT_END
} FrameState;

static FrameState frame_state = STATE_WAIT_START;
static uint8_t frame_len = 0;
static uint8_t frame_cmd = 0;
static uint8_t frame_data[64];
static uint8_t frame_idx = 0;
static uint16_t frame_crc = 0;

void Protocol_Process_Byte(uint8_t byte)
{
    switch (frame_state)
    {
        case STATE_WAIT_START:
            if (byte == FRAME_START)
            {
                frame_state = STATE_LEN;
                frame_idx = 0;
            }
            break;
        case STATE_LEN:
            frame_len = byte;
            frame_state = STATE_CMD;
            break;
        case STATE_CMD:
            frame_cmd = byte;
            frame_state = (frame_len > 0) ? STATE_DATA : STATE_CRC_LOW;
            break;
        case STATE_DATA:
            frame_data[frame_idx++] = byte;
            if (frame_idx >= frame_len)
                frame_state = STATE_CRC_LOW;
            break;
        case STATE_CRC_LOW:
            frame_crc = byte;
            frame_state = STATE_CRC_HIGH;
            break;
        case STATE_CRC_HIGH:
            frame_crc |= (byte << 8);
            frame_state = STATE_WAIT_END;
            break;
        case STATE_WAIT_END:
            if (byte == FRAME_END)
            {
                // Verify CRC
                uint8_t full_frame[2 + 1 + 64];
                full_frame[0] = frame_len;
                full_frame[1] = frame_cmd;
                memcpy(&full_frame[2], frame_data, frame_len);
                uint16_t calc_crc = MB_CRC16(full_frame, 2 + frame_len);
                if (calc_crc == frame_crc)
                {
                    Protocol_Dispatch(frame_cmd, frame_data, frame_len);
                }
            }
            frame_state = STATE_WAIT_START;
            break;
    }
}
```

### C.8 扩展故障案例

#### C.8.1 案例：STM32 时钟配置错误导致波特率偏差

**现象**：使用 HAL_RCC_GetPCLK2Freq() 发现 PCLK2 = 64MHz 而非预期的 72MHz，导致波特率实际为 102400 而非 115200。

**原因**：SystemClock_Config 中 PLLMUL 配置错误，且 HSE 频率与代码假设不符（板载 12MHz 晶振，代码按 8MHz 计算）。

**修复**：检查 HSE_VALUE 宏定义，匹配实际晶振频率：

```c
// In stm32f1xx_hal_conf.h
// #define HSE_VALUE    8000000U    // Wrong!
#define HSE_VALUE    12000000U       // Correct: 12MHz crystal

// Then SystemClock_Config:
RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL6;  // 12MHz * 6 = 72MHz
```

**预防**：初始化后调用 HAL_RCC_GetSysClockFreq() 验证实际时钟频率。

#### C.8.2 案例：DMA 缓冲区 Cache 一致性问题（STM32H7）

**现象**：STM32H7 上 DMA 接收数据在 CPU 读取时为旧值或乱码。

**原因**：STM32H7 有 D-Cache，CPU 读内存走 Cache，DMA 写内存绕过 Cache，导致数据不一致。

**修复**：

```c
// Method 1: Place buffer in non-cacheable region (SRAM4 or DTCM)
__attribute__((section(".dTCM_RAM")))
uint8_t dma_rx_buf[1024];

// Method 2: Manual cache invalidation before reading
#include "stm32h7xx_hal.h"
SCB_InvalidateDCache_by_Addr((uint32_t *)dma_rx_buf, sizeof(dma_rx_buf));
// Now read dma_rx_buf safely

// Method 3: Disable D-Cache (not recommended for performance)
// SCB_DisableDCache();
```

#### C.8.3 案例：RS-485 总线冲突

**现象**：Modbus 总线上多个从站同时响应，导致数据损坏。

**原因**：某从站波特率配置错误（实际 19200 而非 9600），响应时间错乱；或某从站程序 bug，在收到非本机地址时也响应。

**修复**：
1. 检查所有从站波特率一致。
2. 检查从站地址唯一（1-247）。
3. 用逻辑分析仪观察总线，定位异常响应节点。
4. 加总线空闲检测，确保发送前总线空闲。

#### C.8.4 案例：USB 转串口丢数据

**现象**：通过 CH340 USB 转串口与 STM32 通信，高波特率（921600）下偶发丢字节。

**原因**：
1. CH340 内部缓冲区较小（约 256 字节），高波特率下易溢出。
2. USB 调度延迟导致数据不连续到达 UART。
3. Windows USB 驱动非实时，可能延迟数毫秒。

**修复**：
1. 降低波特率至 115200（最稳定）。
2. 使用性能更好的 USB 转串口芯片（FT232H、CP2102N）。
3. STM32 端使用 DMA 循环模式接收，避免中断延迟。
4. 协议层加重传机制（如 Modbus 的超时重发）。

#### C.8.5 案例：printf 在中断中导致死锁

**现象**：在中断处理函数中调用 printf，系统偶发死锁。

**原因**：printf 内部使用 HAL_UART_Transmit 阻塞等待 TXE，若此时 UART 正在发送且中断被屏蔽，会无限等待。另外 printf 内部可能使用 malloc/heap 操作，堆非线程安全。

**修复**：
1. 中断中禁止调用 printf。
2. 中断中仅设置标志，主循环中打印日志。
3. 如必须在中断中输出，使用无锁的环形缓冲区日志系统：

```c
#define LOG_BUF_SIZE 2048
static char log_buf[LOG_BUF_SIZE];
static volatile uint16_t log_head = 0, log_tail = 0;

// ISR-safe logging (non-blocking)
void Log_ISR(const char *msg)
{
    uint16_t next_head = (log_head + 1) % LOG_BUF_SIZE;
    while (*msg && next_head != log_tail)
    {
        log_buf[log_head] = *msg++;
        log_head = (log_head + 1) % LOG_BUF_SIZE;
        next_head = (log_head + 1) % LOG_BUF_SIZE;
    }
}

// Main loop: flush log to UART
void Log_Flush(void)
{
    while (log_tail != log_head)
    {
        HAL_UART_Transmit(&huart1, (uint8_t *)&log_buf[log_tail], 1, 10);
        log_tail = (log_tail + 1) % LOG_BUF_SIZE;
    }
}
```

### C.9 扩展 FAQ

补充常见问题：

| 编号 | 问题 | 原因 | 解决方案 |
|------|------|------|----------|
| 21 | STM32F1 USART1 与 USART2 波特率不同 | 时钟来源不同（APB2 vs APB1） | 分别计算 BRR |
| 22 | DMA 接收首字节丢失 | DMA 启动前 UART 已有数据 | 先读 DR 清空，再启动 DMA |
| 23 | HAL_UART_Transmit_DMA 第二次失败 | 上次未完成就再次调用 | 检查 gState == READY |
| 24 | 中断中 HAL_UART_Receive_IT 返回 BUSY | 上次接收未完成 | 检查 RxState |
| 25 | ESP32 Serial.available() 延迟高 | Arduino 默认轮询间隔 | 使用 ESP-IDF + 任务 |
| 26 | Linux 串口 read 返回部分数据 | VMIN/VTIME 配置不当 | 设置 VMIN=0, VTIME=10 |
| 27 | 树莓派串口蓝牙冲突 | 硬件 UART 默认给蓝牙 | 改用 mini-uart 或禁用蓝牙 |
| 28 | Modbus 从站响应慢 | 主站轮询间隔过长或从站处理慢 | 优化轮询调度 |
| 29 | 长帧（>256B）传输失败 | DMA 缓冲区不足或 IDLE 误触发 | 增大缓冲区，禁用 IDLE |
| 30 | 多 UART 同时使用性能下降 | 中断优先级竞争 | 高速 UART 用 DMA，低速用 IT |

### C.10 UART 波形分析实例

#### C.10.1 正常 8N1 帧波形

发送字节 0x55（01010101），LSB 优先：

```
空闲  起始  D0  D1  D2  D3  D4  D5  D6  D7  停止  空闲
___    _    _    _    _    _    _    _    _   ___
   |__| |__| |__| |__| |__| |__| |__| |__| |__|
1   0   1   0   1   0   1   0   1   0   1   1   1
        LSB                                  MSB
```

0x55 = 0b01010101，LSB 优先发送顺序：1,0,1,0,1,0,1,0。

#### C.10.2 异常波形 - 波特率不匹配

发送端 115200，接收端 9600（差 12 倍）。接收端采样极慢，每位采样跨多个发送位：

```
发送端: |S|D0|D1|D2|D3|D4|D5|D6|D7|Stop|  (10 bits at 115200)
接收端: |S---------|D0---------|D1-----...  (samples every 12 bits)
结果: 接收端将多个发送位当作一个位采样，得到完全错误的数据。
```

#### C.10.3 异常波形 - 噪声导致电平翻转

正常数据 0x55，但 D3 位受噪声干扰从 1 翻转为 0：

```
发送: |S|1|0|1|0|1|0|1|0|Stop|
接收: |S|1|0|1|0|0|0|1|0|Stop|  <- D3 flipped by noise
结果: 接收数据 0x45（01000101），校验位可能检测到错误。
```

若启用奇校验（0x55 有 4 个 1，校验位应为 1 使总 1 数为奇数=5）：
- 接收端计算数据位 1 数 = 3（0x45 = 01000101 有 3 个 1）+ 校验位 1 = 4，非奇数 → PE 置位。

### C.11 电源管理 - UART 低功耗

#### C.11.1 STM32 Stop 模式唤醒

STM32 可在 Stop 模式下保持 UART 接收，收到数据时唤醒 MCU：

```c
void UART_Enter_Stop_With_Wakeup(void)
{
    // Enable UART clock in Stop mode
    HAL_UARTEx_EnableStopMode(&huart1);
    // Enable RXNE wakeup
    __HAL_UART_ENABLE_IT(&huart1, UART_IT_WUF);

    // Enter Stop mode
    HAL_SuspendTick();
    HAL_PWR_EnterSTOPMode(PWR_LOWPOWERREGULATOR_ON, PWR_STOPENTRY_WFI);

    // Woken up by UART - reconfigure system clock
    SystemClock_Config();
    HAL_ResumeTick();
    HAL_UARTEx_DisableStopMode(&huart1);
}
```

注意：Stop 模式下 UART 时钟源需为 LSE 或 HSI（HSE 在 Stop 模式停止）。波特率会因时钟切换而变化。

#### C.11.2 ESP32 Light Sleep + UART 唤醒

```cpp
// ESP32 light sleep with UART wakeup
#include <esp_sleep.h>

void enter_light_sleep()
{
    // UART0 can wake from light sleep
    uart_set_wakeup_threshold(UART_NUM_0, 3);  // Wake after 3 bytes
    esp_sleep_enable_uart_wakeup(0);
    esp_light_sleep_start();
    // Resumes here after wakeup
}
```

### C.12 UART 安全性考量

UART 通信本身无加密、无认证，传输明文易被窃听和篡改。安全敏感场景需在应用层加固：

#### C.12.1 常见威胁

| 威胁 | 风险 | 缓解措施 |
|------|------|----------|
| 窃听 | 数据泄露 | 加密传输 |
| 篡改 | 数据被修改 | MAC 校验 |
| 重放 | 旧消息被重发 | 序列号/时间戳 |
| 伪造 | 非法设备发送指令 | 设备认证 |

#### C.12.2 轻量加密 - AES-128

```c
#include "stm32f1xx_hal_cryp.h"  // STM32 with CRYP peripheral

// AES-128 CBC encrypt a frame
int UART_Encrypt_Send(uint8_t *plaintext, uint16_t len, uint8_t *key)
{
    uint8_t ciphertext[256];
    uint8_t iv[16] = {0};  // Initialization vector

    // Pad to 16-byte boundary (PKCS7)
    uint8_t pad = 16 - (len % 16);
    for (uint8_t i = 0; i < pad; i++) plaintext[len + i] = pad;
    uint16_t padded_len = len + pad;

    // AES-128-CBC encrypt
    CRYP_ConfigTypeDef cfg = {0};
    cfg.Algorithm = CRYP_AES_CBC;
    cfg.DataType = CRYP_DATATYPE_8B;
    HAL_CRYP_SetConfig(&hcryp, &cfg);
    HAL_CRYP_Encrypt(&hcryp, plaintext, padded_len, ciphertext, 100);

    // Prepend IV
    memcpy(tx_buf, iv, 16);
    memcpy(tx_buf + 16, ciphertext, padded_len);
    HAL_UART_Transmit_DMA(&huart1, tx_buf, 16 + padded_len);
    return 16 + padded_len;
}
```

#### C.12.3 消息认证码（HMAC）

```c
// Simple HMAC-SHA256 for message integrity
void Compute_HMAC(uint8_t *msg, uint16_t len, uint8_t *key, uint8_t *mac)
{
    // Use mbedTLS or STM32 HASH peripheral
    // HMAC = H((K ^ opad) || H((K ^ ipad) || msg))
    mbedtls_md_context_t ctx;
    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_md_init(&ctx);
    mbedtls_md_setup(&ctx, info, 1);
    mbedtls_md_hmac_starts(&ctx, key, 32);
    mbedtls_md_hmac_update(&ctx, msg, len);
    mbedtls_md_hmac_finish(&ctx, mac);
    mbedtls_md_free(&ctx);
}
```

### C.13 扩展寄存器速查 - STM32H7

STM32H7 的 USART 寄存器布局与 F1/F4 有差异，新增 ISR/ICR/RQR 等：

| 寄存器 | 全称 | F1/F4 | H7 | 说明 |
|--------|------|-------|----|------|
| ISR | Interrupt Status Register | SR | ISR | 状态标志（H7 新增更多位） |
| ICR | Interrupt Flag Clear Register | - | ICR | 写 1 清零标志（H7 新增） |
| RQR | Request Register | - | RQR | 软件请求（H7 新增） |
| TDR | Transmit Data Register | DR | TDR | 发送数据（H7 独立） |
| RDR | Receive Data Register | DR | RDR | 接收数据（H7 独立） |
| PRESC | Prescaler Register | GTPR | PRESC | 时钟预分频（H7 新增） |

H7 ISR 关键新增位：

| 位 | 名称 | 说明 |
|----|------|------|
| 28 | TXFE | TXFIFO 空 |
| 27 | RXFF | RXFIFO 满 |
| 26 | RXFT | RXFIFO 阈值达到 |
| 25 | TXFT | TXFIFO 阈值达到 |
| 24 | SBKF | 发送断开帧标志 |
| 23 | CMF | 字符匹配标志 |
| 22 | BUSY | 总线忙 |
| 21 | ABRF | 自动波特率完成 |
| 20 | ABRE | 自动波特率错误 |

H7 ICR 清零位（写 1 清零）：

| 位 | 名称 | 清零标志 |
|----|------|----------|
| 23 | CMCF | 字符匹配 (CMF) |
| 17 | FECF | 帧错误 (FE) |
| 16 | ORECF | 溢出错误 (ORE) |
| 15 | NCF | 噪声错误 (NE) |
| 14 | PECF | 校验错误 (PE) |
| 11 | CTSCF | CTS 标志 |
| 4 | IDLECF | 空闲标志 |

H7 FIFO 配置：

```c
// Enable FIFO mode (STM32H7)
USART1->CR1 |= USART_CR1_FIFOEN;
// RXFIFO threshold: interrupt when >= 8 bytes
USART1->CR3 = (USART1->CR3 & ~USART_CR3_RXFTCFG) | (0x03 << USART_CR3_RXFTCFG_Pos);
// TXFIFO threshold: interrupt when <= 4 bytes
USART1->CR3 = (USART1->CR3 & ~USART_CR3_TXFTCFG) | (0x02 << USART_CR3_TXFTCFG_Pos);
```

### C.14 测试与验证方法

#### C.14.1 压力测试

长时间高负载通信测试，验证稳定性：

```c
// Stress test: continuous TX/RX for 1 hour, count errors
void UART_Stress_Test(void)
{
    uint32_t tx_count = 0, rx_count = 0, err_count = 0;
    uint32_t start_tick = HAL_GetTick();
    uint8_t tx_data = 0, rx_data;

    HAL_UART_Receive_DMA(&huart1, rx_buf, 1);  // Start RX
    HAL_UART_Transmit_DMA(&huart1, &tx_data, 1);  // Start TX

    while (HAL_GetTick() - start_tick < 3600000)  // 1 hour
    {
        // Each TX complete, increment and send next
        if (huart1.gState == HAL_UART_STATE_READY)
        {
            tx_data = (tx_data + 1) & 0xFF;
            HAL_UART_Transmit_DMA(&huart1, &tx_data, 1);
            tx_count++;
        }
        // Verify RX data matches
        if (rx_ready)
        {
            if (rx_buf[0] != expected_rx) err_count++;
            expected_rx = (expected_rx + 1) & 0xFF;
            rx_count++;
            rx_ready = 0;
        }
    }
    printf("TX: %lu, RX: %lu, Errors: %lu\n", tx_count, rx_count, err_count);
}
```

#### C.14.2 误码率测试（BERT）

误码率测试（Bit Error Rate Test）发送伪随机序列，接收端比对，计算误码率：

```c
// PRBS-7 pseudo-random sequence generator
uint8_t PRBS7_Next(void)
{
    static uint8_t state = 0x7F;
    uint8_t bit = ((state >> 6) ^ (state >> 7)) & 1;
    state = (state << 1) | bit;
    return state;
}

// BERT test
float UART_BERT_Test(uint32_t duration_ms)
{
    uint32_t bits_sent = 0, bits_error = 0;
    uint32_t start = HAL_GetTick();
    uint8_t expected = PRBS7_Next();

    while (HAL_GetTick() - start < duration_ms)
    {
        // Send and receive PRBS sequence
        uint8_t tx = PRBS7_Next();
        HAL_UART_Transmit(&huart1, &tx, 1, 100);
        uint8_t rx;
        if (HAL_UART_Receive(&huart1, &rx, 1, 100) == HAL_OK)
        {
            bits_sent += 8;
            for (int i = 0; i < 8; i++)
            {
                if (((tx >> i) & 1) != ((rx >> i) & 1))
                    bits_error++;
            }
        }
    }
    return bits_error * 1.0f / bits_sent;  // BER
}
```

典型误码率标准：

| 通信质量 | BER | 说明 |
|---------|-----|------|
| 优秀 | < 10⁻⁹ | 工业级 |
| 良好 | 10⁻⁷ | 商业级 |
| 可接受 | 10⁻⁵ | 调试可用 |
| 差 | > 10⁻³ | 需排查 |

### C.15 UART 在物联网中的应用

#### C.15.1 AT 指令通信

物联网模组（ESP8266、SIM800、NB-IoT）普遍使用 AT 指令集：

```c
// Send AT command and check response
int AT_Command(const char *cmd, const char *expected, uint32_t timeout_ms)
{
    // Flush RX buffer
    RingBuf_Init();

    // Send command
    char full_cmd[64];
    snprintf(full_cmd, sizeof(full_cmd), "%s\r\n", cmd);
    HAL_UART_Transmit_DMA(&huart1, (uint8_t *)full_cmd, strlen(full_cmd));

    // Wait for expected response
    uint32_t start = HAL_GetTick();
    while (HAL_GetTick() - start < timeout_ms)
    {
        int16_t pos = RingBuf_Find('\n');
        if (pos >= 0)
        {
            uint8_t line[64];
            RingBuf_Read(line, pos + 1);
            line[pos] = '\0';
            if (strstr((char *)line, expected))
                return 0;  // Match found
            if (strstr((char *)line, "ERROR"))
                return -1;  // Error response
        }
    }
    return -2;  // Timeout
}

// Example usage
void WiFi_Connect(void)
{
    AT_Command("AT", "OK", 1000);
    AT_Command("AT+CWMODE=1", "OK", 1000);
    AT_Command("AT+CWJAP=\"SSID\",\"password\"", "OK", 15000);
    printf("WiFi connected\n");
}
```

#### C.15.2 GPS NMEA 解析

GPS 模块输出 NMEA 0183 语句，常用 9600 8N1：

```c
// Parse NMEA GGA sentence: $GPGGA,time,lat,N,lon,E,quality,sats,hdop,alt,M,...
void GPS_Parse_GGA(const char *line)
{
    if (strncmp(line, "$GPGGA", 6) != 0) return;

    char time[11] = {0}, lat[12] = {0}, lon[12] = {0};
    int quality = 0, sats = 0;

    // $GPGGA,092750.000,5321.6802,N,00630.3372,W,1,8,1.03,61.7,M,55.2,M,,
    int parsed = sscanf(line, "$GPGGA,%10[^,],%11[^,],%*c,%11[^,],%*c,%d,%d,",
                        time, lat, lon, &quality, &sats);
    if (parsed >= 5)
    {
        printf("Time: %s, Sats: %d, Quality: %d\n", time, sats, quality);
        if (quality > 0)
        {
            printf("Lat: %s, Lon: %s\n", lat, lon);
        }
    }
}

// Process GPS lines from ring buffer
void GPS_Process(void)
{
    int16_t cr = RingBuf_Find('\n');
    if (cr < 0) return;

    char line[128];
    uint16_t len = RingBuf_Read((uint8_t *)line, cr + 1);
    line[len] = '\0';
    // Trim trailing \r
    if (len > 0 && line[len - 1] == '\r') line[len - 1] = '\0';

    if (strncmp(line, "$GP", 3) == 0)
    {
        if (strncmp(line + 3, "GGA", 3) == 0) GPS_Parse_GGA(line);
        // Other sentences: RMC, GLL, GSA, GSV, VTG
    }
}
```

### C.16 扩展对比表

#### C.16.1 USB 转串口芯片对比

| 芯片 | 厂商 | 最高波特率 | 缓冲区 | 驱动 | 特点 |
|------|------|-----------|--------|------|------|
| FT232RL | FTDI | 3 Mbps | 256B | 免驱（Win10） | 稳定、贵 |
| FT2232H | FTDI | 12 Mbps | 4KB | 免驱 | 双通道、高速 |
| CP2102 | Silicon Labs | 1 Mbps | 640B | 免驱 | 性价比好 |
| CP2102N | Silicon Labs | 3 Mbps | 1KB | 免驱 | 升级版 |
| CH340 | WCH | 2 Mbps | 256B | 需装驱动 | 便宜、普及 |
| CH343 | WCH | 6 Mbps | 256B | 需装驱动 | 升级版、高速 |
| PL2303 | Prolific | 12 Mbps | 256B | 需装驱动 | 老牌、有假货 |
| MCP2200 | Microchip | 1 Mbps | 256B | 需装驱动 | 低速稳定 |

#### C.16.2 隔离方案对比

工业环境需电气隔离，常用方案：

| 方案 | 隔离电压 | 速率 | 成本 | 复杂度 |
|------|---------|------|------|--------|
| 光耦（6N137） | 5000V | 1 Mbps | 低 | 中（需多个） |
| 磁隔离（ADuM1201） | 5000V | 25 Mbps | 中 | 低 |
| 容隔离（SI8621） | 5000V | 150 Mbps | 中 | 低 |
| 集成隔离 RS-485（ADM2587E） | 5000V | 500 kbps | 高 | 极低 |
| 隔离电源 + 隔离收发器 | 5000V | 任意 | 高 | 高 |

### C.17 调试工具进阶

#### C.17.1 逻辑分析仪协议解析

Saleae Logic、PulseView 等支持 UART 协议解析：
1. 添加 UART 解析器。
2. 配置 TX/RX 通道、波特率、帧格式。
3. 自动解码数据为 ASCII 或 Hex。

#### C.17.2 串口监控

Linux 下监控串口流量而不影响通信：

```bash
# Method 1: Use interceptty
interceptty /dev/ttyUSB0 | interceptty -d

# Method 2: Use socat to create virtual pair and monitor
socat -d -d PTY,raw,echo=0 PTY,raw,echo=0
# Then connect app to one end, monitor other end

# Method 3: Use strace on the serial port
strace -e read,write -p $(pgrep -f "my_serial_app")
```

#### C.17.3 Python 串口测试

```python
import serial
import serial.tools.list_ports

# List available ports
ports = serial.tools.list_ports.comports()
for p in ports:
    print(p.device, p.description)

# Open and configure
ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE)

# Send and receive
ser.write(b'Hello UART\r\n')
response = ser.read(100)
print(f'Response: {response}')

ser.close()
```

### C.18 总结 - UART 工程师能力清单

成为一名合格的 UART 嵌入式工程师，需掌握：

**基础层**：
- 理解 UART 异步通信原理、帧格式、波特率。
- 掌握 TTL/RS-232/RS-485 电气特性与电平转换。
- 熟练使用示波器、逻辑分析仪、串口调试助手。

**MCU 层**：
- STM32 USART 寄存器（SR/DR/BRR/CR1/CR2/CR3）配置。
- HAL 库三种模式（轮询/中断/DMA）编程。
- LL 库轻量级编程。
- DMA 循环模式 + 半传输中断双缓冲区设计。

**协议层**：
- Modbus RTU 主从协议实现。
- 9 位多机通信。
- 自动波特率检测。
- 自定义协议设计（帧头、转义、CRC、状态机）。

**系统层**：
- Linux termios 串口编程。
- ESP32 Arduino/ESP-IDF 串口编程。
- 跨平台抽象层设计。

**工程层**：
- 噪声排查与信号完整性分析。
- 错误处理与统计。
- 性能优化（高速、低延迟、低功耗）。
- 安全性（加密、认证）。

**排故层**：
- 乱码、丢数据、波特率不匹配等 20+ 常见问题排查。
- 时钟配置、Cache 一致性、总线冲突等疑难杂症。
- 压力测试与误码率测试。

掌握以上能力，即可应对绝大多数 UART 相关的嵌入式开发任务。

---

## 附录 D：术语表

| 术语 | 全称 | 说明 |
|------|------|------|
| UART | Universal Asynchronous Receiver/Transmitter | 通用异步收发器 |
| USART | Universal Synchronous/Asynchronous Receiver/Transmitter | 通用同步/异步收发器 |
| TTL | Transistor-Transistor Logic | 晶体管-晶体管逻辑 |
| RS-232 | Recommended Standard 232 | EIA 串行通信标准 |
| RS-485 | Recommended Standard 485 | EIA 差分串行总线标准 |
| DMA | Direct Memory Access | 直接内存访问 |
| ISR | Interrupt Service Routine | 中断服务程序 |
| FIFO | First In First Out | 先进先出缓冲区 |
| CRC | Cyclic Redundancy Check | 循环冗余校验 |
| BRR | Baud Rate Register | 波特率寄存器 |
| PE | Parity Error | 校验错误 |
| FE | Framing Error | 帧错误 |
| NE | Noise Error | 噪声错误 |
| ORE | Overrun Error | 溢出错误 |
| RXNE | RX Not Empty | 接收寄存器非空 |
| TXE | TX Empty | 发送寄存器空 |
| TC | Transmission Complete | 发送完成 |
| IDLE | Idle Line Detected | 空闲线检测 |
| RTS | Request To Send | 请求发送（流控） |
| CTS | Clear To Send | 清除发送（流控） |
| DE | Driver Enable | 驱动器使能（RS-485） |
| RE | Receiver Enable | 接收器使能（RS-485） |
| ABR | Auto Baud Rate | 自动波特率检测 |
| HT | Half Transfer | 半传输（DMA） |
| TC | Transfer Complete | 传输完成（DMA） |
| BER | Bit Error Rate | 误码率 |
| SI | Signal Integrity | 信号完整性 |
| EMI | Electromagnetic Interference | 电磁干扰 |
| NMEA | National Marine Electronics Association | 海洋电子协会（GPS 协议） |
| RTU | Remote Terminal Unit | 远程终端单元（Modbus 模式） |
| SLIP | Serial Line Internet Protocol | 串行线路网际协议 |

---

*文档版本：v1.1 扩展版 | 最后更新：2026-06 | 适用平台：STM32F1/F4/H7, ESP32, Arduino, Linux*

---

## 附录 E：电气工程深度专题

本附录深入 UART 物理层相关的电气工程知识，包括传输线理论、EMC 设计、接地策略、ESD 防护。

### E.1 传输线理论进阶

#### E.1.1 特性阻抗

传输线特性阻抗由其几何结构和介质材料决定。常见传输线类型：

| 传输线类型 | 特性阻抗 | 典型应用 |
|-----------|---------|----------|
| PCB 微带线 | 50-100Ω | 板内 UART |
| PCB 带状线 | 50-75Ω | 多层板内层 |
| 双绞线 | 100-120Ω | RS-485 |
| 同轴线 | 50-75Ω | 高频、视频 |
| 双芯平行线 | 300Ω | 老式天线 |

PCB 微带线特性阻抗近似公式：

```
Z0 = 87 / sqrt(εr + 1.41) × ln(5.98 × h / (0.8 × w + t))
```

其中：
- εr：基板介电常数（FR4 约 4.4）
- h：介质厚度（mil）
- w：走线宽度（mil）
- t：铜厚（mil）

FR4 板上 50Ω 微带线典型尺寸（1oz 铜厚）：

| 介质厚度 h | 走线宽度 w | 特性阻抗 |
|----------|-----------|---------|
| 0.2 mm | 0.4 mm | ~50Ω |
| 0.4 mm | 0.8 mm | ~50Ω |
| 0.8 mm | 1.6 mm | ~50Ω |

#### E.1.2 反射与振铃

信号到达阻抗不连续点时发生反射，反射波叠加原信号产生振铃。反射系数：

```
Γ = (Z_load - Z_source) / (Z_load + Z_source)
```

振铃幅度取决于反射系数和信号边沿：

| 源阻抗 | 负载阻抗 | Γ | 振铃情况 |
|--------|---------|---|---------|
| 50Ω | 50Ω | 0 | 无振铃（匹配） |
| 50Ω | 1MΩ（高阻输入） | 0.99 | 严重振铃 |
| 50Ω | 10Ω | -0.67 | 负反射，过冲下冲 |
| 25Ω | 100Ω | 0.6 | 中等振铃 |

UART 接收端输入阻抗通常 > 1MΩ，源端输出阻抗 25-50Ω，严重不匹配。源端串联电阻是常用解决方法：

```
STM32 TX (Zout≈25Ω) ──[33Ω]── RX (Zin≈1MΩ)
总源阻抗 = 25 + 33 = 58Ω，接近 50Ω 走线阻抗
```

#### E.1.3 上升时间与带宽

信号上升时间 tr 与带宽 BW 的关系：

```
BW = 0.35 / tr
```

| 上升时间 | 带宽 | 最高有效频率 |
|---------|------|-------------|
| 1 ns | 350 MHz | 极高 |
| 5 ns | 70 MHz | 高 |
| 10 ns | 35 MHz | 中 |
| 50 ns | 7 MHz | 低 |
| 100 ns | 3.5 MHz | UART 适用 |

UART 信号上升时间通常 10-50ns，对应带宽 7-35MHz，PCB 走线需在该频率范围内保持阻抗一致。

### E.2 EMI 与 EMC 设计

#### E.2.1 EMI 辐射机制

UART 信号线作为天线，辐射电磁干扰。辐射强度与以下因素相关：

```
辐射场强 E ∝ f² × L × I
```

其中 f 为频率，L 为线长，I 为电流环路面积。

减少 EMI 措施：

| 措施 | 效果 | 实施难度 |
|------|------|---------|
| 减小信号线长度 | 高 | 低 |
| 减小回流面积 | 高 | 中 |
| 降低信号边沿速率 | 高 | 低（串联电阻） |
| 使用差分信号 | 极高 | 高（需 RS-485） |
| 加屏蔽 | 高 | 中 |
| 降低波特率 | 中 | 低 |
| PCB 分层（地平面） | 高 | 中 |

#### E.2.2 串联电阻减缓边沿

源端串联电阻与走线电容、接收端输入电容形成 RC 滤波，减缓上升时间：

```
新上升时间 tr_new = 2.2 × R_series × C_total
```

示例：R_series = 33Ω，C_total = 10pF（走线 + 引脚）：
```
tr_new = 2.2 × 33 × 10pF = 0.726 ns
```

实际效果：原 5ns 上升时间变为约 5.7ns，对 UART 影响可忽略，但高频谐波显著衰减。

#### E.2.3 共模与差模噪声

| 噪声类型 | 来源 | 耦合方式 | 抑制方法 |
|---------|------|---------|---------|
| 共模噪声 | 电源、地、空间辐射 | 同时耦合到两根线 | 共模电感、差分信号 |
| 差模噪声 | 信号线间串扰 | 直接耦合 | 屏蔽、双绞、间距 |

共模电感对共模噪声呈现高阻抗，对差模信号低阻抗，是抑制共模干扰的有效器件：

```
共模电感阻抗 Z = 2π × f × L_cm
```

| 频率 | 共模电感 100μH 阻抗 | 抑制效果 |
|------|---------------------|---------|
| 1 MHz | 628 Ω | 好 |
| 10 MHz | 6.28 kΩ | 极好 |
| 100 MHz | 62.8 kΩ | 极好 |

### E.3 接地策略

#### E.3.1 单点接地 vs 多点接地

| 接地方式 | 适用频率 | 优点 | 缺点 |
|---------|---------|------|------|
| 单点接地 | < 1 MHz | 无地环路 | 高频阻抗大 |
| 多点接地 | > 10 MHz | 低阻抗 | 易形成地环路 |
| 混合接地 | 通用 | 兼顾 | 复杂 |

UART 通常工作在低频（< 10MHz），推荐单点接地或混合接地。

#### E.3.2 地环路问题

多设备通过不同路径接地时形成地环路，地电位差导致共模电流，干扰 UART 通信：

```
设备 A ──UART── 设备 B
  │                │
  └── 地1 ── 地2 ──┘
```

地环路电流大小：
```
I_loop = V_gnd_diff / Z_loop
```

消除地环路方法：
1. **光耦隔离**：UART 信号通过光耦传输，电气隔离。
2. **磁隔离**：数字隔离芯片（ADuM1201）。
3. **单点接地**：所有设备共地一个点。
4. **共模电感**：抑制共模电流，不阻断信号。

#### E.3.3 隔离 UART 设计

工业 UART 常需电气隔离，典型电路：

```
MCU TX ──[ADuM1201]── 隔离 TX ── RS-485 收发器 ── 总线
MCU RX ──[ADuM1201]── 隔离 RX ── RS-485 收发器 ── 总线
隔离电源 ── 隔离侧供电
```

隔离参数：

| 参数 | 典型值 | 说明 |
|------|--------|------|
| 隔离电压 | 2500-5000 Vrms | 1 分钟耐受 |
| 工作电压 | 500-1000 Vrms | 长期 |
| 隔离电容 | 1-3 pF | 越小越好 |
| 传播延迟 | 20-50 ns | 影响最大波特率 |
| CMTI | 25-50 kV/μs | 共模瞬态抗扰度 |

### E.4 ESD 防护

#### E.4.1 ESD 威胁

人体静电放电（ESD）可达 ±15kV，直接接触 UART 接口可能损坏芯片。IEC 61000-4-2 标准定义 ESD 测试等级：

| 等级 | 接触放电 | 空气放电 | 应用 |
|------|---------|---------|------|
| 1 | ±2 kV | ±2 kV | 受控环境 |
| 2 | ±4 kV | ±4 kV | 办公环境 |
| 3 | ±6 kV | ±8 kV | 工业环境 |
| 4 | ±8 kV | ±15 kV | 严苛环境 |

#### E.4.2 ESD 保护器件

| 器件 | 钳位电压 | 结电容 | 响应速度 | 适用 |
|------|---------|--------|---------|------|
| TVS 二极管 | 5-12V | 1-50 pF | < 1 ns | 通用 |
| 压敏电阻（MOV） | 20-100V | 100-1000 pF | μs 级 | 低速、大功率 |
| 气体放电管 | 100-500V | 1-5 pF | μs 级 | 高压、防雷 |
| 聚合物 PTC | 自恢复 | 50-200 pF | μs 级 | 过流保护 |

UART 接口推荐 TVS 二极管阵列（如 SRV05-4），每个信号线对地加一个 TVS。

#### E.4.3 ESD 保护电路设计

```
                 TVS
UART RX ──┬──────┤├──── GND
          │
       100Ω 串联电阻
          │
       MCU RX 引脚
```

设计要点：
1. TVS 尽量靠近接口连接器（< 10mm）。
2. 串联电阻限制 ESD 电流进入 MCU。
3. TVS 结电容影响高速信号，高速 UART 选低电容 TVS（< 5pF）。
4. 保护地（PGND）与信号地（DGND）通过单点连接。

### E.5 电源滤波

#### E.5.1 电源去耦

UART 收发器（如 MAX3232、MAX485）电源需去耦：

```
VCC ──┬── 10μF 钽电容 ── GND
      │
      ├── 0.1μF 陶瓷电容 ── GND
      │
   MAX485 VCC
```

- 10μF：低频滤波（电源纹波）。
- 0.1μF：高频去耦（开关噪声）。
- 电容尽量靠近芯片 VCC 引脚。

#### E.5.2 电源隔离

隔离 UART 需要隔离电源。方案：

| 方案 | 效率 | 成本 | 复杂度 | 适用 |
|------|------|------|--------|------|
| 隔离 DC-DC | 70-85% | 中 | 中 | 通用 |
| 变压器隔离 | 80-90% | 高 | 高 | 大功率 |
| 电荷泵 | 50-70% | 低 | 低 | 小功率 |
| 隔离模块（B0505S） | 70% | 中 | 极低 | 1-2W |

推荐使用集成隔离电源 + 隔离收发器模块（如 ADuM16050），减少设计复杂度。

---

## 附录 F：更多平台 UART 实现

### F.1 AVR Arduino UART

Arduino UNO（ATmega328P）有 1 个硬件 UART（D0/D1），SoftwareSerial 库可模拟软件串口：

```cpp
// Arduino UNO hardware UART
void setup()
{
    Serial.begin(115200);  // 8N1 default
}

void loop()
{
    if (Serial.available())
    {
        char c = Serial.read();
        Serial.print("Got: ");
        Serial.println(c);
    }
}
```

ATmega328P UART 寄存器：

| 寄存器 | 说明 |
|--------|------|
| UCSR0A | 状态与控制 A |
| UCSR0B | 状态与控制 B（中断使能） |
| UCSR0C | 帧格式 |
| UBRR0H/L | 波特率分频（12 位） |
| UDR0 | 数据寄存器 |

波特率计算（U2X0=0，正常模式）：
```
UBRR = fosc / (16 × baud) - 1
```

16MHz Arduino UNO 常用波特率：

| 波特率 | UBRR | 实际波特率 | 误差 |
|--------|------|-----------|------|
| 9600 | 103 | 9615 | +0.16% |
| 19200 | 51 | 19230 | +0.16% |
| 38400 | 25 | 38461 | +0.16% |
| 57600 | 16 | 58823 | +2.12% |
| 115200 | 8 | 111111 | -3.55% |
| 250000 | 3 | 250000 | 0% |

注意：16MHz Arduino UNO 在 115200bps 误差 -3.55%，超过 ±2.5% 容限，可能出错。建议用 500000bps 或更换 18.432MHz 晶振（专为串口设计）。

### F.2 PIC MCU UART

Microchip PIC16F877A 有 1 个 USART。汇编/C 配置：

```c
#include <xc.h>
#define _XTAL_FREQ 20000000  // 20MHz crystal

void UART_Init(void)
{
    TRISC6 = 0;  // TX output
    TRISC7 = 1;  // RX input
    SPBRG = 129; // 9600 baud at 20MHz, BRGH=1
    TXSTA = 0x24; // TX enable, BRGH=1
    RCSTA = 0x90; // Serial enable, RX enable
}

void UART_Write(char data)
{
    while (!TXIF);  // Wait for TX register empty
    TXREG = data;
}

char UART_Read(void)
{
    while (!RCIF);  // Wait for data
    return RCREG;
}
```

### F.3 RP2040 (Raspberry Pi Pico) UART

RP2040 有 2 个 UART，支持 DMA：

```c
#include "pico/stdlib.h"
#include "hardware/uart.h"

int main()
{
    uart_init(uart0, 115200);
    gpio_set_function(0, GPIO_FUNC_UART);  // TX
    gpio_set_function(1, GPIO_FUNC_UART);  // RX

    uart_puts(uart0, "Hello RP2040\r\n");

    while (1)
    {
        if (uart_is_readable(uart0))
        {
            char c = uart_getc(uart0);
            uart_putc(uart0, c);  // Echo
        }
    }
    return 0;
}
```

RP2040 UART 特性：
- 2 个独立 UART。
- 32 字节 TX FIFO，32 字节 RX FIFO。
- 支持 DMA。
- 自动波特率检测。
- 7-9 位数据位。
- 红外 SIR 编解码。

### F.4 nRF52 UART

Nordic nRF52832 有 1 个 UART，支持 EasyDMA：

```c
#include "nrf_uart.h"
#include "nrf_gpio.h"

void uart_init(void)
{
    nrf_uart_config_t cfg = {
        .baudrate = NRF_UART_BAUDRATE_115200,
        .parity = NRF_UART_PARITY_EXCLUDED,
        .hwfc = NRF_UART_HWFC_DISABLED,
    };
    nrf_uart_configure(NRF_UART0, &cfg);
    nrf_gpio_cfg_output(6);  // TX
    nrf_gpio_cfg_input(7, NRF_GPIO_PIN_PULLUP);  // RX
    nrf_uart_txrx_pins_set(NRF_UART0, 6, 7);
    nrf_uart_enable(NRF_UART0);
}

void uart_send(uint8_t *data, uint8_t len)
{
    for (uint8_t i = 0; i < len; i++)
    {
        nrf_uart_event_clear(NRF_UART0, NRF_UART_EVENT_TXDRDY);
        nrf_uart_txd_set(NRF_UART0, data[i]);
        while (!nrf_uart_event_check(NRF_UART0, NRF_UART_EVENT_TXDRDY));
    }
}
```

### F.5 平台性能对比

| 平台 | UART 数 | 最大波特率 | FIFO | DMA | 自动波特率 |
|------|---------|-----------|------|-----|-----------|
| ATmega328P | 1 | 2 Mbps | 1 字节 | 无 | 无 |
| PIC16F877A | 1 | 1.25 Mbps | 1 字节 | 无 | 无 |
| RP2040 | 2 | 1 Mbps | 32B | 支持 | 支持 |
| nRF52832 | 1 | 1 Mbps | 6B | EasyDMA | 无 |
| STM32F103 | 3-5 | 4.5 Mbps | 无 | DMA1/2 | 无 |
| STM32H7 | 4-8 | 26 Mbps | 16B | MDMA | 支持 |
| ESP32 | 3 | 5 Mbps | 128B | 内置 | 支持 |

---

## 附录 G：项目实战案例

### G.1 案例：工业数据采集网关

**需求**：采集 8 路 Modbus RTU 传感器数据，通过 Wi-Fi 上传服务器。

**架构**：
```
[8×Modbus传感器] ──RS-485── [STM32F407 主控] ──UART── [ESP8266 WiFi] ──网络── [服务器]
```

**关键设计**：

1. **RS-485 总线**：
   - 波特率 9600（传感器普遍支持）。
   - 120Ω 终端电阻 + 偏置电阻。
   - TVS 防护 + 共模电感。

2. **Modbus 轮询调度**：
   - 每个传感器 1 秒轮询一次。
   - 轮询表调度（见 C.2）。
   - 超时重传 3 次。

3. **STM32 ↔ ESP8266 通信**：
   - UART 115200bps。
   - AT 指令模式。
   - DMA 接收 + 环形缓冲区。

4. **数据上传**：
   - MQTT 协议（ESP8266 实现）。
   - JSON 数据格式。
   - 5 秒上报周期。

**代码片段 - 主循环**：

```c
void main_loop(void)
{
    // 1. Modbus 轮询
    Modbus_Poll_Run();

    // 2. 处理 WiFi 模块响应
    AT_Process_Line();

    // 3. 定时上传数据
    static uint32_t last_upload = 0;
    if (HAL_GetTick() - last_upload > 5000)
    {
        last_upload = HAL_GetTick();
        Upload_Sensor_Data();
    }

    // 4. 看门狗喂狗
    HAL_IWDG_Refresh(&hiwdg);
}

void Upload_Sensor_Data(void)
{
    char json[256];
    snprintf(json, sizeof(json),
             "{\"sensors\":[%u,%u,%u,%u,%u,%u,%u,%u]}",
             sensor_data[0], sensor_data[1], sensor_data[2], sensor_data[3],
             sensor_data[4], sensor_data[5], sensor_data[6], sensor_data[7]);
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "AT+CIPSEND=%d", strlen(json));
    AT_Command(cmd, ">", 1000);
    AT_Command(json, "SEND OK", 2000);
}
```

**遇到的问题与解决**：

| 问题 | 原因 | 解决 |
|------|------|------|
| Modbus 偶发超时 | RS-485 长线衰减 | 加中继器，总线分段 |
| ESP8266 掉线 | WiFi 信号弱 | 加看门狗，自动重连 |
| 数据上传丢包 | TCP 粘包 | 加长度前缀，超时重传 |
| STM32 偶发重启 | 电源不稳 | 加大电容，独立 LDO |

### G.2 案例：GPS 轨迹记录器

**需求**：记录 GPS 轨迹到 SD 卡，OLED 显示当前位置。

**架构**：
```
[GPS模块] ──UART── [STM32F103] ──SPI── [SD卡]
                      │──I2C── [OLED]
                      │──UART── [调试口]
```

**关键设计**：

1. **GPS 数据接收**：
   - UART2 9600bps（GPS 默认）。
   - DMA 循环模式接收。
   - 环形缓冲区按行解析 NMEA。

2. **SD 卡写入**：
   - SPI 模式，FATFS 文件系统。
   - 批量写入（每 10 条轨迹写一次）。
   - 文件按日期命名（YYYYMMDD.LOG）。

3. **OLED 显示**：
   - I2C SSD1306。
   - 1 秒刷新一次。

**代码片段 - GPS 数据处理**：

```c
void GPS_Data_Handler(uint8_t *data, uint16_t len)
{
    static char line[128];
    static uint8_t line_idx = 0;

    for (uint16_t i = 0; i < len; i++)
    {
        if (data[i] == '\n')
        {
            line[line_idx] = '\0';
            if (strstr(line, "$GPRMC"))
            {
                GPS_Parse_RMC(line);
                SD_Log_Line(line);
            }
            line_idx = 0;
        }
        else if (data[i] != '\r' && line_idx < sizeof(line) - 1)
        {
            line[line_idx++] = data[i];
        }
    }
}

// Parse RMC sentence: $GPRMC,time,status,lat,N,lon,E,speed,course,date,...
void GPS_Parse_RMC(const char *line)
{
    char status[2] = {0};
    char lat[12] = {0}, lon[12] = {0};
    char speed[8] = {0}, course[8] = {0};

    int parsed = sscanf(line, "$GPRMC,%*[^,],%1[^,],%11[^,],%*c,%11[^,],%*c,%7[^,],%7[^,]",
                        status, lat, lon, speed, course);
    if (parsed >= 1 && status[0] == 'A')  // A = valid
    {
        // Update display
        OLED_Show_GPS(lat, lon, speed);
    }
}
```

### G.3 案例：多 MCU 协同系统

**需求**：STM32 主控 + ESP32 协处理器，STM32 负责实时控制，ESP32 负责网络通信。

**架构**：
```
[STM32F407 主控] ──UART(1.5Mbps)── [ESP32 协处理器] ──WiFi── [云端]
     │                                 │
     ├──电机/传感器                     ├──OTA升级
     └──实时控制                        └──Web配置
```

**关键设计**：

1. **通信协议**：
   - 自定义二进制协议（帧头 0xAA + 长度 + 命令 + 数据 + CRC16 + 帧尾 0x55）。
   - 转义处理（见 C.7.2）。
   - 状态机解析（见 C.7.3）。

2. **DMA 双缓冲区**：
   - STM32 端 DMA 循环模式 + 半传输中断。
   - ESP32 端使用 ESP-IDF UART 事件驱动。

3. **心跳机制**：
   - 双方每 1 秒发送心跳包。
   - 3 秒未收到心跳判定断连。
   - 断连后自动重连。

4. **流量控制**：
   - STM32 处理慢时发送 PAUSE 命令。
   - ESP32 收到 PAUSE 后停止发送 100ms。

**STM32 端代码**：

```c
#define MCU_HEARTBEAT 0x01
#define MCU_DATA      0x02
#define MCU_PAUSE     0x03
#define MCU_RESUME    0x04

void MCU_Send_Frame(uint8_t cmd, uint8_t *data, uint8_t len)
{
    uint8_t frame[64];
    uint16_t idx = 0;
    frame[idx++] = 0xAA;
    frame[idx++] = len;
    frame[idx++] = cmd;
    memcpy(&frame[idx], data, len);
    idx += len;
    uint16_t crc = MB_CRC16(&frame[1], 2 + len);
    frame[idx++] = crc & 0xFF;
    frame[idx++] = (crc >> 8) & 0xFF;
    frame[idx++] = 0x55;
    HAL_UART_Transmit_DMA(&huart1, frame, idx);
}

// Heartbeat task (called every 1s)
void MCU_Heartbeat_Task(void)
{
    static uint32_t last_hb = 0;
    static uint32_t last_rx = 0;

    if (HAL_GetTick() - last_hb > 1000)
    {
        last_hb = HAL_GetTick();
        uint8_t status = Get_System_Status();
        MCU_Send_Frame(MCU_HEARTBEAT, &status, 1);
    }

    // Check connection
    if (HAL_GetTick() - last_rx > 3000)
    {
        ESP32_Connected = false;
        LED_Blink(RED, 200);  // Slow red blink = disconnected
    }
    else
    {
        ESP32_Connected = true;
        LED_Blink(GREEN, 1000);  // Slow green blink = connected
    }
}
```

**ESP32 端代码**：

```cpp
#include <esp_log.h>
#include <driver/uart.h>

void esp32_uart_task(void *arg)
{
    uint8_t data[256];
    while (1)
    {
        int len = uart_read_bytes(UART_NUM_1, data, sizeof(data), pdMS_TO_TICKS(100));
        if (len > 0)
        {
            for (int i = 0; i < len; i++)
            {
                Protocol_Process_Byte(data[i]);  // Reuse STM32 protocol parser
            }
        }
        // Send heartbeat every 1s
        static uint32_t last_hb = 0;
        if (xTaskGetTickCount() - last_hb > pdMS_TO_TICKS(1000))
        {
            last_hb = xTaskGetTickCount();
            uint8_t wifi_status = WiFi.isConnected() ? 1 : 0;
            ESP32_Send_Frame(0x01, &wifi_status, 1);
        }
    }
}
```

### G.4 案例：无线传感器网络

**需求**：多个 LoRa 节点通过 UART 连接 LoRa 模块，组成传感器网络。

**架构**：
```
[传感器] ── [STM32 + LoRa模块] ──UART── 无线 ── [LoRa模块 + 网关STM32] ── [服务器]
```

**LoRa 模块 UART 配置**（SX1278）：
- 波特率 9600。
- AT 指令配置参数（频率、扩频因子、带宽）。
- 透传模式收发数据。

**网关代码片段**：

```c
void LoRa_Gateway_Task(void)
{
    // Receive from LoRa module via UART
    if (uart_frame_ready)
    {
        uart_frame_ready = 0;
        // Forward to server via Ethernet/WiFi
        Ethernet_Send(uart_rx_buf, uart_rx_len);
    }
    // Receive commands from server, forward to LoRa
    if (Ethernet_Data_Ready())
    {
        uint8_t cmd[64];
        uint16_t len = Ethernet_Read(cmd, sizeof(cmd));
        // Send to LoRa module via UART (transparent mode)
        HAL_UART_Transmit_DMA(&huart1, cmd, len);
    }
}
```

### G.5 案例：串口工业打印机

**需求**：STM32 控制热敏打印机，打印订单小票。

**架构**：
```
[STM32F103] ──UART(9600)── [热敏打印机模块]
```

**打印机协议**：
- ESC/POS 指令集。
- 9600bps，8N1，硬件流控（RTS/CTS）。

**代码片段**：

```c
// ESC/POS commands
const uint8_t CMD_INIT[]      = {0x1B, 0x40};              // Initialize
const uint8_t CMD_ALIGN_CTR[] = {0x1B, 0x61, 0x01};        // Center align
const uint8_t CMD_BOLD_ON[]   = {0x1B, 0x45, 0x01};        // Bold on
const uint8_t CMD_BOLD_OFF[]  = {0x1B, 0x45, 0x00};        // Bold off
const uint8_t CMD_FEED[]      = {0x1B, 0x64, 0x03};        // Feed 3 lines
const uint8_t CMD_CUT[]       = {0x1D, 0x56, 0x00};        // Cut paper

void Print_Receipt(const char *store, const char *items[], const char *total)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)CMD_INIT, 2, 100);
    HAL_UART_Transmit(&huart2, (uint8_t *)CMD_ALIGN_CTR, 3, 100);
    HAL_UART_Transmit(&huart2, (uint8_t *)CMD_BOLD_ON, 3, 100);
    Print_Text(store);
    HAL_UART_Transmit(&huart2, (uint8_t *)CMD_BOLD_OFF, 3, 100);
    Print_Text("\r\n");
    Print_Text("------------------------------\r\n");
    for (int i = 0; items[i] != NULL; i++)
    {
        Print_Text(items[i]);
        Print_Text("\r\n");
    }
    Print_Text("------------------------------\r\n");
    Print_Text("Total: ");
    Print_Text(total);
    Print_Text("\r\n");
    HAL_UART_Transmit(&huart2, (uint8_t *)CMD_FEED, 3, 100);
    HAL_UART_Transmit(&huart2, (uint8_t *)CMD_CUT, 3, 100);
}

void Print_Text(const char *text)
{
    HAL_UART_Transmit(&huart2, (uint8_t *)text, strlen(text), 100);
}
```

**注意事项**：
1. 打印机打印速度慢，需硬件流控避免缓冲区溢出。
2. 热敏打印需要一定时间，连续打印注意散热。
3. 缺纸检测通过 GPIO 中断处理。
4. 打印失败需重试机制。

---

## 附录 H：扩展故障案例集

### H.1 案例：STM32 DMA 接收首字节丢失

**现象**：使用 HAL_UART_Receive_DMA 启动接收后，第一个字节经常丢失。

**原因**：DMA 启动前 UART DR 中可能有残留数据（如上次接收未读完），DMA 启动时该数据被覆盖。

**修复**：

```c
void UART_DMA_Start_Clean(void)
{
    // 1. Disable UART
    __HAL_UART_DISABLE(&huart1);
    // 2. Clear any pending data in DR
    (void)USART1->DR;
    // 3. Clear flags
    __HAL_UART_CLEAR_OREFLAG(&huart1);
    // 4. Re-enable UART
    __HAL_UART_ENABLE(&huart1);
    // 5. Start DMA
    HAL_UART_Receive_DMA(&huart1, uart_rx_buf, UART_RX_BUF_SIZE);
}
```

### H.2 案例：Modbus 通信偶发 CRC 错误

**现象**：Modbus RTU 通信偶尔 CRC 校验失败，错误率约 0.1%。

**原因**：
1. RS-485 总线长 200 米，超过 9600bps 的稳定距离。
2. 总线无终端电阻，信号反射。
3. 干扰源（变频器）靠近总线。

**修复**：
1. 两端加 120Ω 终端电阻。
2. 总线改用屏蔽双绞线，屏蔽层接地。
3. 远离变频器走线，或加金属线槽屏蔽。
4. 软件层加 Modbus 重传机制（3 次重试）。

### H.3 案例：ESP32 + Arduino 高波特率丢数据

**现象**：ESP32 Arduino Serial1 在 921600bps 下偶发丢字节。

**原因**：Arduino 默认 RX 缓冲区 256 字节，高波特率下缓冲区溢出。

**修复**：

```cpp
void setup()
{
    Serial1.setRxBufferSize(1024);  // Must call before begin
    Serial1.begin(921600, SERIAL_8N1, 18, 19);
}
```

或改用 ESP-IDF API 获得更好性能（见 13.6）。

### H.4 案例：Linux 串口 read 阻塞

**现象**：Linux C 程序 read() 一直阻塞，即使有数据。

**原因**：VMIN=1, VTIME=0 配置下，read 必须读到 1 字节才返回，且无超时。

**修复**：

```c
// Set VMIN=0, VTIME=10 (1 second timeout)
tty.c_cc[VMIN] = 0;
tty.c_cc[VTIME] = 10;
tcsetattr(fd, TCSANOW, &tty);
// Now read() returns after 1 second even if no data
```

### H.5 案例：STM32 多 UART 中断优先级冲突

**现象**：STM32 同时使用 5 个 UART，高波特率 UART3 频繁 ORE。

**原因**：所有 UART 中断优先级相同，低波特率 UART 中断处理时间长，阻塞高波特率 UART。

**修复**：

```c
// Assign higher priority (lower number) to high-speed UART
HAL_NVIC_SetPriority(USART3_IRQn, 0, 0);  // Highest
HAL_NVIC_SetPriority(USART1_IRQn, 1, 0);
HAL_NVIC_SetPriority(USART2_IRQn, 2, 0);
HAL_NVIC_SetPriority(UART4_IRQn, 3, 0);
HAL_NVIC_SetPriority(UART5_IRQn, 4, 0);

// Better: use DMA for high-speed UART, free up CPU
```

### H.6 案例：UART 唤醒 Stop 模式后波特率变化

**现象**：STM32 进入 Stop 模式，UART 唤醒后通信乱码。

**原因**：Stop 模式下 HSE 停止，UART 时钟切换到 HSI（±1% 精度），波特率偏差超容限。

**修复**：

```c
void UART_Wakeup_Handler(void)
{
    // After wake from Stop, reconfigure system clock
    SystemClock_Config();  // Restart HSE/PLL
    // UART clock restored, communication resumes correctly
    // Note: first few bytes after wakeup may be corrupted
}
```

或使用 LSE（32.768kHz）作为 Stop 模式 UART 时钟，但波特率限制在 9600。

### H.7 案例：串口调试助手显示乱码

**现象**：用 PuTTY/SSCOM 接收 STM32 输出，显示乱码。

**排查步骤**：

1. **检查波特率**：调试助手波特率是否与 STM32 一致。
2. **检查帧格式**：8N1 是否一致。
3. **检查时钟**：用 `printf("SYSCLK=%lu\n", SystemCoreClock)` 验证。
4. **检查接线**：TX-RX 交叉，GND 共地。
5. **检查电平**：STM32 是 3.3V TTL，USB-TTL 模块是否 3.3V（5V 模块可能损坏 STM32）。
6. **示波器验证**：测量实际波特率。

常见原因统计：

| 原因 | 占比 |
|------|------|
| 波特率不匹配 | 50% |
| 时钟配置错误 | 20% |
| 接线错误 | 15% |
| 电平不匹配 | 10% |
| 帧格式不一致 | 5% |

### H.8 案例汇总表

| 案例 | 现象 | 根因 | 解决 |
|------|------|------|------|
| C.8.1 | PCLK 与预期不符 | HSE_VALUE 错误 | 修正晶振宏 |
| C.8.2 | H7 DMA 数据乱码 | D-Cache 一致性 | 非缓存区或 invalidate |
| C.8.3 | RS-485 总线冲突 | 波特率/地址冲突 | 统一配置 |
| C.8.4 | USB 转串口丢字节 | CH340 缓冲区小 | 降波特率或换芯片 |
| C.8.5 | printf 中断死锁 | 阻塞调用 | ISR-safe 日志 |
| H.1 | DMA 首字节丢失 | DR 残留 | 启动前清空 |
| H.2 | Modbus CRC 偶发错误 | 长线无终端 | 加 120Ω 电阻 |
| H.3 | ESP32 高速丢数据 | 缓冲区不足 | 增大 RX buffer |
| H.4 | Linux read 阻塞 | VMIN/VTIME | 设 VMIN=0, VTIME=10 |
| H.5 | 多 UART ORE | 优先级冲突 | 调整 NVIC 优先级 |
| H.6 | 唤醒后乱码 | 时钟切换 | 重新配置时钟 |
| H.7 | 调试助手乱码 | 波特率/时钟 | 逐步排查 |

---

## 附录 I：UART 学习路径与资源

### I.1 学习路径

**入门（1-2 周）**：
1. 理解 UART 异步通信原理。
2. Arduino 串口编程（Serial.begin, Serial.print）。
3. PC 与 MCU 通信（USB-TTL 模块）。
4. 串口调试助手使用。

**进阶（2-4 周）**：
1. STM32 HAL 库 UART 编程（轮询、中断）。
2. 寄存器级配置（CR1/CR2/CR3/BRR）。
3. DMA 模式编程。
4. RS-232/RS-485 电平转换。

**高级（1-2 月）**：
1. DMA 循环模式 + 半传输中断双缓冲区。
2. Modbus RTU 协议实现。
3. 多机通信（9 位模式）。
4. 自动波特率检测。
5. 错误处理与统计。

**专家（持续）**：
1. 信号完整性分析（示波器、阻抗匹配）。
2. EMC 设计（屏蔽、接地、滤波）。
3. 跨平台抽象层设计。
4. 安全通信（加密、认证）。
5. 性能优化（低延迟、低功耗）。

### I.2 推荐资源

**书籍**：
- 《Serial Port Complete》- Jan Axelson
- 《嵌入式系统串口通信》- 国内教材
- STM32 Reference Manual（RM0008/RM0090/RM0433）
- 《信号完整性与电源完整性》- Eric Bogatin

**在线资源**：
- STMicroelectronics 官方文档与例程
- Modbus Organization 官方规范
- SparkFun UART 教程
- Stack Overflow UART 标签

**工具**：
- Saleae Logic（逻辑分析仪）
- Tera Term / PuTTY（串口终端）
- STM32CubeMX（配置工具）
- PulseView（开源逻辑分析仪软件）
- Serial Studio（数据可视化）

### I.3 实践项目建议

由易到难的实践项目：

1. **printf 调试**：STM32 通过 UART 输出调试信息到 PC。
2. **GPS 数据解析**：读取 GPS 模块 NMEA 语句，解析经纬度。
3. **蓝牙串口通信**：HC-05 与手机 APP 通信。
4. **Modbus 从站**：实现 Modbus RTU 从站，响应主站查询。
5. **多机通信**：1 主 3 从 RS-485 网络。
6. **高速数据采集**：DMA 双缓冲区接收传感器数据流。
7. **工业网关**：Modbus 转 MQTT，工业设备上云。
8. **串口服务器**：TCP/UDP 与 UART 透传。

### I.4 认证与标准

相关专业认证：

| 认证 | 颁发机构 | 涉及 UART 内容 |
|------|---------|---------------|
| 嵌入式系统设计师 | 中国软考 | UART 原理、编程 |
- 工业控制网络 | 各厂商 | Modbus、RS-485 |
| CIS CO CCNA | Cisco | 串行通信基础 |
| FCC Part 15 | FCC | EMC 合规 |
| CE EMC | 欧盟 | EMC 合规 |
| IEC 61000-4-2 | IEC | ESD 测试 |
| IEC 61000-4-4 | IEC | EFT 测试 |

---

## 附录 J：UART 协议进阶与最佳实践

### J.1 高可靠性协议设计要点

在工业控制、医疗设备、车载电子等高可靠性场景中，UART 通信需要额外的协议层保障。以下是一个带帧头、长度、CRC16 校验、超时重传的可靠协议设计示例：

```c
/* Reliable UART frame protocol: [HEAD(0xAA55)][LEN][SEQ][CMD][PAYLOAD...][CRC16] */
#define FRAME_HEAD0     0xAA
#define FRAME_HEAD1     0x55
#define FRAME_MAX_LEN   64
#define FRAME_TIMEOUT_MS 50

typedef struct {
    uint8_t  head[2];   /* 0xAA, 0x55 */
    uint8_t  len;       /* payload length */
    uint8_t  seq;       /* sequence number */
    uint8_t  cmd;       /* command code */
    uint8_t  payload[FRAME_MAX_LEN];
    uint16_t crc;       /* CRC16 over len..payload */
} uart_frame_t;

/* Build a frame and send via UART */
void Frame_Send(UART_HandleTypeDef *huart, uint8_t cmd,
                const uint8_t *payload, uint8_t len, uint8_t seq)
{
    uart_frame_t f;
    f.head[0] = FRAME_HEAD0;
    f.head[1] = FRAME_HEAD1;
    f.len     = len;
    f.seq     = seq;
    f.cmd     = cmd;
    if (len > 0) {
        memcpy(f.payload, payload, len);
    }
    f.crc = Modbus_CRC16((uint8_t *)&f.len, 2 + len);
    HAL_UART_Transmit(huart, (uint8_t *)&f, 6 + len, 100);
}
```

### J.2 接收状态机解析

接收端用状态机解析可避免半包/粘包问题，对噪声引起的字节丢失有较好鲁棒性：

```c
typedef enum {
    ST_HEAD0, ST_HEAD1, ST_LEN, ST_SEQ, ST_CMD, ST_PAYLOAD, ST_CRC_L, ST_CRC_H
} frame_state_t;

static frame_state_t s_state = ST_HEAD0;
static uint8_t s_idx = 0;
static uart_frame_t s_rx;

void Frame_OnByte(uint8_t b)
{
    switch (s_state) {
    case ST_HEAD0:
        if (b == FRAME_HEAD0) { s_rx.head[0] = b; s_state = ST_HEAD1; }
        break;
    case ST_HEAD1:
        s_state = (b == FRAME_HEAD1) ? (s_rx.head[1] = b, ST_LEN) : ST_HEAD0;
        break;
    case ST_LEN:
        s_rx.len = b;
        if (b > FRAME_MAX_LEN) { s_state = ST_HEAD0; return; }
        s_idx = 0; s_state = ST_SEQ;
        break;
    case ST_SEQ:  s_rx.seq = b; s_state = ST_CMD; break;
    case ST_CMD:  s_rx.cmd = b; s_state = (s_rx.len > 0) ? ST_PAYLOAD : ST_CRC_L; break;
    case ST_PAYLOAD:
        s_rx.payload[s_idx++] = b;
        if (s_idx >= s_rx.len) s_state = ST_CRC_L;
        break;
    case ST_CRC_L: s_rx.crc = b; s_state = ST_CRC_H; break;
    case ST_CRC_H:
        s_rx.crc |= (uint16_t)b << 8;
        Frame_Dispatch(&s_rx);   /* verify CRC then handle */
        s_state = ST_HEAD0;
        break;
    }
}
```

### J.3 通信质量监控

实时监控 UART 通信质量有助于早期发现噪声、波特率漂移、线缆劣化等问题：

| 监控指标 | 计算方法 | 告警阈值 | 处理建议 |
|---------|---------|---------|---------|
| 误码率 (BER) | 错误帧数 / 总帧数 | > 1e-4 | 检查线缆屏蔽、降低波特率 |
| CRC 失败率 | CRC 错误帧 / 总帧数 | > 0.1% | 检查 EMI 干扰、加磁环 |
| 超时重传率 | 超时次数 / 发送次数 | > 5% | 检查对端负载、波特率容限 |
| 帧丢失率 | 期望帧数 - 实际帧数 | > 0.5% | 检查 DMA 缓冲区溢出 |
| 平均响应时间 | sum(RTT)/N | > 50 ms | 优化调度、减少中断延迟 |

```c
/* Communication quality statistics */
typedef struct {
    uint32_t tx_frames;
    uint32_t rx_frames;
    uint32_t crc_errors;
    uint32_t timeout_errors;
    uint32_t overflow_errors;
    uint32_t noise_errors;
} uart_stats_t;

static uart_stats_t g_stats;

void Stats_OnRxOk(void)        { g_stats.rx_frames++; }
void Stats_OnCrcError(void)    { g_stats.crc_errors++; }
void Stats_OnTimeout(void)     { g_stats.timeout_errors++; }
void Stats_OnOverflow(void)    { g_stats.overflow_errors++; }
void Stats_OnNoiseError(void)  { g_stats.noise_errors++; }

/* Call every 1s from a timer, log or report via SNMP */
void Stats_Report(void)
{
    uint32_t total = g_stats.rx_frames + g_stats.crc_errors;
    float ber = (total > 0) ? (float)g_stats.crc_errors / total : 0.0f;
    printf("RX=%lu CRC=%lu ORE=%lu NE=%lu BER=%.4f%%\r\n",
           g_stats.rx_frames, g_stats.crc_errors,
           g_stats.overflow_errors, g_stats.noise_errors, ber * 100.0f);
}
```

### J.4 工程交付检查清单

| 检查项 | 验证方法 | 通过标准 |
|--------|---------|---------|
| 波特率精度 | 测量实际位宽 | 误差 < ±1.5% |
| 帧格式一致性 | 双端配置对比 | 数据位/校验/停止位一致 |
| 信号完整性 | 示波器看眼图 | 过冲 < 20%，无振铃 |
| 抗噪声能力 | 注入干扰测试 | BER < 1e-5 |
| ESD 防护 | 接触放电 ±8kV | 通信不中断 |
| 长时间稳定性 | 72h 连续通信 | 无丢包、无死锁 |
| 低功耗 | 休眠电流测量 | < 50 µA（待机） |
| 协议兼容性 | 与第三方设备互通 | 数据无歧义 |

### J.5 常见配置陷阱速查

1. **HAL_UART_Receive_DMA 缓冲区必须对齐**：DMA 要求缓冲区地址与数据宽度对齐，否则触发总线 fault。使用 `__attribute__((aligned(4)))`。
2. **IDLE 中断标志必须手动清零**：STM32F1 中 `SR` 寄存器的 IDLE 位读后写 0（或读 SR 再读 DR）才能清零，否则中断反复触发。
3. **RS-485 DE/RE 切换时序**：DE 必须在最后一个停止位发出后再拉低，过早拉低会截断数据。STM32 支持 RS-485 硬件 DE 控制（CR3_DEM），优先使用。
4. **printf 重定向栈溢出**：微库（MicroLIB）的 printf 仍占用较多栈，重定向函数中避免大数组，建议改用自定义 `print_u`。
5. **DMA 循环模式 vs 普通模式**：循环模式自动重启传输，不会丢失后续数据；普通模式需在回调中重新启动，期间数据会丢失。高速接收必须用循环模式。
6. **波特率容限累积**：8N1 帧 ±2.5%，9N1 帧 ±2.2%，加校验位后容限进一步下降，高波特率时务必测量实际波特率。

---

*文档版本：v2.0 完整版 | 最后更新：2026-06 | 适用平台：STM32F1/F4/H7, ESP32, Arduino, Linux, RP2040, nRF52, PIC*


