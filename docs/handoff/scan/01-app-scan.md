## 任务：全面代码扫描（两轮，不修）

目标线程：01-app（布局/导航/主题/UI 组件）

范围文件：
- frontend/src/App.tsx
- frontend/src/main.tsx
- frontend/src/components/layout/*.tsx
- frontend/src/components/topbar/*.tsx
- frontend/src/components/shared/*.tsx
- frontend/src/stores/useAppStore.ts
- frontend/src/hooks/useTheme.ts
- frontend/src/hooks/usePanelResize.ts
- frontend/src/hooks/useKeyboard.ts
- frontend/src/styles/globals.css

方法：做两轮扫描，每轮把结果追加到 `docs/review/01-app-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 20-30 分钟通读全部范围文件，找以下问题：

1. 类型安全
   - TS strict 模式报错
   - 变量/参数可能为 null 但未做守卫
   - 类型断言过于宽松（as any 滥用）

2. 功能完整性
   - 是否有按钮/入口声明了但没连接 onClick/handler
   - state 到 UI 的绑定是否完整
   - 异步操作是否有 await / .catch

3. 代码异味
   - 不再使用的 import、变量、组件
   - 重复的代码块可以抽取但没抽
   - 硬编码的字符串/数字应该做成常量

4. 资源泄漏
   - EventListener 绑了没解绑
   - 定时器/interval 清理了吗
   - AbortController 是否在组件卸载时 abort

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 3-5 个最可疑的路径做深度检查：

1. 契约对齐
   - 调用的 API 路径和 api-contract.md 一致吗？
   - 请求/响应字段名、类型和文档一致吗？

2. 错误处理
   - try/catch 是否覆盖了所有异步操作
   - 用户看到的错误信息是否可理解
   - 网络断开 / API 超时 / 空数据 这些场景有处理吗

3. 竞态条件
   - 多个 setState 是否可能互相覆盖
   - 快速点击 submit 是否会导致请求重复

4. 边界情况
   - 空列表 / 空消息 / 空会话 时 UI 是否正常
   - 超长文本 / 特殊字符 / 纯空格输入

### 输出格式

每个问题按这个格式写：

```
## [P0/P1/P2] 简短标题

- 位置：文件路径:行号
- 现象：
- 影响评估（出故障时用户看到什么）：
- 建议修复方式（一句话）：
```

优先级含义：
- P0 = 上线就炸（空引用、未捕获异常、接口不匹配）
- P1 = 体验劣化（UI 异常、加载失败、操作无反馈）
- P2 = 代码质量（可优化、可抽取、可清理）

### 完成后

两轮都做完后，通知 00-control：「01-app 扫描完成，结果在 docs/review/01-app-scan.md」

注意：不要修，只记录。
