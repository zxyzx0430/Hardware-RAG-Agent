import { useChatStore } from "../../stores/useChatStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useI18n } from "../../i18n";
import type { ContentPart } from "../../types/session";

function formatDuration(ms: number) {
  if (ms < 60000) return Math.floor(ms / 1000) + 's';
  return Math.floor(ms / 60000) + 'm ' + Math.floor((ms % 60000) / 1000) + 's';
}

/** 简易 SVG 折线图 */
function Sparkline({ data, color, height = 40 }: { data: number[]; color: string; height?: number }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1);
  const w = 240;
  const step = w / (data.length - 1);
  const points = data.map((v, i) => `${i * step},${height - (v / max) * (height - 4) - 2}`).join(" ");
  const areaPoints = `0,${height} ${points} ${w},${height}`;
  return (
    <svg width={w} height={height} style={{ display: "block" }}>
      <defs>
        <linearGradient id={`grad-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0.05" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={`url(#grad-${color.replace("#", "")})`} />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      {data.map((v, i) => (
        <circle key={i} cx={i * step} cy={height - (v / max) * (height - 4) - 2} r="2" fill={color} />
      ))}
    </svg>
  );
}

/** 粗略估算 token 数（中文 x1.5 + 英文 x0.5） */
function estimateTokens(text: string): number {
  const cnChars = [...text].filter(c => '\u4e00' <= c && c <= '\u9fff').length;
  const otherChars = text.length - cnChars;
  return Math.ceil(cnChars * 1.5 + otherChars * 0.5);
}

/** Extract plain text from Message content (string | ContentPart[]) */
function contentToText(content: string | ContentPart[]): string {
  if (typeof content === 'string') return content;
  return content.map(p => 'text' in p ? p.text : '').join('');
}

export function StatsPanel() {
  const { t } = useI18n();
  const { messages, statsOpen, hideStats } = useChatStore();
  const { model } = useSettingsStore();

  if (!statsOpen) return null;

  const userMsgs = messages.filter((m) => m.role === 'user').length;
  const assistantMsgs = messages.filter((m) => m.role === 'assistant').length;
  let srcCount = 0;
  messages.forEach((m) => { if (m.sources) srcCount += m.sources.length; });

  // 优先使用 API 返回的真实 usage，无数据时回退估算
  let realPromptTokens = 0;
  let realCompletionTokens = 0;
  let realTotalTokens = 0;
  let hasRealUsage = false;

  messages.forEach((m) => {
    if (m.role === 'assistant' && m.usage) {
      realPromptTokens += m.usage.promptTokens;
      realCompletionTokens += m.usage.completionTokens;
      realTotalTokens += m.usage.totalTokens;
      hasRealUsage = true;
    }
  });

  // 估算 fallback
  const estimatedInput = messages
    .filter((m) => m.role === 'user')
    .reduce((a, m) => a + estimateTokens(contentToText(m.content)), 0);
  const estimatedOutput = messages
    .filter((m) => m.role === 'assistant')
    .reduce((a, m) => a + estimateTokens(contentToText(m.content)), 0);

  const inputTokens = hasRealUsage ? realPromptTokens : estimatedInput;
  const outputTokens = hasRealUsage ? realCompletionTokens : estimatedOutput;
  const totalTokens = hasRealUsage ? realTotalTokens : (estimatedInput + estimatedOutput);
  const totalDur = messages.reduce((a, m) => a + (m.activity ? m.activity.durationMs : 0), 0);

  // 每条消息的 token 数（用于折线图）
  const inputTokenSeries: number[] = [];
  const outputTokenSeries: number[] = [];
  messages.forEach((m) => {
    if (m.role === 'user') {
      inputTokenSeries.push(estimateTokens(contentToText(m.content)));
    } else if (m.role === 'assistant') {
      outputTokenSeries.push(m.usage?.completionTokens || estimateTokens(contentToText(m.content)));
    }
  });

  const stats = [
    [t('statsMsgCount'), messages.length + ''],
    [t('statsUserMsgs'), userMsgs + ''],
    [t('statsAiMsgs'), assistantMsgs + ''],
    [t('statsSources'), srcCount + ''],
    [t('statsTokens'), totalTokens.toLocaleString() + ' tokens' + (hasRealUsage ? ' ✓' : ' ~')],
    ['Input Tokens', inputTokens.toLocaleString() + (hasRealUsage ? '' : ' ~')],
    ['Output Tokens', outputTokens.toLocaleString() + (hasRealUsage ? '' : ' ~')],
    [t('statsDuration'), formatDuration(totalDur)],
    [t('statsModel'), model],
  ];

  return (
    <>
      <div className="dropdown-backdrop" onClick={hideStats} />
      <div className="stats-panel" style={{ position: 'fixed', top: 60, right: 20, zIndex: 9999, width: 300, background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.15)', maxHeight: '80vh', overflowY: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 13 }}>{t('chatStats')}</div>
        <div style={{ padding: 8 }}>
          {stats.map(([label, value]) => (
            <div key={label} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontSize: 12, color: 'var(--muted-fg)' }}>{label}</span>
              <span style={{ fontSize: 13, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{value}</span>
            </div>
          ))}
          {!hasRealUsage && (
            <div style={{ padding: '6px 8px', fontSize: 10, color: 'var(--muted-fg)', fontStyle: 'italic' }}>
              ~ 为估算值，API 返回 usage 后显示真实值
            </div>
          )}
        </div>

        {/* Token 折线图 */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Token Usage</div>
          <div style={{ display: 'flex', gap: 16, marginBottom: 8 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 10, color: 'var(--muted-fg)', marginBottom: 4 }}>Input: {inputTokens.toLocaleString()}</div>
              <Sparkline data={inputTokenSeries} color="#3b82f6" />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 10, color: 'var(--muted-fg)', marginBottom: 4 }}>Output: {outputTokens.toLocaleString()}</div>
              <Sparkline data={outputTokenSeries} color="#10b981" />
            </div>
          </div>
        </div>

        <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
          <button onClick={hideStats} style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--card)', fontSize: 12, cursor: 'pointer' }}>{t('close')}</button>
        </div>
      </div>
    </>
  );
}
