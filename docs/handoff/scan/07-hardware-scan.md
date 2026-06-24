## 任务：全面代码扫描（两轮，不修）

目标线程：07-hardware（硬件工作台）

范围文件：
- frontend/src/components/workbench/WorkbenchPanel.tsx
- frontend/src/api/endpoints.ts (monitor 相关)
- backend/app/api/hardware_routes.py
- backend/app/api/build_routes.py
- backend/app/api/common.py (GPIO 常量)

方法：做两轮扫描，每轮把结果追加到 `docs/review/07-hardware-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 20-30 分钟通读全部范围文件，找以下问题：

1. 类型安全
   - 串口参数的 TS 类型和后端是否对齐
   - GPIO 引脚编号的合法性校验

2. 功能完整性
   - 串口打开/关闭/心跳是否正确
   - 烧录编译和烧录模拟的进度反馈
   - 接线图 SVG 交互是否完整

3. 代码异味
   - 硬编码的引脚列表 / 芯片型号
   - 重复的诊断逻辑
   - stub 工具是否标记清楚

4. 资源泄漏
   - 串口连接在组件卸载时关闭
   - WebSocket 重连逻辑

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 3-5 个最可疑的路径做深度检查：

1. 契约对齐
   - /api/devices /api/diagnose /api/wiring 和 api-contract.md 一致吗
   - monitor WebSocket 路径和文档一致吗

2. 错误处理
   - 串口打开失败前端显示什么
   - 编译失败时是否展示错误日志
   - 接线图生成失败

3. 竞态条件
   - 串口数据接收和 UI 更新是否线程安全
   - 多个串口同时操作

4. 边界情况
   - 无串口设备 / 无可用端口
   - 不支持的芯片型号
   - 接线引脚超出 GPIO 范围

### 输出格式

每个问题按这个格式写：

```
## [P0/P1/P2] 简短标题

- 位置：文件路径:行号
- 现象：
- 影响评估（出故障时用户看到什么）：
- 建议修复方式（一句话）：
```

### 完成后

两轮都做完后，通知 00-control：「07-hardware 扫描完成，结果在 docs/review/07-hardware-scan.md」

注意：不要修，只记录。
