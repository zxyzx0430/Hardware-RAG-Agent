/** 加载状态：fetchMessages 期间无本地缓存时显示，区别于"真空会话"的 EmptyState。
 *  用 SVG animateTransform 实现旋转，无需外部 CSS keyframes；颜色用 CSS 变量适配主题。 */
export function LoadingState() {
  return (
    <div className="empty-state" style={{ flexDirection: "column" }}>
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" aria-label="加载中">
        <circle cx="12" cy="12" r="10" stroke="var(--border)" strokeWidth="3" />
        <path d="M12 2a10 10 0 0 1 10 10" stroke="var(--primary)" strokeWidth="3" strokeLinecap="round">
          <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite" />
        </path>
      </svg>
      <p style={{ marginTop: 12, color: "var(--muted-fg)", fontSize: 13 }}>加载中...</p>
    </div>
  );
}
