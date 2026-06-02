import { Clock3, MapPinned, UsersRound, WandSparkles } from "lucide-react";
import { Avatar } from "./Avatar";
import { ChatInput } from "./ChatInput";
import { Header } from "./Header";

type Props = {
  onSubmit: (text: string) => void;
  disabled?: boolean;
};

const guideCards = [
  { icon: UsersRound, title: "人数人员", text: "例如：2大1小、和朋友、带父母" },
  { icon: MapPinned, title: "出发位置", text: "例如：高新区、钟楼、咸阳" },
  { icon: Clock3, title: "时间窗口", text: "例如：周六下午、10点到14点" },
  { icon: WandSparkles, title: "偏好需求", text: "例如：亲子、拍照、少走路、安静" }
];

const examples = ["👶 亲子半日游", "🙂 朋友 citywalk", "🚕 咸阳到西安", "💳 预算少点"];

export function HomeScreen({ onSubmit, disabled }: Props) {
  return (
    <main className="page">
      <section className="phone-shell">
        <Header />
        <div className="home-content">
          <div className="assistant-row">
            <Avatar />
            <div className="assistant-bubble">
              <strong>周末想去哪？直接说一句，</strong>
              <span>我来把活动、吃饭、路线和预约一起安排好。</span>
            </div>
          </div>
          <p className="hint-title">说得越具体，计划越合适</p>

          <div className="guide-grid">
            {guideCards.map((card) => {
              const Icon = card.icon;
              return (
                <article className="guide-card" key={card.title}>
                  <div className="guide-icon">
                    <Icon size={18} />
                  </div>
                  <h3>{card.title}</h3>
                  <p>{card.text}</p>
                </article>
              );
            })}
          </div>

          <p className="hint-title example-title">试试这样说</p>
          <div className="example-grid">
            {examples.map((example) => (
              <button key={example} onClick={() => onSubmit(example)} disabled={disabled}>
                {example}
              </button>
            ))}
          </div>
        </div>
        <ChatInput onSubmit={onSubmit} disabled={disabled} />
      </section>
    </main>
  );
}
