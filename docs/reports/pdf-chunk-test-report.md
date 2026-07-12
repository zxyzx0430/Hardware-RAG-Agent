# PDF Multimodal 入库测试报告

测试 KB: `kb-44cf14d6`
分块策略: multimodal (Vision LLM)
模型: oc/mimo-v2.5 @ https://9router.zxyzx.bbroot.com/v1
测试时间: 2026-06-28 05:16:09
测试文件数: 8

## 概览

| 文件 | 状态 | chunks | 总字符 | avg | <100 | 代码块 | 表格 | 截断代码 | 拆分表格 | 策略 | 页数 | 批次 | sections | 耗时s | tokens |
|------|------|--------|--------|-----|------|--------|------|---------|---------|------|------|------|----------|-------|--------|
| spi_specification.pdf | indexed | 28 | 24751 | 884.0 | 0 | 0 | 0 | 0 | 0 | toc | 10 | 14 | 31 | 313.6 | 206618 |
| dht11_datasheet.pdf | indexed | 11 | 9599 | 872.6 | 0 | 0 | 0 | 0 | 0 | toc | 4 | 5 | 7 | 161.1 | 54606 |
| i2c_specification.pdf | indexed | 218 | 191029 | 876.3 | 0 | 0 | 0 | 0 | 0 | toc | 62 | 49 | 137 | 687.9 | 919429 |
| mpu6050_datasheet.pdf | indexed | 115 | 93384 | 812.0 | 0 | 0 | 0 | 0 | 0 | toc | 52 | 39 | 83 | 543.5 | 694170 |
| esp32-c3_datasheet.pdf | indexed | 162 | 128107 | 790.8 | 0 | 0 | 0 | 0 | 0 | toc | 76 | 31 | 100 | 396.2 | 780608 |
| stm32f103c8_datasheet.pdf | indexed | 140 | 115790 | 827.1 | 0 | 0 | 0 | 0 | 0 | toc | 67 | 34 | 71 | 379.0 | 706870 |
| a4988_datasheet.pdf | indexed | 41 | 34478 | 840.9 | 0 | 0 | 0 | 0 | 0 | toc | 20 | 15 | 32 | 161.3 | 233591 |
| lm2596_datasheet.pdf | indexed | 138 | 112916 | 818.2 | 1 | 0 | 0 | 0 | 0 | toc | 47 | 24 | 82 | 283.9 | 561936 |

## 汇总统计

- 总 chunks: **853**
- 短 chunks (<100 chars): **1** (0.1%)
- 极短 chunks (<50 chars): **0** (0.0%)
- 空 chunks: **0**
- 代码块截断: **0**
- 表格跨 chunk 拆分: **0**
- 纯符号 chunks: **0**
- 总耗时: **2926.5s** (48.8min)
- 总 tokens: **4157828**

## 分批策略分布

- `toc`: 8 个文件

## 每个文件详细分析

### spi_specification.pdf

- chunks: 28
- 字符总数: 24751 | min: 433 | max: 998 | avg: 884.0
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 10 | 批次: 14 | sections: 31
  - 耗时: 313.6s
  - tokens: prompt=199230, completion=7388, total=206618

**首 chunk 预览**:
```
© Freescale Semiconductor, Inc., 2006. All rights reserved.
Freescale Semiconductor
Application Note
AN3208
Rev. 0, 1/2006
Table of Contents
1
Overview
This document is a quick-reference troubleshooting 
guide for solving crystal oscillator problems that might 
be encountered when working with microcontrollers. 
A practical explanation of the Pierce and Colpitts 
oscillators used in Freescale micr
```

**末 chunk 预览**:
```
MC68HC908GP32”, Freescale Semiconductor Application Note AN2105, 2001.
7. Yan-Tai Ng, “Designing with the MC68HC908JL/JK Microcontroller Family”, Freescale  
Semiconductor Application Note AN2158, 2001.
8. Ross Carlton, Greg Racino, John Suchyta, “Improving the Transient Immunity Performance of 
Microcontroller-Based Applications”, Freescale Semiconductor Application Note, Freescale 
Semiconductor
```

### dht11_datasheet.pdf

- chunks: 11
- 字符总数: 9599 | min: 439 | max: 993 | avg: 872.6
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 4 | 批次: 5 | sections: 7
  - 耗时: 161.1s
  - tokens: prompt=53306, completion=1300, total=54606

**首 chunk 预览**:
```
EL-USB-ULT-LCD
Ultra Low Temperature Cryogenic
Vaccine Data Logger with LCD screen
Issue 1    01/2021    Page 1 of 4
www.lascarelectronics.com/data-loggers
Your data, anytime, anywhere
The EL-USB-ULT-LCD is designed to monitor vaccines in cryogenic dry ice storage.
This standalone data logger measures and stores more than 32,000 temperature readings from its high accuracy thermocouple 
temperature
```

**末 chunk 预览**:
```
Passivation
If left unused for extended periods of time lithium metal batteries, including those used in the EasyLog range of data loggers, 
naturally form a non-conductive internal layer preventing them from self-discharge and effectively increasing their shelf life. When 
first installed in the data logger, this may cause a momentary drop in the battery voltage (the Transient Minimum Voltage) as
```

### i2c_specification.pdf

- chunks: 218
- 字符总数: 191029 | min: 229 | max: 1000 | avg: 876.3
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 62 | 批次: 49 | sections: 137
  - 耗时: 687.9s
  - tokens: prompt=887458, completion=31971, total=919429

**首 chunk 预览**:
```
UM10204
I2C-bus specification and user manual
Rev. 7.0 — 1 October 2021
User manual
 
 
Document information
Information
Content
Keywords
I2C, I2C-bus, Standard-mode, Fast-mode, Fast-mode Plus, Fm+, UltraFast-
mode, UFm, High Speed, Hs, inter-IC, SDA, SCL, USDA, USCL
Abstract
Philips Semiconductors (now NXP Semiconductors) developed a simple
bidirectional 2-wire bus for efficient inter-IC control,
```

**末 chunk 预览**:
```
© NXP B.V. 2021.
All rights reserved.
For more information, please visit: http://www.nxp.com
For sales office addresses, please send an email to: salesaddresses@nxp.com
Date of release: 1 October 2021
Document identifier: UM10204
```

### mpu6050_datasheet.pdf

- chunks: 115
- 字符总数: 93384 | min: 181 | max: 999 | avg: 812.0
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 52 | 批次: 39 | sections: 83
  - 耗时: 543.5s
  - tokens: prompt=674822, completion=19348, total=694170

**首 chunk 预览**:
```
InvenSense Inc. 
1197 Borregas Ave, Sunnyvale, CA 94089 U.S.A. 
Tel: +1 (408) 988-7339  Fax: +1 (408) 988-8104 
Website: www.invensense.com 
Document Number: PS-MPU-6000A-00 
Revision: 3.4 
Release Date: 08/19/2013 
 
 
           1 of 52 
MPU-6000 and MPU-6050 
Product Specification 
Revision 3.4
```

**末 chunk 预览**:
```
transportation, aerospace and nuclear instruments, undersea equipment, power plant equipment, disaster prevention and crime 
prevention equipment.  
 
InvenSense® is a registered trademark of InvenSense, Inc. MPUTM, MPU-6000TM, MPU-6050TM, MPU-60X0TM, Digital Motion 
Processor™, DMP ™, Motion Processing Unit™, MotionFusion™, MotionInterface™, MotionTracking™, and MotionApps™ are 
trademarks of Inv
```

### esp32-c3_datasheet.pdf

- chunks: 162
- 字符总数: 128107 | min: 141 | max: 1000 | avg: 790.8
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 76 | 批次: 31 | sections: 100
  - 耗时: 396.2s
  - tokens: prompt=758762, completion=21846, total=780608

**首 chunk 预览**:
```
ESP32-C3 Series
Datasheet Version 2.4
Ultra-Low-Power SoC with RISC-V Single-Core CPU
2.4 GHz Wi-Fi (802.11b/g/n) and Bluetooth® 5 (LE)
Optional flash in the chip’s package up to 8 MB
QFN32 (5×5 mm) package
Including:
ESP32-C3
ESP32-C3FN4 – End of life (EOL)
ESP32-C3FH4
ESP32-C3FH4AZ – Not Recommended for New Designs (NRND)
ESP32-C3FH4X – Recommended
ESP32-C3FH8X
www.espressif.com
```

**末 chunk 预览**:
```
All trade names, trademarks and registered trademarks mentioned in this document are property of their respective owners, and are
hereby acknowledged.
Copyright © 2026 Espressif Systems (Shanghai) Co., Ltd. All rights reserved.
www.espressif.com
```

### stm32f103c8_datasheet.pdf

- chunks: 140
- 字符总数: 115790 | min: 211 | max: 999 | avg: 827.1
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 67 | 批次: 34 | sections: 71
  - 耗时: 379.0s
  - tokens: prompt=690574, completion=16296, total=706870

**首 chunk 预览**:
```
Preliminary Data
This is preliminary information on a new product now in development or undergoing evaluation. Details are subject to 
change without notice.
July 2007
  Rev 2
1/67
1
STM32F103x6
STM32F103x8 STM32F103xB
Performance line,  ARM-based 32-bit MCU with Flash, USB, CAN,
seven 16-bit timers, two ADCs and nine communication interfaces
Features
■
Core: ARM 32-bit Cortex™-M3 CPU
–
72 MHz, 90
```

**末 chunk 预览**:
```
in Table 28: Electrical sensitivities. RPU and RPD min and max values 
added to Table 29: I/O static characteristics. RPU min and max values 
added to Table 32: NRST pin characteristics.
Figure 18: I2C bus AC waveforms and measurement circuit and 
Figure 17: Recommended NRST pin protection corrected.
Notes removed below Table 7, Table 32, Table 37.
IDD typical values changed in Table 11: Maximum c
```

### a4988_datasheet.pdf

- chunks: 41
- 字符总数: 34478 | min: 399 | max: 999 | avg: 840.9
- 短 chunks (<100): 0 (indices: [])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 20 | 批次: 15 | sections: 32
  - 耗时: 161.3s
  - tokens: prompt=226808, completion=6783, total=233591

**首 chunk 预览**:
```
FEATURES AND BENEFITS
▪ Low Rds(on) outputs
▪ Automatic current decay mode detection/selection
▪ Mixed and slow current decay modes
▪ Synchronous rectification for low power dissipation
▪ Internal UVLO
▪ Crossover-current protection
▪ 3.3 and 5 V compatible logic supply
▪ Thermal shutdown circuitry
▪ Short-to-ground protection
▪ Shorted load protection
▪ Five selectable step modes: full, 1/2, 1/4,
```

**末 chunk 预览**:
```
improvements in the performance, reliability, or manufacturability of its products.  Before placing an order, the user is cautioned to verify that the 
information being relied upon is current.  
Allegro’s products are not to be used in any devices or systems, including but not limited to life support devices or systems, in which a failure of 
Allegro’s product can reasonably be expected to cause 
```

### lm2596_datasheet.pdf

- chunks: 138
- 字符总数: 112916 | min: 71 | max: 1000 | avg: 818.2
- 短 chunks (<100): 1 (indices: [133])
- 极短 chunks (<50): 0
- 代码块 chunks: 0 | 截断: 0
- 表格 chunks: 0 | 拆分: 0
- 纯符号 chunks: 0
- **Multimodal Trace**:
  - 策略: `toc`
  - 页数: 47 | 批次: 24 | sections: 82
  - 耗时: 283.9s
  - tokens: prompt=542884, completion=19052, total=561936

**首 chunk 预览**:
```
LM2596 SIMPLE SWITCHER® Power Converter 150-kHz
3-A Step-Down Voltage Regulator
1 Features
•
New product available:
–
LMR51430 4.5 to 36-V, 3-A, 500-kHz and 1.1-
MHz synchronous converter
•
For faster time to market:
–
TLVM13630 3 to 36-V, 3-A, 200-kHz to 2.2-MHz 
power module
•
3.3-V, 5-V, 12-V, and adjustable output versions
•
Adjustable version output voltage range: 1.2-V to 
37-V ±4% maximum o
```

**末 chunk 预览**:
```
warranties or warranty disclaimers for TI products. Unless TI explicitly designates a product as custom or customer-specified, TI products 
are standard, catalog, general purpose devices.
TI objects to and rejects any additional or different terms you may propose.
IMPORTANT NOTICE
Copyright © 2026, Texas Instruments Incorporated
Last updated 10/2025
```
