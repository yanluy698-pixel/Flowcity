export type FlowEventType = "stage_start" | "stage_done" | "final" | "error";

export type FlowEvent = {
  type: FlowEventType;
  stage?: string;
  label?: string;
  payload?: Record<string, unknown>;
  message?: string;
};

export type StageStatus = "pending" | "active" | "done" | "error";

export type StageState = {
  stage: string;
  label: string;
  status: StageStatus;
  payload?: Record<string, unknown>;
  startedAt?: number;
  endedAt?: number;
  durationMs?: number;
};

export type ChatTurn = {
  id: string;
  displayInput: string;
  effectiveInput: string;
  stages: StageState[];
  startedAt: number;
  completedAt?: number;
  totalDurationMs?: number;
  finalPayload?: Record<string, any>;
  error?: string;
};

export type ModifyDraft = {
  label: string;
  suggestion: string;
  systemPrompt: string;
};

export type TimelineItem = {
  start?: string;
  end?: string;
  type?: string;
  title?: string;
  description?: string;
  poiId?: string;
  routeRef?: string;
  estimatedCost?: number;
};
