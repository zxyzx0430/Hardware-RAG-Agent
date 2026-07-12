// 多 tab 同步：用 BroadcastChannel API 在不同 tab 间广播事件
// 项目目标浏览器是现代 Chrome/Edge，BroadcastChannel 支持充分
// 自己 tab 发的事件不会收到（BroadcastChannel 只跨 tab），不会重复刷新

export type BroadcastEvent =
  | 'sessions_changed'        // 会话列表变化（新建/删除/重命名/移动项目）
  | 'session_deleted'         // 会话被删除（payload: sessionId）
  | 'messages_changed';       // 某会话消息变化（payload: sessionId）

const CHANNEL_NAME = 'hardware-rag-agent';

let channel: BroadcastChannel | null = null;

/** 获取单例 channel；老旧浏览器不支持时返回 null，post/on 静默 no-op */
function getChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null;
  if (!channel) channel = new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

/** 广播事件到其他 tab */
export function post(event: BroadcastEvent, payload?: string): void {
  const ch = getChannel();
  if (!ch) return;
  ch.postMessage({ event, payload });
}

/** 监听其他 tab 的事件；返回取消订阅函数 */
export function on(event: BroadcastEvent, handler: (payload?: string) => void): () => void {
  const ch = getChannel();
  if (!ch) return () => {};
  const listener = (e: MessageEvent) => {
    if (e.data?.event === event) handler(e.data.payload);
  };
  ch.addEventListener('message', listener);
  return () => ch.removeEventListener('message', listener);
}
