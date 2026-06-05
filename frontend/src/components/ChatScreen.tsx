import { Avatar } from "./Avatar";
import { ChatInput } from "./ChatInput";
import { Header } from "./Header";
import { PlanCard } from "./PlanCard";
import { StageProgress } from "./StageProgress";
import type { ChatTurn, ModifyDraft } from "../types";

type Props = {
  turns: ChatTurn[];
  onSubmit: (text: string) => void;
  modifyDraft?: ModifyDraft;
  onDraftPrompt: (draft: ModifyDraft) => void;
  onClearDraft: () => void;
  onConfirm: (turnId: string) => void;
  onRuntimeReplan: (turnId: string) => void;
  onHypothesisFeedback: (turnId: string, feedback: Record<string, unknown>) => void;
  disabled?: boolean;
};

export function ChatScreen({
  turns,
  onSubmit,
  modifyDraft,
  onDraftPrompt,
  onClearDraft,
  onConfirm,
  onRuntimeReplan,
  onHypothesisFeedback,
  disabled
}: Props) {
  return (
    <main className="page">
      <section className="phone-shell">
        <Header />
        <div className="chat-scroll">
          {turns.map((turn) => (
            <section className="turn" key={turn.id}>
              <div className="user-bubble">{turn.displayInput}</div>
              {!turn.finalPayload?.assistantMessage && (
                <div className="assistant-row compact">
                  <Avatar />
                  <StageProgress stages={turn.stages} totalDurationMs={turn.totalDurationMs} />
                </div>
              )}
              {turn.finalPayload?.planExplanation?.message && (
                <div className="assistant-row compact">
                  <Avatar />
                  <div className="assistant-bubble explanation-bubble">
                    <strong>方案解释</strong>
                    {String(turn.finalPayload.planExplanation.message)
                      .split("\n")
                      .map((line, index) => (
                        <span key={`${turn.id}-explain-${index}`}>{line}</span>
                      ))}
                  </div>
                </div>
              )}
              {turn.finalPayload?.assistantMessage && (
                <div className="assistant-row compact">
                  <Avatar />
                  <div className="assistant-bubble suggestion-bubble">
                    <strong>先确认一个关键点</strong>
                    <span>{String(turn.finalPayload.assistantMessage.message)}</span>
                    <div className="quick-replies">
                      {((turn.finalPayload.assistantMessage.quickReplies ?? []) as string[]).map((reply) => (
                        <button key={reply} type="button" onClick={() => onSubmit(reply)} disabled={disabled}>
                          {reply}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              {turn.finalPayload && !turn.finalPayload.assistantMessage && (
                <div className="assistant-row compact">
                  <Avatar />
                  <PlanCard
                    payload={turn.finalPayload}
                    onConfirm={() => onConfirm(turn.id)}
                    onRuntimeReplan={() => onRuntimeReplan(turn.id)}
                    onModifyPrompt={onDraftPrompt}
                    onHypothesisFeedback={(feedback) => onHypothesisFeedback(turn.id, feedback)}
                    totalDurationMs={turn.totalDurationMs}
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
          draft={modifyDraft}
          onClearDraft={onClearDraft}
          disabled={disabled}
          placeholder={disabled ? "FlowCity 正在处理..." : "继续提要求，比如：少走路一点"}
        />
      </section>
    </main>
  );
}
