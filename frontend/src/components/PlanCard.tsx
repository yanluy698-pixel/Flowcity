import { useState } from "react";
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
    .split("Stage 5 validation failed; execution is not allowed.").join("确认状态刚刚刷新了，方案还在，可以继续确认。")
    .split("execution is not allowed").join("确认状态刚刚刷新了，方案还在，可以继续确认")
    .split("当前不能模拟执行").join("确认状态刚刚刷新了")
    .split("模拟执行").join("确认")
    .split("未返回明确预约时段").join("可到店取号")
    .split("中段补充").join("顺路安排")
    .split("补充体验：").join("")
    .split("补充体验:").join("")
    .split("补充体验").join("顺路安排")
    .split("把空出来的时间变成顺路可逛的内容").join("中间顺路逛一下，不用干等")
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
    codes.length ? `确认码：${codes.map((item: any) => item.code).join("、")}` : "",
    "说明：出发前再看一眼门店和交通状态。"
  ].filter(Boolean).join("\n");
}

function decisionDraft(option: Record<string, any>): ModifyDraft {
  const label = String(option.label ?? "这个走法");
  const suggestion = label.startsWith("按") ? `${label}重新排` : `按${label}重新排`;
  const timing = option.constraintsPatch?.mealTiming;
  return {
    label,
    suggestion,
    systemPrompt: `用户已经明确选择饭点方案：${label}${timing ? `（mealTiming=${timing}）` : ""}。请直接按这个选择重新规划，保留原始时间、预算和人数约束；不要再次要求用户在饭点方案之间二选一。`,
    constraintsPatch: option.constraintsPatch,
    prefillInput: true
  };
}

export function PlanCard({ payload, onConfirm, onRuntimeReplan, onModifyPrompt, onHypothesisFeedback, totalDurationMs }: Props) {
  const [orderSubmitted, setOrderSubmitted] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const plan = finalPlan(payload);
  const draft = activeDraft(payload);
  const timeline = (plan.timeline ?? []) as TimelineItem[];
  const budget = plan.budgetEstimate ?? {};
  const mealTimingDecision = plan.mealTimingDecision;
  const decisionOptions = Array.isArray(plan.decisionOptions) ? plan.decisionOptions.slice(0, 3) : [];
  const executionResult = payload.executionResult;
  const issues = runtimeIssues(payload);
  const canReplan = executionResult?.canRuntimeReplan && !runtimeReplan(payload);
  const hasRuntimePlan = Boolean(runtimeReplan(payload));
  const confirmed = executionResult?.executionStatus === "confirmed";
  const confirmationCodes = executionResult?.confirmationCodes ?? [];
  const hasBlockingIssue = issues.some((issue: any) => issue.blocking);
  const needsUserReplanDecision = ["blocked", "partial"].includes(executionResult?.executionStatus);
  const planFailed = plan?.status === "failed";
  const decisionRequired = Boolean(plan?.decisionRequired || decisionOptions.length > 0 || draft?.requiresPlanChoice);
  const friendlyBlockedReason = planFailed ? plan.summary : draft?.blockedReason;
  const draftActuallyBlocked = draft?.draftStatus === "blocked" && !decisionRequired;
  const hasConfirmablePlan = !planFailed && timeline.length > 0;
  const canConfirm = !confirmed && hasConfirmablePlan && !decisionRequired && !draftActuallyBlocked && !hasBlockingIssue && !needsUserReplanDecision;
  const canShare = !planFailed && timeline.length > 0;
  const canRefreshByRuntime = canReplan && !hasRuntimePlan && !confirmed;

  function copyShare() {
    navigator.clipboard?.writeText(shareText(payload, totalDurationMs));
    setShareCopied(true);
    window.setTimeout(() => setShareCopied(false), 1800);
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

      {mealTimingDecision && decisionOptions.length > 0 && (
        <div className="decision-panel">
          <strong>饭点有两种排法</strong>
          <div className="decision-options">
            {decisionOptions.map((option: any) => {
              const previewTimeline = option.previewPlan?.timeline ?? [];
              return (
                <div className="decision-option" key={option.id ?? option.label}>
                  <div className="decision-heading">
                    <span>{userText(option.label ?? "备选方案")}</span>
                  </div>
                  {Array.isArray(previewTimeline) && previewTimeline.length > 0 && (
                    <ul>
                      {previewTimeline.slice(0, 3).map((item: TimelineItem, index: number) => (
                        <li key={`${option.id}-${index}`}>{item.start ?? ""}-{item.end ?? ""} {item.title ?? item.type ?? "安排"}</li>
                      ))}
                    </ul>
                  )}
                  <button type="button" onClick={() => onModifyPrompt(decisionDraft(option))}>
                    就按这个来
                  </button>
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
          {hasBlockingIssue && <p>这一步先别直接确认，按最新状态换一版会更稳。</p>}
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

      {!confirmed && !canConfirm && !needsUserReplanDecision && draftActuallyBlocked && !hasConfirmablePlan && (
        <div className="runtime-box blocked">
          <strong>这版暂时不适合确认</strong>
          <p>{userText(friendlyBlockedReason ?? "有一步条件没满足，可以点修改换个更稳的走法。")}</p>
        </div>
      )}

      {confirmed && (
        <div className="order-card">
          <div className="order-card-head">
            <span>行程已确认</span>
            <strong>{money(budget.totalCost)} · 人均 {money(budget.perPersonCost)}</strong>
          </div>
          <div className="order-mini-timeline">
            {timeline.slice(0, 4).map((item, index) => (
              <span key={`${item.start}-${item.title}-${index}`}>
                {item.start ?? "--:--"} {item.title ?? item.type ?? "安排"}
              </span>
            ))}
          </div>
          {confirmationCodes.length > 0 && (
            <p>{confirmationCodes.slice(0, 2).map((item: any) => item.code).join("、")}</p>
          )}
          <button
            type="button"
            className={`order-button${orderSubmitted ? " done" : ""}`}
            onClick={() => setOrderSubmitted(true)}
          >
            {orderSubmitted ? "已下单" : "一键下单"}
          </button>
        </div>
      )}

      {shareCopied && <div className="share-toast">分享文案已复制，可以直接发给朋友。</div>}

      <div className="primary-row">
        {canConfirm && (
          <button className="confirm-button" onClick={onConfirm}>
            {hasRuntimePlan ? "确认新版行程" : "确认行程"}
          </button>
        )}
        {canShare && (
          <button className="confirm-button secondary" onClick={copyShare}>
            {shareCopied ? "已复制分享文案" : "一键分享给朋友"}
          </button>
        )}
        {canRefreshByRuntime && (
          <button className="subtle-replan-button" onClick={onRuntimeReplan}>
            按最新状态重新规划
          </button>
        )}
        {!confirmed && (
          <button className="subtle-replan-button" onClick={() => onModifyPrompt(buildMajorChangeDraft(plan))}>
            整体大改
          </button>
        )}
      </div>
    </article>
  );
}
