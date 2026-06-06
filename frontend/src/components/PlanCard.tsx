import { buildMajorChangeDraft, buildNodeModifyDraft } from "../modifyIntents";
import type { ModifyDraft, TimelineItem } from "../types";

type Props = {
  payload: Record<string, any>;
  onConfirm: () => void;
  onRuntimeReplan: () => void;
  onModifyPrompt: (draft: ModifyDraft) => void;
  onHypothesisFeedback: (feedback: Record<string, unknown>) => void;
  totalDurationMs?: number;
};

function runtimeReplan(payload: Record<string, any>) {
  return payload.runtimeReplanResult ?? payload.executionResult?.runtimeReplanResult;
}

function finalPlan(payload: Record<string, any>) {
  const runtime = runtimeReplan(payload);
  if (runtime?.replannedFinalPlan) return runtime.replannedFinalPlan;
  if (runtime?.replannedTimelinePlan) return runtime.replannedTimelinePlan;
  const replan = payload.replanResult;
  if (replan?.success && replan.replannedTimelinePlan) return replan.replannedTimelinePlan;
  return payload.timelinePlan ?? {};
}

function activeDraft(payload: Record<string, any>) {
  return runtimeReplan(payload)?.replannedExecutionDraft ?? payload.executionDraft ?? {};
}

function money(value: unknown) {
  if (typeof value !== "number") return "¥0";
  return `¥${Math.round(value)}`;
}

function seconds(ms?: number) {
  if (typeof ms !== "number") return "";
  return `${(ms / 1000).toFixed(1)} 秒`;
}

function runtimeIssues(payload: Record<string, any>) {
  return payload.executionResult?.runtimeValidationResult?.issues ?? [];
}

const PLACE_LABELS: Record<string, string> = {
  area_xa_xiaozhai: "小寨",
  area_xa_qujiang: "曲江",
  area_xa_zhonglou: "钟楼",
  area_xa_gaoxin: "高新",
  area_xa_daminggong: "大明宫",
  area_xa_xingzheng: "行政中心",
  origin_xianyang_downtown: "咸阳市区",
  public_transport: "公共交通",
  taxi: "打车",
  walk: "步行"
};

function userText(value?: string) {
  if (!value) return "FlowCity 已加入这一步。";
  let text = value;
  for (const [from, to] of Object.entries(PLACE_LABELS)) {
    text = text.split(from).join(to);
  }
  return text
    .split("Mock ").join("")
    .split("Mock").join("")
    .split("阶段四").join("系统")
    .split("阶段五").join("确认前")
    .split("未返回明确预约时段").join("可到店取号")
    .split("routeRef").join("路线")
    .replace(/\s+/g, " ")
    .trim();
}

function compactTimelineText(value?: string) {
  const text = userText(value)
    .replace(/可执行依据：/g, "")
    .replace(/画像辅助参考：/g, "")
    .replace(/基础评分较高/g, "")
    .replace(/预算友好/g, "预算合适")
    .replace(/排队较短/g, "少排队")
    .replace(/；+/g, "；")
    .replace(/，+/g, "，")
    .trim();
  if (!text) return "这一步已安排好。";
  return text.length > 58 ? `${text.slice(0, 58)}...` : text;
}

function shareText(payload: Record<string, any>, totalDurationMs?: number) {
  const plan = finalPlan(payload);
  const timeline = (plan.timeline ?? []) as TimelineItem[];
  const budget = plan.budgetEstimate ?? {};
  const codes = payload.executionResult?.confirmationCodes ?? [];
  return [
    plan.summary ?? "FlowCity 方案",
    "",
    ...timeline.map((item) => `${item.start ?? ""}-${item.end ?? ""} ${item.title ?? item.type ?? "安排"}`),
    "",
    `预算：总计 ${money(budget.totalCost)}，人均 ${money(budget.perPersonCost)}`,
    totalDurationMs ? `生成用时：${seconds(totalDurationMs)}` : "",
    codes.length ? `Mock 确认码：${codes.map((item: any) => item.code).join("、")}` : "",
    "说明：这是 FlowCity 生成的演示确认信息，实际出行前以门店最新状态为准。"
  ].filter(Boolean).join("\n");
}

function decisionDraft(option: Record<string, any>): ModifyDraft {
  const label = String(option.label ?? "这个走法");
  const suggestion = String(option.userPrompt ?? label);
  return {
    label,
    suggestion,
    systemPrompt: `用户选择了这个走法：${label}。请按这个方向重新规划，保留原始时间、预算和人数约束；如果会牺牲吃饭、路程或游玩体验，要说明清楚。`
  };
}

export function PlanCard({ payload, onConfirm, onRuntimeReplan, onModifyPrompt, onHypothesisFeedback, totalDurationMs }: Props) {
  const plan = finalPlan(payload);
  const draft = activeDraft(payload);
  const timeline = (plan.timeline ?? []) as TimelineItem[];
  const budget = plan.budgetEstimate ?? {};
  const reasonBadges = Array.isArray(plan.reasonBadges) ? plan.reasonBadges.slice(0, 4) : [];
  const recommendationReasons = Array.isArray(plan.recommendationReasons)
    ? plan.recommendationReasons.slice(0, 3)
    : [];
  const mealTimingDecision = plan.mealTimingDecision;
  const decisionOptions = Array.isArray(plan.decisionOptions) ? plan.decisionOptions.slice(0, 3) : [];
  const executionResult = payload.executionResult;
  const issues = runtimeIssues(payload);
  const canReplan = executionResult?.canRuntimeReplan && !runtimeReplan(payload);
  const hasRuntimePlan = Boolean(runtimeReplan(payload));
  const confirmed = executionResult?.executionStatus === "confirmed";
  const confirmationCodes = executionResult?.confirmationCodes ?? [];
  const hasBlockingIssue = issues.some((issue: any) => issue.blocking);
  const needsUserReplanDecision = canReplan || ["blocked", "partial"].includes(executionResult?.executionStatus);
  const pendingActions = Array.isArray(draft?.pendingActions) ? draft.pendingActions : [];
  const planFailed = plan?.status === "failed";
  const friendlyBlockedReason = planFailed
    ? (Array.isArray(plan.recommendationReasons) && plan.recommendationReasons[0]) || plan.summary
    : draft?.blockedReason;
  const canConfirm =
    !confirmed &&
    !hasBlockingIssue &&
    !needsUserReplanDecision &&
    draft?.draftStatus !== "blocked" &&
    !planFailed &&
    pendingActions.length > 0;

  function copyShare() {
    navigator.clipboard?.writeText(shareText(payload, totalDurationMs));
  }

  return (
    <article className="plan-card">
      <div className="plan-section">
        <span className="mini-label">{hasRuntimePlan ? "新版方案" : "推荐方案"}</span>
        <h2>{userText(plan.summary ?? "已生成可执行周末计划")}</h2>
        <p>
          总计 {money(budget.totalCost)}，人均 {money(budget.perPersonCost)}
          {totalDurationMs ? `，生成用时 ${seconds(totalDurationMs)}` : ""}
        </p>
      </div>

      {(reasonBadges.length > 0 || recommendationReasons.length > 0) && (
        <div className="reason-panel">
          {reasonBadges.length > 0 && (
            <div className="reason-badges">
              {reasonBadges.map((badge: string) => (
                <span key={badge}>{userText(badge)}</span>
              ))}
            </div>
          )}
          {recommendationReasons.length > 0 && (
            <ul>
              {recommendationReasons.slice(0, 2).map((reason: string, index: number) => (
                <li key={`${index}-${reason}`}>{userText(reason)}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {mealTimingDecision && decisionOptions.length > 0 && (
        <div className="decision-panel">
          <strong>{userText(mealTimingDecision.title ?? "这里想让你拍个板")}</strong>
          <p>{userText(mealTimingDecision.message ?? "这次有两种都能走的安排，我把取舍说清楚，你选更舒服的那个。")}</p>
          <div className="decision-options">
            {decisionOptions.map((option: any) => {
              const active = option.id === mealTimingDecision.chosenOptionId;
              const previewTimeline = option.previewPlan?.timeline ?? [];
              return (
                <div className={`decision-option${active ? " active" : ""}`} key={option.id ?? option.label}>
                  <div className="decision-heading">
                    <span>{userText(option.label ?? "备选方案")}</span>
                    <em>{active ? "现在看到的是这个" : option.status === "ok" ? "也可以这样走" : "要稍微取舍"}</em>
                  </div>
                  <p>{userText(option.tradeoff ?? option.summary ?? "")}</p>
                  {Array.isArray(previewTimeline) && previewTimeline.length > 0 && (
                    <ul>
                      {previewTimeline.slice(0, 3).map((item: TimelineItem, index: number) => (
                        <li key={`${option.id}-${index}`}>{item.start ?? ""}-{item.end ?? ""} {item.title ?? item.type ?? "安排"}</li>
                      ))}
                    </ul>
                  )}
                  {!active && (
                    <button type="button" onClick={() => onModifyPrompt(decisionDraft(option))}>
                      就按这个来
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="timeline">
        {timeline.map((item, index) => (
          <div className="timeline-item" key={`${item.title}-${index}`}>
            <div className="time">{item.start ?? "--:--"}-{item.end ?? ""}</div>
            <div className="timeline-copy">
              <div className="timeline-heading">
                <strong>{item.title ?? item.type ?? "安排"}</strong>
                <button type="button" onClick={() => onModifyPrompt(buildNodeModifyDraft(item))}>
                  修改
                </button>
              </div>
              <p>{compactTimelineText(item.description ?? item.routeRef ?? "FlowCity 已加入这一步。")}</p>
            </div>
          </div>
        ))}
      </div>

      {issues.length > 0 && (
        <div className="runtime-box">
          <strong>确认前状态变化</strong>
          {issues.slice(0, 4).map((issue: any, index: number) => (
            <p key={`${issue.code}-${index}`}>{userText(issue.message)}</p>
          ))}
          {needsUserReplanDecision && <p>要不要按最新状态换一个更稳的方案？</p>}
        </div>
      )}

      {hasRuntimePlan && (
        <div className="runtime-box calm">
          <strong>已根据最新状态调整</strong>
          {(runtimeReplan(payload)?.replacementSummary?.changedBecause ?? [])
            .slice(0, 2)
            .map((text: string, index: number) => <p key={index}>{text}</p>)}
        </div>
      )}

      {!confirmed && !canConfirm && !needsUserReplanDecision && (
        <div className="runtime-box blocked">
          <strong>当前不能模拟执行</strong>
          <p>{userText(friendlyBlockedReason ?? "还没有可执行的锁票、预约或取号草案。")}</p>
        </div>
      )}

      {confirmed && (
        <div className="execution-done">
          <strong>已确认，可以发给朋友了</strong>
          <p>我把时间轴、预算和确认信息整理好了，下面可以一键复制。</p>
          {confirmationCodes.length > 0 ? confirmationCodes.map((item: any) => (
            <p key={item.code}>{item.type}：{item.code}</p>
          )) : <p>本方案不需要提前锁票或预约，到店前按时间轴出发即可。</p>}
        </div>
      )}

      {confirmed && (
        <div className="share-preview">
          <strong>分享预览</strong>
          <pre>{shareText(payload, totalDurationMs)}</pre>
        </div>
      )}

      <div className="primary-row">
        {needsUserReplanDecision && !hasRuntimePlan && (
          <button className="confirm-button secondary" onClick={onRuntimeReplan}>
            按最新状态重新规划
          </button>
        )}
        {canConfirm && (
          <button className="confirm-button" onClick={onConfirm}>
            {hasRuntimePlan ? "确认新版模拟执行" : "确认模拟执行"}
          </button>
        )}
        {confirmed && (
          <button className="confirm-button secondary" onClick={copyShare}>
            一键分享给朋友
          </button>
        )}
        {!confirmed && !hasRuntimePlan && (
          <button className="confirm-button secondary" onClick={() => onModifyPrompt(buildMajorChangeDraft(plan))}>
            整体大改
          </button>
        )}
      </div>
    </article>
  );
}
