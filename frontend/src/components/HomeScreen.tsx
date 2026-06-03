import { useState } from "react";
import { Avatar } from "./Avatar";
import { ChatInput } from "./ChatInput";
import { Header } from "./Header";

type Props = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
};

const guideCards = [
  { title: "人数人员", text: "例如：2大1小、和朋友、带父母" },
  { title: "出发位置", text: "例如：高新区、钟楼、咸阳" },
  { title: "时间窗口", text: "例如：周六下午、10点到14点" },
  { title: "偏好需求", text: "例如：亲子、拍照、少走路、安静" }
];

const examples = [
  "周六下午2点到6点，我从曲江池附近出发，带5岁孩子和老婆，老婆最近减脂，别太远，总预算400。",
  "今晚6点半我们4个人在钟楼地铁站集合，想citywalk加小吃，别走太多路，最后找个地方坐下来聊。",
  "周天1点到7点，我们3个男生从咸阳秦都站附近出发，坐地铁去西安市区玩，人均100以内。",
  "周六中午我们4个大学生从长安大学、西安交大、西北大学、陕师大出发，人均80以内，想找公平集合点。",
  "周六下午2点到6点，我从长安大学渭水校区出发，想和喜欢的女生去市区玩，吃的别油腻，总预算200。"
];

export function HomeScreen({ onSubmit, disabled }: Props) {
  const [draftText, setDraftText] = useState("");

  return (
    <main className="page">
      <section className="phone-shell">
        <Header />
        <div className="home-content">
          <section className="home-hero">
            <div className="home-hero-copy">
              <span className="mini-label">FlowCity · 西安周末闲时规划</span>
              <h1>把一句话变成能下单的周末安排</h1>
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

          <div className="guide-grid">
            {guideCards.map((card) => (
              <article className="guide-card" key={card.title}>
                <h3>{card.title}</h3>
                <p>{card.text}</p>
              </article>
            ))}
          </div>

          <p className="hint-title example-title">试试这样说</p>
          <div className="example-grid">
            {examples.map((example) => (
              <button key={example} onClick={() => setDraftText(example)} disabled={disabled}>
                {example}
              </button>
            ))}
          </div>
        </div>
        <ChatInput onSubmit={onSubmit} disabled={disabled} draftText={draftText} />
      </section>
    </main>
  );
}
