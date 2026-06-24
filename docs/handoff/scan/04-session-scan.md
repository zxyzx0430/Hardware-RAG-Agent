## 任务：全面代码扫描（两轮，不修）

目标线程：04-session（持久化 / 设置 / 会话管理）

范围文件：
- frontend/src/stores/useSessionStore.ts
- frontend/src/stores/useSettingsStore.ts
- frontend/src/components/session/SessionPanel.tsx
- frontend/src/components/settings/SettingsPage.tsx
- frontend/src/components/shared/SnapshotPanel.tsx
- frontend/src/components/bookmarks/BookmarkPanel.tsx
- frontend/src/utils/persistence.ts
- frontend/src/types/session.ts
- backend/app/db/models.py (Session / Message / Settings)
- backend/app/api/auth.py
- backend/app/api/dependencies.py
- backend/app/db/database.py

方法：做两轮扫描，每轮把结果追加到 `docs/review/04-session-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 20-30 分钟通读全部范围文件，找以下问题：

1. 类型安全
   - TS 类型定义和实际 API 返回是否一致
   - Python 模型字段 missing / nullable 标注

2. 功能完整性
   - API Key 加密/解密流程是否覆盖所有读写路径
   - 设置修改后是否立即持久化
   - 分片存储的 flush 时机是否正确

3. 代码异味
   - 重复的 CRUD 逻辑
   - 硬编码的路由路径
   - 未使用的 store action

4. 资源泄漏
   - 数据库会话是否正确关闭
   - window.beforeunload 是否做最后 flush

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 3-5 个最可疑的路径做深度检查：

1. 契约对齐
   - sessions/messages API 和 api-contract.md 一致吗
   - 设置读写接口字段和文档一致吗

2. 错误处理
   - 数据库写入失败时用户看到什么
   - API Key 解密失败降级逻辑
   - 分片存储写满/磁盘满了怎么办

3. 竞态条件
   - 快速切换会话时消息是否正确加载
   - 多个 tab 同时修改设置谁赢

4. 边界情况
   - 删除会话时关联的消息是否一起删除
   - 空会话列表显示什么
   - API Key 加密密钥丢失后的恢复

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

两轮都做完后，通知 00-control：「04-session 扫描完成，结果在 docs/review/04-session-scan.md」

注意：不要修，只记录。
