# UX 收尾打磨设计稿

> 日期：2026-07-07
> 状态：待批准
> 范围：A 档 16 项快速 win + B 档视觉 3 项 + B 档后端低风险 4 项，共 23 项

## Why

项目接近收尾，4 个 subagent 从聊天交互/视觉布局/工作台知识库设置/后端性能 4 个角度审查出 38 个 UX 提升点。本设计稿聚焦其中性价比最高的 23 项（16 项 S 工作量 + 7 项 M 工作量），在收尾阶段快速提升用户体验。

## What Changes

### 批次 1：A 档前端快速 win（10 项）

| # | 改动 | 文件 | 工作量 |
|---|------|------|--------|
| 1 | InputBar 拖拽附件加 isDragOver state + 高亮边框 + "释放以上传"提示条 | InputBar.tsx | S |
| 2 | ErrorBlock 加复制图标按钮，复制 code+message+detail | ErrorBlock.tsx | S |
| 3 | EmptyState 点击建议改为 setDraft（填入输入框），非直接发送 | EmptyState.tsx | S |
| 4 | 内联 img 加 onLoad/onError，加载中灰色占位+spinner，失败显示"图片加载失败"+重试 | AssistantMessageContent.tsx, UserMessageContent.tsx | S |
| 5 | textarea 最大高度 160px → 280px | InputBar.tsx | S |
| 6 | emoji → SVG：密码显隐(🙈👁️→eye/eye-off)、管理(⚙→gear)、提示(💡→lightbulb) 等 | SettingsPage.tsx, KnowledgePanel.tsx, InputBar.tsx, FlashPane.tsx | S |
| 7 | 圆角令牌化：base.css 定义 --radius-sm:4px / --radius:6px / --radius-md:8px / --radius-lg:12px | base.css + 全局替换 | S |
| 8 | 字号下限提升：9-10px → 11-12px，定义字号梯度令牌 | misc.css, workbench.css, settings.css, chat.css, knowledge.css | S |
| 9 | 暗色硬编码颜色改用 CSS 变量（--success/--warn/--danger 等） | settings.css, workbench.css, misc.css, layout.css | S |
| 10 | toggle 统一：合并 3 套为 1 套（36×20 / knob 16px / 行程 16px） | misc.css, settings.css | S |

### 批次 2：A 档工作台快速 win（4 项）

| # | 改动 | 文件 | 工作量 |
|---|------|------|--------|
| 11 | SerialPane/WiringPane/FlashPane 所有图标按钮加 aria-label | SerialPane.tsx, WiringEditor.tsx, FlashPane.tsx | S |
| 12 | 接线图空状态文案改为"点击「从代码提取」或「生成接线图」"，修复死胡同 | WiringPane.tsx | S |
| 13 | 演示数据按钮移到"未连接"空状态内 + 加确认弹窗 | SerialPane.tsx | S |
| 14 | 烧录进度文案区分"编译中… 45%"/"烧录中… 45%"，完成后进度条保留 3 秒 | FlashPane.tsx | S |

### 批次 3：A 档后端快速 win（4 项）

| # | 改动 | 文件 | 工作量 |
|---|------|------|--------|
| 15 | auth store 进程内缓存 + mtime 失效，避免每次请求读文件 | auth.py | S |
| 16 | 串口扫描 comports() 和 PDF 解析 parse_from_bytes 用 asyncio.to_thread 包裹 | hardware_routes.py, attachments.py | S |
| 17 | fallback SSE 路径每 15s yield heartbeat 事件 | chat_routes.py | S |
| 18 | async def 路由中直接用同步 DB 的改回 def，让 FastAPI 自动线程池化（~15 个函数） | chat_routes.py, kb_routes.py, search_routes.py | S |

### 批次 4：B 档视觉重点（3 项）

| # | 改动 | 文件 | 工作量 |
|---|------|------|--------|
| 19 | 按钮统一：定义 primary/secondary/ghost/danger 4 变体，固定 padding 6px 14px / radius 6px / font-size 12px，所有按钮迁移 | base.css + 全局替换 | M |
| 20 | 焦点可见样式：base.css 加全局 :focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; } | base.css | M |
| 21 | 空状态统一：定义 <EmptyState icon title desc /> 组件，替换所有纯文字空状态（保留 spinner 不用骨架屏） | 新建 EmptyState.tsx + 替换 | M |

## Impact

- **Affected code**：前端 15+ 组件、4 个 CSS 文件、后端 4 个路由文件
- **Affected specs**：无（纯 UX 打磨，不改变功能契约）
- **Risk**：批次 3 后端改动（#18 async→def）涉及 ~15 个函数，需回归测试确保功能正常
- **Regression**：改完后做全链路回归测试（发消息/工具调用/知识库/串口/烧录）

## Design Decisions

1. **按钮 4 变体**（primary/secondary/ghost/danger）：覆盖现有 10+ 种按钮，固定规范
2. **加载状态**：保留现有 spinner，不做骨架屏（用户偏好简单）
3. **空状态**：统一为 EmptyState 组件，但加载态保留 spinner
4. **async DB 修复**：用 def 替换（删 async 关键字），FastAPI 自动线程池化，最省力
5. **回归测试**：所有改动完成后做全链路回归

## Execution Plan

4 个批次可顺序执行（批次间无依赖），每批次完成后可独立验证：

```
批次 1（前端 S×10）→ 批次 2（工作台 S×4）→ 批次 3（后端 S×4）→ 批次 4（视觉 M×3）→ 回归测试
```

批次 1 和 2 可并行（文件不重叠）。批次 3 独立。批次 4 依赖批次 1 的令牌定义（圆角/字号）。

## Out of Scope

- B 档聊天：流式"返回最新"按钮、发送后滚动、高风险二次确认（用户未选）
- B 档工作台：首配引导、KB 管理器重构、索引进度改进（用户未选）
- B 档后端高风险：rerank 优化、agent 缓存（用户明确不做）
- C 档：书签逻辑、虚拟列表、内联样式重构、间距网格、KB 分页等
