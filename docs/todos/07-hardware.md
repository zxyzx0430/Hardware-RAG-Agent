# 07-hardware TODO

> 参考 AGENTS.md TODO 系统规则维护。

## 当前任务

- [x] 修复 monitor WebSocket 路径缺 /api/ 前缀（issue #7，P0）
      ✅ endpoints.ts monitor 常量改为 /api/monitor/，apiWS 去掉硬编码 /api 前缀拼接，
         WorkbenchPanel.tsx WS 调用同步改为 /api/monitor/，api-contract.md 文档对齐
- [x] stub 工具返回带入参信息（issue #4，P1）
      ✅ AuditPinsTool/WiringTool/BuildTool/UploadTool 的 output 改为 f-string 含入参字段；
         SearchDocsTool 已正确，无改动；self.args 死代码不存在

## 待办

- [ ] 后端硬件 API 实现（serial.py / flash.py / wiring.py / safety.py / diagnose.py）
- [ ] 前后端联调 monitor WebSocket
- [ ] 确认 pyserial / PlatformIO 是否安装
