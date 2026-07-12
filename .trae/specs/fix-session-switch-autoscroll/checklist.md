# Checklist

## 必须修复（7 项）

- [x] 刷新页面后，聊天容器自动滚到最后一条消息
- [x] 首次切换到会话 B，B 自动滚到底部
- [x] 切回曾访问的会话 A，恢复之前滚动位置（不强制滚到底）
- [x] 切换到消息数相同的会话时，新会话自动滚到底部
- [x] 切换到空会话时显示空状态，不报错
- [x] 用户在会话 A 滚到中间，切换到会话 B 时，B 自动滚到底部（userScrolledRef 已重置）
- [x] 流式输出中用户向上滚动不被打断
- [x] 会话含图片时，图片加载后仍定位到底部
- [x] HITL 确认框出现后按 ESC，Agent 收到 deny 决策继续执行（不永久阻塞）
- [x] 推理模型 reasoning 步骤标签显示 'thinking' 而非 'thing'
- [x] sendMessage 同步异常时显示 ErrorBlock（不空白）
- [x] 首次进入无本地缓存的会话，显示加载指示而非 EmptyState
- [x] 编辑消息 textarea 支持 Shift+Enter 换行
- [x] ErrorBlock 在暗色模式下颜色协调（用 CSS 变量）

## 建议修改（10 项）

- [x] fetchMessages 失败时显示 toast
- [x] persistLastTurn 失败时显示 toast
- [x] 流式中尝试发送消息时显示 toast "正在生成中，请先停止当前回答"
- [x] 流式中按 ESC 停止流式
- [x] 引用消息时按 ESC 取消引用
- [x] 有附件时按 ESC 移除附件
- [x] 编辑消息时按 ESC 取消编辑
- [x] 图片灯箱打开时按 ESC 关闭
- [x] ConfirmDialog 中按 Tab 焦点在对话框内循环
- [x] ConfirmDialog 显示时自动聚焦"拒绝"按钮
- [x] ImageLightbox 有 role="dialog" aria-modal="true"
- [x] 图片加载时灯箱显示 loading 指示
- [x] 会话 A 输入草稿 → 切到 B → 切回 A → 草稿保留
- [x] 删除当前活跃会话后立即切到第一个剩余会话（不留空白）
- [x] 删除会话弹应用内 Modal（不是浏览器原生 confirm）
- [x] 新建项目弹应用内 Modal 含输入框（不是浏览器原生 prompt）
- [x] 恢复快照弹应用内 Modal（不是浏览器原生 confirm）
- [x] 删除服务商弹应用内 Modal（不是浏览器原生 confirm）
- [x] Modal ESC 等价"取消"（Promise resolve false/null）
- [x] Modal Tab 焦点循环（focus trap）
- [x] Modal 暗色模式颜色协调（用 CSS 变量）
- [x] 多 tab：tab A 发消息 → tab B 同步显示新消息
- [x] 多 tab：tab A 删会话 → tab B 侧边栏移除；若在看该会话则切换
- [x] 多 tab：tab A 新建会话 → tab B 侧边栏出现

## 代码质量

- [x] `npx tsc --noEmit` 通过，0 errors
- [x] 滚动相关代码注释中英混排符合规范（业务逻辑用中文，技术术语保留英文）
- [x] 没有破坏已有的流式输出滚动优化（memo 化、rAF 节流等）
- [x] 没有破坏图片渲染、source 引用、代码块高亮、收藏夹、编辑重发等现有功能（代码审查确认未改动相关逻辑）
- [x] 新增 Toast 组件有合理的类型注解（ToastItem interface + ToastType 联合类型）
- [x] 新增 Modal 组件有合理的类型注解，Promise 接口类型清晰
- [x] 新增 broadcast.ts 有合理的类型注解，事件名有枚举/联合类型约束
- [x] 没有引入不必要的第三方依赖
- [x] 没有修改问题 4（并发 SSE，用户刻意为之）和问题 24（超长折叠，用户明确不修）
