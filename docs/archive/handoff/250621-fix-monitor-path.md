# 交办单 250621-fix-monitor-path

## 归属
- 线程：07-hardware
- 优先级：P0
- 关联问题：#7 monitor 路径缺 /api/ 前缀

## 任务
frontend/src/api/endpoints.ts 中 monitor 路径改为 /api/monitor/{port}

## 验证
1. api-contract.md 中 monitor 接口路径已为 /api/monitor/{port}
2. 前端 mock 层如有 monitor 相关也对齐

## 开工前必读
AGENTS.md / docs/completed.md / docs/api-contract.md / docs/issue-tracker.md


## TODO 清单

- [ ] endpoints.ts monitor 路径改为 /api/monitor/{port}
- [ ] mock.ts monitor 相关路径对齐
- [ ] api-contract.md 确认 monitor 为 /api/monitor/{port}
