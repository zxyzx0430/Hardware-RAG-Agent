# Hardware RAG Agent — 知识库建设任务看板

> 最后更新：2026-06-15
> 线程：知识库建设线程
> 状态：Week 3 进行中

---

## 总体进度

| 阶段 | 状态 | 完成日期 |
|------|------|----------|
| Week 3：首批 10 篇文档 | 🔄 进行中 | — |
| Week 4：建立 10 题评测基线 | ⏳ 待开始 | — |
| Week 5-6：扩充 20+ 篇 | ⏳ 待开始 | — |
| Week 8：多知识库拆分 | ⏳ 待开始 | — |
| Week 10：最终评测报告 | ⏳ 待开始 | — |

---

## Week 3 任务清单

### 📦 管线搭建
- [x] 项目基建（Week 1-2 已完成）
- [x] `src/rag/document_loader.py` — PDF 下载模块
- [x] `src/rag/document_processor.py` — Docling 解析 + LLM 翻译
- [x] `src/rag/vector_store.py` — ChromaDB 向量化入库
- [x] `src/rag/pipeline.py` — 统一编排入口
- [ ] `src/rag/__init__.py` — 模块导出

### 📄 首批 10 篇文档
- [ ] 1. ESP32-WROOM-32 Datasheet
- [ ] 2. ESP32-C3 Datasheet
- [ ] 3. STM32F103C8T6 Datasheet
- [ ] 4. Arduino Uno R3 (ATmega328P) Datasheet
- [ ] 5. Arduino Nano (ATmega328P) Datasheet
- [ ] 6. DHT22 温湿度传感器
- [ ] 7. HC-SR04 超声波传感器
- [ ] 8. MPU6050 加速度计/陀螺仪
- [ ] 9. BMP280 气压传感器
- [ ] 10. I2C LCD1602 模块

---

## 文档标准格式

每篇文档入库前需转为统一格式：

```yaml
---
title: "芯片/模块名称"
category: dev-boards | sensors | protocols | peripherals | troubleshooting | my-notes
source_url: "官方 PDF 或网页链接"
last_updated: "YYYY-MM-DD"
tags: [tag1, tag2]
---

## 概述

## 引脚定义

| 引脚 | 名称 | 功能 | 备注 |

## 电气特性

## 通信接口

## 踩坑记录
```

## 翻译规范

- 技术术语保留英文原文（GPIO、I2C、SPI、UART、PWM、ADC 等）
- 中文翻译自然通顺
- 表格行列对齐
- 保留代码块结构

---
