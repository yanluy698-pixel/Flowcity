import { useEffect, useState } from "react";
import { confirmExecution, runFlowStream } from "./api/flowClient";
import { AdminConsole } from "./components/AdminConsole";
import { ChatScreen } from "./components/ChatScreen";
import { HomeScreen } from "./components/HomeScreen";
import type { ChatTurn, FlowEvent, ModifyDraft, SessionHistoryEntry, StageState } from "./types";

const STAGE_LABELS: Record<string, string> = {
  extract: "理解需求",
  area: "比较可行区域与点名目的地",
  supply: "在入围区域中寻找地点",
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
  "空窗",
  "缓冲",
  "等位",
  "时间段",
  "这段",
  "加点",
  "加些",
  "加一个",
  "奶茶",
  "茶饮",
  "休息",
  "优化",
  "少走路",
  "晚饭",
  "晚餐",
  "吃饭",
  "早一点",
  "早点",
  "提前",
  "先吃"
];

function createTurn(displayInput: string, effectiveInput: string, modifyContextLabel?: string): ChatTurn {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    displayInput,
    effectiveInput,
    modifyContextLabel,
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
      error: friendlyError(event.message),
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

function friendlyError(message?: string) {
  const text = String(message || "");
  if (/Stage\s*2|validation/i.test(text)) {
    return "我刚刚理解需求时有个字段没对齐，已经需要重新整理一下。";
  }
  if (/Stage\s*5|execution is not allowed/i.test(text)) {
    return "确认状态刚刚没对齐，我会把这版方案重新对齐一下。";
  }
  return text || "FlowCity 运行失败";
}

function isFollowUp(text: string, turns: ChatTurn[]) {
  return turns.length > 0 && FOLLOW_UP_HINTS.some((hint) => text.includes(hint));
}

function looksLikeNewPlan(text: string) {
  const hasTime = /(?:周[一二三四五六日天末]|今天|明天|今晚|下午|晚上|中午|早上|上午|\d{1,2}\s*点)/.test(text);
  const hasPeople = /(?:\d+\s*个(?:人|男生|女生|大学生)?|[一二三四五六七八九十]\s*个(?:人|男生|女生|大学生)?|带孩子|带娃|朋友|同学|男生|女生)/.test(text);
  const hasPlanVerb = /(?:想|希望|安排|规划|出去|出来|集合|出发|去|玩|逛|吃饭|citywalk|预算|结束)/i.test(text);
  const hasPlace = /(?:钟楼|小寨|赛格|曲江|大雁塔|大唐不夜城|高新|大明宫|行政中心|咸阳|西安|大学|校区|地铁站)/.test(text);
  return hasPlanVerb && ((hasTime && hasPeople) || (hasTime && hasPlace) || (hasPeople && hasPlace && text.length > 22));
}

function isClearlyNonPlanningInput(text: string) {
  const value = text.trim();
  if (!value) return false;
  const planningSignal =
    /(?:周|今天|明天|今晚|中午|下午|晚上|早上|上午|\d{1,2}\s*点|人|男生|女生|同学|朋友|孩子|老婆|对象|约会|聚会|集合|出发|回家|回校|预算|人均|元|吃饭|晚饭|午饭|餐|小吃|玩|逛|citywalk|商圈|地铁|附近|路线|安排|规划)/i.test(value);
  if (planningSignal || looksLikeNewPlan(value)) return false;
  if (/^(你好|您好|hello|hi|嗨|在吗|谢谢|感谢|你是谁|你能干嘛|你能做什么)[。！!？?\s]*$/i.test(value)) {
    return true;
  }
  return value.length <= 18 && /(?:讲个笑话|写代码|翻译|天气|股票|新闻|闲聊|唱歌|画图)/.test(value);
}

const LEGACY_SESSION_STORAGE_KEY = "flowcity.sessionId";
const SESSION_HISTORY_KEY = "flowcity.sessionHistory.v1";
const SESSION_HISTORY_TTL_MS = 2 * 60 * 60 * 1000;
const MAX_SESSION_HISTORY = 8;

function newWebSessionId() {
  return `web_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function compactTitle(text: string) {
  const value = text.replace(/\s+/g, " ").trim();
  return value.length > 28 ? `${value.slice(0, 28)}...` : value || "未命名规划";
}

function pruneSessionHistory(entries: SessionHistoryEntry[], now = Date.now()) {
  return entries
    .filter((entry) => entry.sessionId && now - Number(entry.updatedAt || 0) <= SESSION_HISTORY_TTL_MS)
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .slice(0, MAX_SESSION_HISTORY);
}

function loadSessionHistory() {
  try {
    window.localStorage.removeItem(LEGACY_SESSION_STORAGE_KEY);
    const raw = window.localStorage.getItem(SESSION_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return pruneSessionHistory(parsed);
  } catch {
    return [];
  }
}

function saveSessionHistory(entries: SessionHistoryEntry[]) {
  const next = pruneSessionHistory(entries);
  try {
    window.localStorage.setItem(SESSION_HISTORY_KEY, JSON.stringify(next));
  } catch {
    // History is a convenience layer; planning should still work without localStorage.
  }
  return next;
}

function compactTurnForHistory(turn: ChatTurn): ChatTurn {
  return {
    ...turn,
    stages: turn.stages.map(({ payload: _payload, ...stage }) => stage),
    effectiveInput: turn.displayInput
  };
}

function buildSessionHistoryEntry(sessionId: string, turns: ChatTurn[]): SessionHistoryEntry | null {
  if (!sessionId || turns.length === 0) return null;
  const firstInput = turns[0]?.displayInput ?? "";
  const lastInput = turns[turns.length - 1]?.displayInput ?? firstInput;
  return {
    sessionId,
    title: compactTitle(firstInput),
    lastInput: compactTitle(lastInput),
    updatedAt: Date.now(),
    turns: turns.slice(-4).map(compactTurnForHistory)
  };
}

export default function App() {
  const [hash, setHash] = useState(() => window.location.hash);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [modifyDraft, setModifyDraft] = useState<ModifyDraft | undefined>();
  const [sessionId, setSessionId] = useState(newWebSessionId);
  const [sessionHistory, setSessionHistory] = useState<SessionHistoryEntry[]>(loadSessionHistory);

  useEffect(() => {
    const onHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const entry = buildSessionHistoryEntry(sessionId, turns);
    if (!entry) return;
    setSessionHistory((items) =>
      saveSessionHistory([entry, ...items.filter((item) => item.sessionId !== entry.sessionId)])
    );
  }, [sessionId, turns]);

  function handleNewSession() {
    if (isRunning) return;
    setSessionId(newWebSessionId());
    setTurns([]);
    setModifyDraft(undefined);
  }

  function handleResumeSession(entry: SessionHistoryEntry) {
    if (isRunning) return;
    setSessionId(entry.sessionId);
    setTurns(entry.turns ?? []);
    setModifyDraft(undefined);
  }

  async function handleSubmit(text: string, hypothesisFeedback?: Record<string, unknown>) {
    if (!text.trim() || isRunning) return;
    const displayInput = text.trim();
    if (!modifyDraft && isClearlyNonPlanningInput(displayInput)) {
      const now = performance.now();
      const turn: ChatTurn = {
        ...createTurn(displayInput, displayInput),
        completedAt: now,
        totalDurationMs: 0,
        finalPayload: {
          assistantMessage: {
            mode: "info",
            message:
              "我主要帮你做周末 4-6 小时本地出行规划：把一句话拆成时间、人数、预算、地点和偏好，再组合可执行的玩、吃、路线和确认流程。你可以直接说“周六下午从哪出发、几个人、预算多少、想怎么玩”。",
            quickReplies: [
              "周六下午2点到6点，带孩子轻松玩",
              "今晚6点半朋友在钟楼集合，想逛吃",
              "周天1点到7点，从咸阳坐地铁去西安市区玩"
            ]
          }
        },
        stages: ORDERED_STAGES.map((stage) => ({ ...stage, status: "done" as const }))
      };
      setTurns((items) => [...items, turn]);
      return;
    }
    const activeDraft = modifyDraft;
    setModifyDraft(undefined);
    const lastTurn = turns[turns.length - 1];
    const hasModifyContext = Boolean(activeDraft?.systemPrompt);
    const shouldStartNewPlan = !hasModifyContext && looksLikeNewPlan(displayInput);
    const shouldOptimizePrevious = !shouldStartNewPlan && (hasModifyContext || isFollowUp(displayInput, turns));
    const runSessionId = shouldStartNewPlan ? newWebSessionId() : sessionId;
    if (shouldStartNewPlan) {
      setSessionId(runSessionId);
      setTurns([]);
    }
    const effectiveInput = hasModifyContext
      ? `${activeDraft!.systemPrompt}\n\n【用户补充】${displayInput}`
      : displayInput;
    const turn = createTurn(displayInput, effectiveInput, activeDraft?.label);
    setTurns((items) => (shouldStartNewPlan ? [turn] : [...items, turn]));
    setIsRunning(true);
    let pendingEvents: Array<{ event: FlowEvent; eventTime: number }> = [];
    let flushTimer: number | undefined;
    const flushPendingEvents = () => {
      if (flushTimer !== undefined) {
        window.clearTimeout(flushTimer);
        flushTimer = undefined;
      }
      if (!pendingEvents.length) return;
      const events = pendingEvents;
      pendingEvents = [];
      setTurns((items) =>
        items.map((item) =>
          item.id === turn.id
            ? events.reduce((next, queued) => updateTurnWithEvent(next, queued.event, queued.eventTime), item)
            : item
        )
      );
    };

    try {
      await runFlowStream(
        effectiveInput,
        (event) => {
          const eventTime = performance.now();
          if (event.type === "final" || event.type === "error") {
            flushPendingEvents();
            setTurns((items) =>
              items.map((item) => (item.id === turn.id ? updateTurnWithEvent(item, event, eventTime) : item))
            );
            return;
          }
          pendingEvents.push({ event, eventTime });
          if (flushTimer === undefined) {
            flushTimer = window.setTimeout(flushPendingEvents, 700);
          }
        },
        {
          plannerLlm: false,
          strictPlannerLlm: false,
          limit: 12,
          sessionId: runSessionId,
          interactionMode: shouldStartNewPlan ? "new_plan" : shouldOptimizePrevious ? "refine" : "auto",
          previousPlanId: shouldStartNewPlan ? undefined : lastTurn?.finalPayload?.planId,
          hypothesisFeedback,
          constraintsPatch: activeDraft?.constraintsPatch,
          debug: false
        }
      );
    } catch (error) {
      setTurns((items) =>
        items.map((item) =>
          item.id === turn.id
            ? { ...item, error: friendlyError(error instanceof Error ? error.message : "FlowCity 服务异常") }
            : item
        )
      );
    } finally {
      flushPendingEvents();
      setIsRunning(false);
    }
  }

  function handleHypothesisFeedback(turnId: string, feedback: Record<string, unknown>) {
    const turn = turns.find((item) => item.id === turnId);
    const text = String(feedback.text ?? "这个隐性需求猜测");
    handleSubmit(`不要采用“${text}”这个需求猜测，保留其他安排重新规划。`, feedback);
  }

  async function handleConfirm(turnId: string) {
    const turn = turns.find((item) => item.id === turnId);
    if (!turn?.finalPayload) return;
    try {
      const executionResult = await confirmExecution({
        plannerLlm: false,
        sessionId,
        planId: turn?.finalPayload?.planId
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
            ? { ...item, error: friendlyError(error instanceof Error ? error.message : "确认执行失败") }
            : item
        )
      );
    }
  }

  async function handleRuntimeReplan(turnId: string) {
    const turn = turns.find((item) => item.id === turnId);
    const payload = turn?.finalPayload;
    if (!payload) return;
    try {
      const executionResult = await confirmExecution({
        plannerLlm: false,
        replanOnRuntimeFailure: true,
        sessionId,
        planId: payload.planId
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
            ? { ...item, error: friendlyError(error instanceof Error ? error.message : "重新规划失败") }
            : item
        )
      );
    }
  }

  if (hash === "#admin") {
    return <AdminConsole />;
  }

  return turns.length === 0 ? (
    <HomeScreen
      onSubmit={handleSubmit}
      onNewSession={handleNewSession}
      onResumeSession={handleResumeSession}
      sessionHistory={sessionHistory}
      disabled={isRunning}
    />
  ) : (
      <ChatScreen
        turns={turns}
        onSubmit={handleSubmit}
        onNewSession={handleNewSession}
        modifyDraft={modifyDraft}
        onDraftPrompt={setModifyDraft}
        onClearDraft={() => setModifyDraft(undefined)}
        onConfirm={handleConfirm}
        onRuntimeReplan={handleRuntimeReplan}
        onHypothesisFeedback={handleHypothesisFeedback}
        disabled={isRunning}
    />
  );
}
