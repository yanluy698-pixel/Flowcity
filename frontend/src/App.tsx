import { useState } from "react";
import { confirmExecution, runFlowStream } from "./api/flowClient";
import { ChatScreen } from "./components/ChatScreen";
import { HomeScreen } from "./components/HomeScreen";
import type { ChatTurn, FlowEvent, ModifyDraft, StageState } from "./types";

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

const FOLLOW_UP_HINTS = [
  "换",
  "不要",
  "不想",
  "别去",
  "避开",
  "太远",
  "太贵",
  "不好",
  "质疑",
  "为什么",
  "重新",
  "想玩",
  "我要玩",
  "景点",
  "逛一下",
  "少走路",
  "晚饭",
  "晚餐",
  "吃饭",
  "早一点",
  "早点",
  "提前",
  "先吃"
];

function createTurn(displayInput: string, effectiveInput: string): ChatTurn {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    displayInput,
    effectiveInput,
    startedAt: performance.now(),
    stages: ORDERED_STAGES.map((stage) => ({ ...stage }))
  };
}

function updateTurnWithEvent(turn: ChatTurn, event: FlowEvent, eventTime: number): ChatTurn {
  if (event.stage === "router") {
    return turn;
  }

  if (event.type === "stage_start" && event.stage) {
    return {
      ...turn,
      stages: turn.stages.map((item) =>
        item.stage === event.stage
          ? { ...item, label: event.label ?? item.label, status: "active", startedAt: eventTime }
          : item.status === "active"
            ? {
                ...item,
                status: "done",
                endedAt: eventTime,
                durationMs: item.startedAt ? eventTime - item.startedAt : item.durationMs
              }
            : item
      )
    };
  }

  if (event.type === "stage_done" && event.stage) {
    return {
      ...turn,
      stages: turn.stages.map((item) =>
        item.stage === event.stage
          ? {
              ...item,
              status: "done",
              payload: event.payload,
              endedAt: eventTime,
              durationMs: item.startedAt ? eventTime - item.startedAt : item.durationMs
            }
          : item
      )
    };
  }

  if (event.type === "final") {
    return {
      ...turn,
      finalPayload: event.payload,
      completedAt: eventTime,
      totalDurationMs: eventTime - turn.startedAt,
      stages: turn.stages.map((item): StageState => ({
        ...item,
        status: item.status === "error" ? "error" : "done",
        endedAt: item.endedAt ?? eventTime,
        durationMs: item.durationMs ?? (item.startedAt ? eventTime - item.startedAt : undefined)
      }))
    };
  }

  if (event.type === "error") {
    return {
      ...turn,
      error: event.message ?? "FlowCity 运行失败",
      completedAt: eventTime,
      totalDurationMs: eventTime - turn.startedAt,
      stages: turn.stages.map((item) =>
        item.stage === event.stage || item.status === "active"
          ? { ...item, status: "error" }
          : item
      )
    };
  }

  return turn;
}

function isFollowUp(text: string, turns: ChatTurn[]) {
  return turns.length > 0 && FOLLOW_UP_HINTS.some((hint) => text.includes(hint));
}

export default function App() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [modifyDraft, setModifyDraft] = useState<ModifyDraft | undefined>();
  const [sessionId] = useState(() => `web_${Date.now()}_${Math.random().toString(16).slice(2)}`);

  async function handleSubmit(text: string) {
    if (!text.trim() || isRunning) return;
    const displayInput = text.trim();
    const activeDraft = modifyDraft;
    setModifyDraft(undefined);
    const lastTurn = turns[turns.length - 1];
    const hasModifyContext = Boolean(activeDraft?.systemPrompt);
    const shouldOptimizePrevious = hasModifyContext || isFollowUp(displayInput, turns);
    const effectiveInput = hasModifyContext
      ? `${activeDraft!.systemPrompt}\n\n【用户补充】${displayInput}`
      : displayInput;
    const turn = createTurn(displayInput, effectiveInput);
    setTurns((items) => [...items, turn]);
    setIsRunning(true);

    try {
      await runFlowStream(
        effectiveInput,
        (event) => {
          const eventTime = performance.now();
          setTurns((items) =>
            items.map((item) => (item.id === turn.id ? updateTurnWithEvent(item, event, eventTime) : item))
          );
        },
        {
          plannerLlm: false,
          strictPlannerLlm: false,
          limit: 8,
          sessionId,
          interactionMode: shouldOptimizePrevious ? "refine" : "auto",
          previousPlanId: lastTurn?.finalPayload?.planId
        }
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
    const runtimeReplan =
      turn?.finalPayload?.runtimeReplanResult ??
      turn?.finalPayload?.executionResult?.runtimeReplanResult;
    const draft = runtimeReplan?.replannedExecutionDraft ?? turn?.finalPayload?.executionDraft;
    if (!draft) return;
    try {
      const executionResult = await confirmExecution(draft, {
        structuredDemand: turn?.finalPayload?.structuredDemand,
        timelinePlan: runtimeReplan?.replannedTimelinePlan ?? turn?.finalPayload?.timelinePlan,
        mockSupply: runtimeReplan?.runtimeMockSupply ?? turn?.finalPayload?.mockSupply,
        plannerLlm: false
      });
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

  async function handleRuntimeReplan(turnId: string) {
    const turn = turns.find((item) => item.id === turnId);
    const payload = turn?.finalPayload;
    const draft = payload?.executionDraft;
    if (!payload || !draft) return;
    try {
      const executionResult = await confirmExecution(draft, {
        structuredDemand: payload.structuredDemand,
        timelinePlan: payload.timelinePlan,
        mockSupply: payload.mockSupply,
        plannerLlm: false,
        replanOnRuntimeFailure: true
      });
      setTurns((items) =>
        items.map((item) =>
          item.id === turnId && item.finalPayload
            ? {
                ...item,
                finalPayload: {
                  ...item.finalPayload,
                  executionResult,
                  runtimeReplanResult: executionResult.runtimeReplanResult
                }
              }
            : item
        )
      );
    } catch (error) {
      setTurns((items) =>
        items.map((item) =>
          item.id === turnId
            ? { ...item, error: error instanceof Error ? error.message : "重新规划失败" }
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
        modifyDraft={modifyDraft}
        onDraftPrompt={setModifyDraft}
        onClearDraft={() => setModifyDraft(undefined)}
        onConfirm={handleConfirm}
        onRuntimeReplan={handleRuntimeReplan}
        disabled={isRunning}
    />
  );
}
