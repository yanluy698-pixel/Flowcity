import { useState } from "react";
import { Avatar } from "./Avatar";
import { ChatInput } from "./ChatInput";
import { Header } from "./Header";
import type { SessionHistoryEntry } from "../types";

type Props = {
  onSubmit: (text: string) => void;
  onNewSession?: () => void;
  onResumeSession?: (entry: SessionHistoryEntry) => void;
  sessionHistory?: SessionHistoryEntry[];
  disabled?: boolean;
};

const examples = [
  "周六下午2点到6点，我从曲江池附近出发，带5岁孩子和老婆出去玩，老婆最近减脂，别太远，总预算400，最好6点前能回到家附近。",
  "今晚6点半我们4个人在钟楼地铁站集合，2男2女，想citywalk加小吃，但别走太多路，最后找个地方坐下来聊，10点前结束。",
  "周天1点到7点，我们3个男生从咸阳秦都站附近出发，坐地铁去西安市区玩，人均100以内。",
  "我们4个大学生周六中午一点集合，分别从长安大学、西安交大、西北大学和陕师大出发，希望找个对大家都公平的地方，再安排吃和玩，人均100以内。",
  "周六下午2点到6点，我从长安大学渭水校区出发，想去市区和喜欢的女生玩，她最近减肥，吃的要好吃但别油腻，总预算200以内，别安排得太正式。"
];

function formatHistoryTime(updatedAt: number) {
  return new Date(updatedAt).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function HomeScreen({
  onSubmit,
  onNewSession,
  onResumeSession,
  sessionHistory = [],
  disabled
}: Props) {
  const [draftText, setDraftText] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const hasHistory = sessionHistory.length > 0;

  return (
    <main className="page">
      <section className="phone-shell">
        <Header
          onNewSession={onNewSession}
          onHistoryClick={hasHistory ? () => setHistoryOpen(true) : undefined}
          hasHistory={hasHistory}
          disabled={disabled}
        />
        <div className="home-content">
          <section className="home-hero">
            <div className="home-hero-copy">
              <span className="mini-label">FlowCity · 西安周末闲时规划</span>
              <h1>把一句话变成能落地的周末安排</h1>
              <p>先理解需求，再查活动、餐厅、路线和动态状态；确认前如果变了，就重新给你排一版。</p>
            </div>
            <div className="home-yellow-panel">
              <strong>今天可以处理</strong>
              <span>亲子 / Citywalk / 低预算 / 多人集合 / 轻约会</span>
            </div>
          </section>

          <div className="assistant-row">
            <Avatar />
            <div className="assistant-bubble">
              <strong>周末想去哪？直接说一句，</strong>
              <span>我来把活动、吃饭、路线和预约一起安排好。</span>
            </div>
          </div>
          <p className="hint-title">说得越具体，计划越合适</p>

          <p className="hint-title example-title">也可以点一个示例再改</p>
          <div className="example-grid">
            {examples.map((example) => (
              <button key={example} onClick={() => setDraftText(example)} disabled={disabled}>
                {example}
              </button>
            ))}
          </div>
        </div>
        <ChatInput
          onSubmit={onSubmit}
          disabled={disabled}
          draft={draftText ? { label: "示例", suggestion: draftText, systemPrompt: "" } : undefined}
          showDraftPreview={false}
          onClearDraft={() => setDraftText("")}
        />
        {historyOpen && (
          <div className="history-drawer-layer">
            <button
              type="button"
              className="history-backdrop"
              aria-label="关闭历史记录"
              onClick={() => setHistoryOpen(false)}
            />
            <aside className="history-drawer" aria-label="历史规划">
              <div className="history-drawer-head">
                <strong>最近规划</strong>
                <button type="button" onClick={() => setHistoryOpen(false)} aria-label="关闭历史记录">
                  ×
                </button>
              </div>
              <div className="history-list">
                {sessionHistory.map((entry) => (
                  <button
                    key={entry.sessionId}
                    type="button"
                    className="history-item"
                    onClick={() => {
                      onResumeSession?.(entry);
                      setHistoryOpen(false);
                    }}
                    disabled={disabled}
                  >
                    <span>
                      <strong>{entry.title}</strong>
                      <small>{entry.lastInput}</small>
                    </span>
                    <em>{formatHistoryTime(entry.updatedAt)}</em>
                  </button>
                ))}
              </div>
            </aside>
          </div>
        )}
      </section>
    </main>
  );
}
