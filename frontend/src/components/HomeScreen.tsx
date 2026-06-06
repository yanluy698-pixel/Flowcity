import { useState } from "react";
import { Avatar } from "./Avatar";
import { ChatInput } from "./ChatInput";
import { Header } from "./Header";

type Props = {
  onSubmit: (text: string) => void;
  onNewSession?: () => void;
  disabled?: boolean;
};

const examples = [
  "周六下午2点到6点，我从曲江池附近出发，带5岁孩子和老婆，老婆最近减脂，别太远，总预算400。",
  "今晚6点半我们4个人在钟楼地铁站集合，想citywalk加小吃，别走太多路，最后找个地方坐下来聊。",
  "周天1点到7点，我们3个男生从咸阳秦都站附近出发，坐地铁去西安市区玩，人均100以内。"
];

export function HomeScreen({ onSubmit, onNewSession, disabled }: Props) {
  const [draftText, setDraftText] = useState("");

  return (
    <main className="page">
      <section className="phone-shell">
        <Header onNewSession={onNewSession} disabled={disabled} />
        <div className="home-content">
          <section className="home-hero">
            <div className="home-hero-copy">
              <span className="mini-label">FlowCity · 西安周末闲时规划</span>
              <h1>把一句话变成能模拟执行的周末安排</h1>
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
          onClearDraft={() => setDraftText("")}
        />
      </section>
    </main>
  );
}
