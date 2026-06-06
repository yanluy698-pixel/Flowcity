import type { FlowEvent } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export async function runFlowStream(
  input: string,
  onEvent: (event: FlowEvent) => void,
  options?: {
    limit?: number;
    plannerLlm?: boolean;
    strictPlannerLlm?: boolean;
    sessionId?: string;
    interactionMode?: "auto" | "new_plan" | "refine";
    previousPlanId?: string;
    hypothesisFeedback?: Record<string, unknown>;
  }
) {
  const response = await fetch(`${API_BASE}/api/flow/run-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input,
      limit: options?.limit ?? 8,
      plannerLlm: options?.plannerLlm ?? false,
      strictPlannerLlm: options?.strictPlannerLlm ?? false,
      sessionId: options?.sessionId,
      interactionMode: options?.interactionMode ?? "auto",
      previousPlanId: options?.previousPlanId,
      hypothesisFeedback: options?.hypothesisFeedback
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

export async function confirmExecution(options?: {
  plannerLlm?: boolean;
  replanOnRuntimeFailure?: boolean;
  sessionId?: string;
  planId?: string;
}) {
  const response = await fetch(`${API_BASE}/api/flow/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plannerLlm: options?.plannerLlm ?? false,
      replanOnRuntimeFailure: options?.replanOnRuntimeFailure ?? false,
      sessionId: options?.sessionId,
      planId: options?.planId
    })
  });
  if (!response.ok) {
    throw new Error(`确认执行失败：${response.status}`);
  }
  return response.json();
}

function adminHeaders(token: string) {
  return {
    "Content-Type": "application/json",
    "X-FlowCity-Admin-Token": token
  };
}

async function adminJson(path: string, token: string, options?: RequestInit) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...adminHeaders(token),
      ...(options?.headers ?? {})
    }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(String(payload.detail ?? payload.error ?? `请求失败：${response.status}`));
  }
  return payload;
}

export async function fetchAdminDatasets(token: string) {
  return adminJson("/api/admin/datasets", token);
}

export async function fetchAdminCoverage(token: string) {
  return adminJson("/api/admin/coverage", token);
}

export async function saveAdminRecord(
  token: string,
  slug: string,
  collectionKey: string,
  recordIndex: number,
  record: Record<string, unknown>
) {
  return adminJson(
    `/api/admin/datasets/${encodeURIComponent(slug)}/${encodeURIComponent(collectionKey)}/${recordIndex}`,
    token,
    {
      method: "PUT",
      body: JSON.stringify({ record })
    }
  );
}

export async function createAdminRecord(
  token: string,
  slug: string,
  collectionKey: string,
  record: Record<string, unknown>
) {
  return adminJson(`/api/admin/datasets/${encodeURIComponent(slug)}/${encodeURIComponent(collectionKey)}`, token, {
    method: "POST",
    body: JSON.stringify({ record })
  });
}

export async function deleteAdminRecord(
  token: string,
  slug: string,
  collectionKey: string,
  recordIndex: number
) {
  return adminJson(
    `/api/admin/datasets/${encodeURIComponent(slug)}/${encodeURIComponent(collectionKey)}/${recordIndex}`,
    token,
    { method: "DELETE" }
  );
}

export async function fetchLearningAnalysis(token: string) {
  return adminJson("/api/learning/analysis", token);
}

export async function fetchLearningProposals(token: string) {
  return adminJson("/api/learning/proposals", token);
}

export async function reviewLearningProposal(
  token: string,
  proposalId: string,
  status: "approved" | "rejected" | "pending_review"
) {
  return adminJson(`/api/learning/proposals/${encodeURIComponent(proposalId)}/review`, token, {
    method: "POST",
    body: JSON.stringify({ status })
  });
}
