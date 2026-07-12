# 04-session 代码扫描报告

> 扫描时间：2026-06-24
> 扫描方法：两轮（Pass 1 广度 + Pass 2 深度），仅记录问题，不改代码
> 范围：持久化 / 设置 / 会话管理 相关 12 个文件

---

## Pass 1：广度扫描

---

## [P1] beforeunload 未注册，页面关闭可能丢失数据

- 位置：rontend/src/stores/useChatStore.ts:796
- 现象：lushPendingShards() 函数存在，通过 500ms debounce 定时刷写，但 window.addEventListener("beforeunload", flushPendingShards) 未注册
- 影响评估：用户关闭浏览器标签页时，最近 500ms 内的消息变更可能丢失
- 建议修复方式：在 useEffect 或 store 初始化时注册 beforeunload 事件

---

## [P1] bookmarks 和 snapshots 纯前端存储，无后端同步

- 位置：书签 rontend/src/stores/useChatStore.ts 中 bookmarkData/bookmarkFolders；快照 rontend/src/components/shared/SnapshotPanel.tsx
- 现象：Bookmark/BookmarkFolder 的 ORM 模型已建（models.py），但没有任何后端 CRUD 路由。快照完全无后端模型和路由
- 影响评估：清除浏览器数据或更换设备时所有书签和快照永久丢失
- 建议修复方式：为 Bookmark/BookmarkFolder 添加后端 CRUD 路由

---

## [P1] Provider API Key 在前端 localStorage 明文存储

- 位置：rontend/src/stores/useSettingsStore.ts providerKeys 字段
- 现象：persistence.ts 将 providerKeys: { openai: "sk-...", deepseek: "sk-..." } 明文写入 localStorage["hwrag_settings"]
- 影响评估：任何能访问浏览器开发者工具的人或 XSS 脚本可窃取所有 API Key
- 建议修复方式：API Key 输入后仅通过 /api/auth/store-key 加密存储，前端只存 session_token

---

## [P1] Settings 后端同步不完整，仅 API Key 变更触发

- 位置：rontend/src/stores/useSettingsStore.ts setProviderKey
- 现象：model / temperature / systemPrompt / themeMode 等变更仅写入 localStorage，未调 PUT /api/settings
- 影响评估：后端不感知大部分设置变更，未来多设备或服务端配置管理无法同步
- 建议修复方式：subscribe 回调中检测关键设置变更并调 apiPut("settings") 同步

---

## [P2] settings 类型文件与 store 不同步

- 位置：rontend/src/types/settings.ts vs rontend/src/stores/useSettingsStore.ts
- 现象：store 中有 showKeys / verifyStatus / toolKeys / showToolKeys / baseUrls 等字段，types/settings.ts 只定义了 ProviderInfo / Skill / MCPServer 三个接口
- 影响评估：类型检查无法捕获 store 字段变更错误
- 建议修复方式：在 types/settings.ts 中补充完整的 SettingsState 接口

---

## [P2] auth.py 内联鉴权与 dependencies.py 重复

- 位置：ackend/app/api/auth.py list_keys 和 delete_key 路由
- 现象：两个路由内部自己写鉴权逻辑，而非复用 dependencies.py 的 current_user 依赖
- 影响评估：两次鉴权逻辑之间存在分歧风险
- 建议修复方式：改为通过 Depends(current_user) 鉴权，移除内联鉴权

---

## [P2] api/client.ts 兼容旧格式注释已过时

- 位置：rontend/src/api/client.ts:82
- 现象：crud.py 已全部使用 _ok() 包装标准格式，兼容旧格式的注释不再准确
- 影响评估：轻微误导
- 建议修复方式：更新注释为「保留 safety net」

---

## [P3] database.py 使用 __import__ 模式

- 位置：ackend/app/db/database.py init_db()
- 现象：多处使用 __import__("sqlalchemy").text(...) 而非顶部 import
- 影响评估：IDE 无法静态分析
- 建议修复方式：统一 from sqlalchemy import text

---

## [P3] useSessionStore.ts 冗余 saveToStorage 调用

- 位置：rontend/src/stores/useSessionStore.ts 约 10 处
- 现象：每个 action 都手动调 saveToStorage("sessions")，容易遗漏
- 建议修复方式：通过 Zustand subscribe 自动持久化，类似 useSettingsStore

---

## Pass 2：深度扫描

---

## [P0] 多 Tab 修改设置存在竞态（last-write-wins）

- 位置：rontend/src/stores/useSettingsStore.ts subscribe 写入 localStorage
- 现象：Tab A 改 model → localStorage 写入；Tab B 之后改 temperature → 全量覆写（JSON.stringify 整个 store），Tab A 的变更丢失。未监听 window.storage 事件做跨 Tab 同步
- 影响评估：用户开两个页面修改不同设置时后保存的覆盖先保存的
- 建议修复方式：监听 window.addEventListener("storage") 跨 Tab 同步；或按 key 粒度存储

---

## [P1] 加密密钥丢失后无恢复机制

- 位置：ackend/app/api/auth.py _get_fernet()
- 现象：.enc_key 文件存储 Fernet 密钥。若文件被删除/损坏，所有已加密的 API Key 永久无法解密
- 影响评估：所有 Provider API Key 需重新输入
- 建议修复方式：支持 ENCRYPTION_KEY 环境变量覆盖；创建时打日志提醒备份

---

## [P1] 空会话列表无空状态提示

- 位置：rontend/src/components/session/SessionPanel.tsx
- 现象：filtered 为空时用户看到空白区域，无加载中/无结果/空状态提示
- 影响评估：用户会后困惑是加载中还是出了问题
- 建议修复方式：添加条件渲染空状态提示

---

## [P2] createSession 分支路径缺源会话存在校验

- 位置：ackend/app/api/crud.py create_session
- 现象：branch_from_session_id 指向无效 session 时，db.query().first() 返回 None，随后访问 .messages 抛 AttributeError → 500
- 影响评估：恶意请求或过期分支数据导致 500
- 建议修复方式：添加 if not source_session: raise _fail("NOT_FOUND", ...)

---

## [P2] fetchWithTimeout 默认超时 8s 对批量操作偏短

- 位置：rontend/src/api/client.ts:66
- 现象：首次 GET /api/sessions 在有大量会话消息时可能超过 8 秒
- 影响评估：会话列表加载可能因超时而失败
- 建议修复方式：对批量接口指定 15-20 秒超时，或统一提高到 15s

---

## 总结

| 等级 | 数量 | 关键问题 |
|------|------|---------|
| P0 | 1 | 多 Tab 设置竞态（last-write-wins） |
| P1 | 5 | beforeunload / 书签快照纯前端 / API Key 明文存 / 设置不同步后端 / 加密密钥无恢复 / 空状态 |
| P2 | 4 | 类型不同步 / auth 内联鉴权 / 注释过时 / 分支校验缺 / 超时偏短 |
| P3 | 2 | __import__ 模式 / 冗余 saveToStorage |
