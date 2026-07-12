# 硬件术语综合测试文档

本文档用于验证 BM25 分词、RRF 融合分数、查询改写三套核心机制的准确性。文档刻意覆盖多种硬件术语（芯片型号、外设、协议、封装、模块），便于测试 jieba 词典和检索精度。

## 1. STM32 系列芯片

### 1.1 STM32F4 开发指南

STM32F4 系列基于 ARM Cortex-M4 内核，主频 168 MHz，内置 FPU 和 DSP 指令。典型型号 STM32F407VG 采用 LQFP100 封装，拥有 192 KB SRAM 和 1 MB Flash。

配置 GPIO 时需要先开启时钟：`RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN`。STM32F4 的 GPIO 支持推挽（Push-pull）和开漏（Open-drain）两种输出模式，上拉（Pull-up）和下拉（Pull-down）电阻可软件配置。

DMA 传输可以显著降低 CPU 负载。STM32F4 有 16 路 DMA 流，分属两个 DMA 控制器。配置 DMA 的关键步骤：

1. 使能 DMA 时钟
2. 配置外设地址和数据地址
3. 设置传输方向、数据宽度、循环模式
4. 使能 DMA 中断
5. 启动传输

### 1.2 STM32H7 性能对比

STM32H7 系列采用 Cortex-M7 内核，主频高达 480 MHz，远超 STM32F4。STM32H743VI 拥有 1 MB SRAM 和 2 MB Flash，支持 QSPI 外部 Flash 扩展。

## 2. ESP32 无线开发

### 2.1 ESP32-S3 引脚与 Strapping

ESP32-S3 是乐鑫最新一代 Wi-Fi + BLE SoC，双核 Xtensa LX7 @ 240 MHz。Strapping 引脚决定启动模式：

- GPIO0：Boot 模式（高=正常启动，低=下载模式）
- GPIO46：VDD_SPI 电压选择
- GPIO45：芯片内核电压

启动时这些引脚必须有确定的电平，否则 ESP32-S3 可能进入意外模式。典型做法是用 10kΩ 上拉电阻把 GPIO0 拉高。

### 2.2 ESP32-C3 RISC-V 架构

ESP32-C3 采用 RISC-V 单核架构，主频 160 MHz，功耗更低。适合物联网低功耗场景。ESP32-C3 同样有 Strapping 引脚（GPIO2/GPIO8），配置方式与 ESP32-S3 类似。

### 2.3 ESP8266 旧款对比

ESP8266 是乐鑫早期产品，单核 @ 80 MHz，仅支持 Wi-Fi 不支持 BLE。虽然性能弱于 ESP32 系列，但成本低廉，仍广泛用于简单 IoT 节点。

## 3. 通信总线协议

### 3.1 SPI 全双工高速总线

SPI（Serial Peripheral Interface）是全双工同步串行总线，使用四根线：SCLK、MOSI、MISO、CS。相比 I2C 的两线制，SPI 速度更快，可达 50 MHz 以上。

STM32 的 SPI 支持主从模式、DMA 传输、I2S 复用。配置 SPI 时需要注意时钟极性（CPOL）和时钟相位（CPHA），这四个组合形成四种 SPI 模式。

### 3.2 UART 串口通信

UART 是最基础的异步串行通信，只需 TX/RX 两根线。STM32 的 USART 支持最高 4.5 Mbps，带硬件流控（RTS/CTS）。常见波特率：9600、115200、921600。

调试 UART 时用逻辑分析仪抓波形最有效。注意奇偶校验（Parity）和停止位（Stop bit）的配置必须收发两端一致。

### 3.3 CAN 总线

CAN 总线用于汽车和工业控制，具有强抗干扰能力。STM32F4 内置 bxCAN 控制器，支持 CAN 2.0A/B 协议。CAN 总线需要 120Ω 终端电阻。

## 4. 常用传感器与模块

### 4.1 MPU6050 六轴姿态传感器

MPU6050 集成三轴加速度计和三轴陀螺仪，通过 I2C 接口通信，地址 0x68（AD0 接地）或 0x69（AD0 接 VCC）。读取数据时需要配置采样率、量程、低通滤波器。

### 4.2 BME280 环境传感器

BME280 集成温度、湿度、气压三种测量，同样走 I2C 接口。相比 DHT11/DHT22 只能测温湿度，BME280 多了气压数据，可用于海拔估算。

### 4.3 OLED 显示屏 SSD1306

SSD1306 是 0.96 寸 OLED 屏常用驱动 IC，支持 I2C 和 SPI 两种接口。I2C 模式下地址 0x3C 或 0x3D。显示原理是通过页寻址写入显存。

### 4.4 WS2812 全彩 LED

WS2812（NeoPixel）是内置驱动的 RGB LED，单线数据协议。时序要求严格：0 码 0.35µs 高电平，1 码 0.9µs 高电平。STM32 用 PWM + DMA 驱动 WS2812 是经典方案。

### 4.5 HC-SR04 超声波测距

HC-SR04 通过 Trig 引脚发 10µs 高电平触发，Echo 引脚返回高电平脉宽对应距离。测量范围 2cm-400cm，精度 3mm。

## 5. 开发工具链

### 5.1 HAL 库与 LL 库

STM32 有两套官方库：HAL（Hardware Abstraction Layer）和 LL（Low-Layer）。HAL 跨系列兼容但效率低，LL 接近寄存器效率高。新手建议先用 HAL，熟悉后用 LL。

### 5.2 FreeRTOS 实时操作系统

FreeRTOS 是嵌入式最流行的 RTOS。STM32 + FreeRTOS 组合下要注意中断优先级：FreeRTOS 中断必须配置为优先级 5-15，否则可能崩溃。

### 5.3 SWD 调试接口

SWD（Serial Wire Debug）只需 SWDIO/SWCLK 两根线即可调试，比 JTAG 的五线制更省引脚。STM32 默认支持 SWD，烧录工具用 ST-Link 或 J-Link。

## 6. 电源与时钟设计

### 6.1 晶振选型

STM32 需要外部晶振（Crystal）提供精确时钟。HSE 典型 8 MHz，LSE 为 32.768 kHz。晶振负载电容计算公式：`CL = (C1*C2)/(C1+C2) + Cs`，其中 Cs 为 PCB 杂散电容约 3-5pF。

### 6.2 EEPROM 存储器

EEPROM 用于保存掉电不丢失的参数。STM32F4 没有内置 EEPROM，需要用 Flash 模拟或外挂 24C02（I2C 接口）。

## 7. 看门狗与 Bootloader

### 7.1 看门狗定时器

看门狗（Watchdog）用于系统死机后自动复位。STM32 有独立看门狗 IWDG 和窗口看门狗 WWDG。喂狗（Kick the dog）必须在超时前完成。

### 7.2 Bootloader 设计

STM32 的 Bootloader 从 System Flash 启动，支持 UART 升级。自定义 Bootloader 需要配置中断向量表重定向：`SCB->VTOR = 0x08000000`。
