import type { FlowEvent } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export async function runFlowStream(
  input: string,
  onEvent: (event: FlowEvent) => void,
  options?: { limit?: number; plannerLlm?: boolean; strictPlannerLlm?: boolean }
) {
  const response = await fetch(`${API_BASE}/api/flow/run-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input,
      limit: options?.limit ?? 3,
      plannerLlm: options?.plannerLlm ?? false,
      strictPlannerLlm: options?.strictPlannerLlm ?? false
    })
  });

  if (!response.ok || !response.body) {
    throw new Error(`FlowCity 服务暂时不可用：${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      onEvent(JSON.parse(line) as FlowEvent);
    }
  }

  if (buffer.trim()) {
    onEvent(JSON.parse(buffer) as FlowEvent);
  }
}

export async function confirmExecution(executionDraft: Record<string, unknown>) {
  const response = await fetch(`${API_BASE}/api/flow/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ executionDraft })
  });
  if (!response.ok) {
    throw new Error(`确认执行失败：${response.status}`);
  }
  return response.json();
}
