import { useMemo, useState } from "react";
import { confirmExecution, runFlowStream } from "./api/flowClient";
import { ChatScreen } from "./components/ChatScreen";
import { HomeScreen } from "./components/HomeScreen";
import type { ChatTurn, FlowEvent, StageState } from "./types";

const STAGE_LABELS: Record<string, string> = {
  extract: "理解需求",
  supply: "查活动、餐厅和路线",
  plan: "组合时间轴",
  validate: "校验预算、余票和路线风险",
  execute_draft: "生成执行草案"
};

const ORDERED_STAGES = Object.entries(STAGE_LABELS).map(([stage, label]) => ({
  stage,
  label,
  status: "pending" as const
}));

function createTurn(displayInput: string, effectiveInput: string): ChatTurn {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    displayInput,
    effectiveInput,
    stages: ORDERED_STAGES.map((stage) => ({ ...stage }))
  };
}

function updateTurnWithEvent(turn: ChatTurn, event: FlowEvent): ChatTurn {
  if (event.type === "stage_start" && event.stage) {
    return {
      ...turn,
      stages: turn.stages.map((item) =>
        item.stage === event.stage
          ? { ...item, label: event.label ?? item.label, status: "active" }
          : item.status === "active"
            ? { ...item, status: "done" }
            : item
      )
    };
  }

  if (event.type === "stage_done" && event.stage) {
    return {
      ...turn,
      stages: turn.stages.map((item) =>
        item.stage === event.stage ? { ...item, status: "done", payload: event.payload } : item
      )
    };
  }

  if (event.type === "final") {
    return {
      ...turn,
      finalPayload: event.payload,
      stages: turn.stages.map((item): StageState => ({
        ...item,
        status: item.status === "error" ? "error" : "done"
      }))
    };
  }

  if (event.type === "error") {
    return {
      ...turn,
      error: event.message ?? "FlowCity 运行失败",
      stages: turn.stages.map((item) =>
        item.stage === event.stage || item.status === "active"
          ? { ...item, status: "error" }
          : item
      )
    };
  }

  return turn;
}

export default function App() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [isRunning, setIsRunning] = useState(false);

  const lastEffectiveInput = useMemo(
    () => (turns.length > 0 ? turns[turns.length - 1].effectiveInput : undefined),
    [turns]
  );

  async function handleSubmit(text: string) {
    if (!text.trim() || isRunning) return;
    const displayInput = text.trim();
    const effectiveInput = lastEffectiveInput
      ? `${lastEffectiveInput}。用户补充修改：${displayInput}`
      : displayInput;
    const turn = createTurn(displayInput, effectiveInput);
    setTurns((items) => [...items, turn]);
    setIsRunning(true);

    try {
      await runFlowStream(
        effectiveInput,
        (event) => {
          setTurns((items) =>
            items.map((item) => (item.id === turn.id ? updateTurnWithEvent(item, event) : item))
          );
        },
        { plannerLlm: true, strictPlannerLlm: true }
      );
    } catch (error) {
      setTurns((items) =>
        items.map((item) =>
          item.id === turn.id
            ? { ...item, error: error instanceof Error ? error.message : "FlowCity 服务异常" }
            : item
        )
      );
    } finally {
      setIsRunning(false);
    }
  }

  async function handleConfirm(turnId: string) {
    const turn = turns.find((item) => item.id === turnId);
    const draft = turn?.finalPayload?.executionDraft;
    if (!draft) return;
    try {
      const executionResult = await confirmExecution(draft);
      setTurns((items) =>
        items.map((item) =>
          item.id === turnId && item.finalPayload
            ? { ...item, finalPayload: { ...item.finalPayload, executionResult } }
            : item
        )
      );
    } catch (error) {
      setTurns((items) =>
        items.map((item) =>
          item.id === turnId
            ? { ...item, error: error instanceof Error ? error.message : "确认执行失败" }
            : item
        )
      );
    }
  }

  return turns.length === 0 ? (
    <HomeScreen onSubmit={handleSubmit} disabled={isRunning} />
  ) : (
    <ChatScreen
      turns={turns}
      onSubmit={handleSubmit}
      onConfirm={handleConfirm}
      disabled={isRunning}
    />
  );
}
