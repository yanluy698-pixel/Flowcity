import { Avatar } from "./Avatar";
import { ChatInput } from "./ChatInput";
import { Header } from "./Header";
import { PlanCard } from "./PlanCard";
import { StageProgress } from "./StageProgress";
import type { ChatTurn } from "../types";

type Props = {
  turns: ChatTurn[];
  onSubmit: (text: string) => void;
  onConfirm: (turnId: string) => void;
  disabled?: boolean;
};

export function ChatScreen({ turns, onSubmit, onConfirm, disabled }: Props) {
  return (
    <main className="page">
      <section className="phone-shell">
        <Header />
        <div className="chat-scroll">
          {turns.map((turn) => (
            <section className="turn" key={turn.id}>
              <div className="user-bubble">{turn.displayInput}</div>
              <div className="assistant-row compact">
                <Avatar />
                <StageProgress stages={turn.stages} />
              </div>
              {turn.finalPayload && (
                <div className="assistant-row compact">
                  <Avatar />
                  <PlanCard
                    payload={turn.finalPayload}
                    onConfirm={() => onConfirm(turn.id)}
                  />
                </div>
              )}
              {turn.error && (
                <div className="assistant-row compact">
                  <Avatar />
                  <div className="error-bubble">{turn.error}</div>
                </div>
              )}
            </section>
          ))}
        </div>
        <ChatInput
          onSubmit={onSubmit}
          disabled={disabled}
          placeholder={disabled ? "FlowCity 正在处理..." : "继续提要求，比如：少走路一点"}
        />
      </section>
    </main>
  );
}
