import { CalendarCheck, CircleDollarSign, Map, Share2, ShieldCheck } from "lucide-react";
import type { TimelineItem } from "../types";

type Props = {
  payload: Record<string, any>;
  onConfirm: () => void;
};

function finalPlan(payload: Record<string, any>) {
  const replan = payload.replanResult;
  if (replan?.success && replan.replannedTimelinePlan) {
    return replan.replannedTimelinePlan;
  }
  return payload.timelinePlan ?? {};
}

function money(value: unknown) {
  if (typeof value !== "number") return "¥0";
  return `¥${Math.round(value)}`;
}

export function PlanCard({ payload, onConfirm }: Props) {
  const plan = finalPlan(payload);
  const timeline = (plan.timeline ?? []) as TimelineItem[];
  const budget = plan.budgetEstimate ?? {};
  const issues = payload.validationResult?.issues ?? [];
  const draft = payload.executionDraft ?? {};
  const alternatives = [
    ...(draft.alternativeCandidates?.activities ?? []),
    ...(draft.alternativeCandidates?.restaurants ?? [])
  ].slice(0, 3);
  const executionResult = payload.executionResult;

  function sharePlan() {
    const summary = `${plan.summary ?? "FlowCity 方案"}\n${timeline
      .map((item) => `${item.start ?? ""}-${item.end ?? ""} ${item.title ?? ""}`)
      .join("\n")}`;
    navigator.clipboard?.writeText(summary);
  }

  return (
    <article className="plan-card">
      <div className="plan-head">
        <div>
          <span className="mini-label">推荐方案</span>
          <h2>{plan.summary ?? "已生成可执行周末计划"}</h2>
        </div>
        <div className="price-chip">
          <CircleDollarSign size={15} />
          {money(budget.totalCost)}
        </div>
      </div>

      <div className="budget-row">
        <span>活动 {money(budget.activityCost)}</span>
        <span>餐饮 {money(budget.restaurantCost)}</span>
        <span>路线 {money(budget.routeCost)}</span>
        <strong>人均 {money(budget.perPersonCost)}</strong>
      </div>

      <div className="timeline">
        {timeline.map((item, index) => (
          <div className="timeline-item" key={`${item.title}-${index}`}>
            <div className="time">
              <strong>{item.start ?? "--:--"}</strong>
              <span>{item.end ?? ""}</span>
            </div>
            <div className="line" />
            <div className="timeline-copy">
              <h3>{item.title ?? item.type ?? "安排"}</h3>
              <p>{item.description ?? item.routeRef ?? "FlowCity 已加入这一步。"}</p>
            </div>
          </div>
        ))}
      </div>

      <div className="route-card">
        <Map size={16} />
        <span>路线缩略：按时间轴自动串联活动、餐厅和转场提醒。</span>
      </div>

      {issues.length > 0 && (
        <div className="risk-card">
          <ShieldCheck size={16} />
          <span>{issues[0].message ?? "存在轻微履约风险，已带入执行草案。"}</span>
        </div>
      )}

      {alternatives.length > 0 && (
        <div className="alternatives">
          <span className="mini-label">可替代项目</span>
          {alternatives.map((item: any) => (
            <button key={item.poiId}>{item.name}</button>
          ))}
        </div>
      )}

      <div className="action-row">
        <button className="secondary-action">换更省钱</button>
        <button className="secondary-action">换少走路</button>
        <button className="secondary-action">换室内</button>
      </div>

      <div className="primary-row">
        <button className="share-button" onClick={sharePlan}>
          <Share2 size={16} />
          一键分享
        </button>
        <button className="confirm-button" onClick={onConfirm}>
          <CalendarCheck size={16} />
          确认执行
        </button>
      </div>

      {executionResult?.executionStatus === "confirmed" && (
        <div className="execution-done">
          已生成 Mock 执行结果：{executionResult.confirmationCodes?.length ?? 0} 个确认码
        </div>
      )}
    </article>
  );
}
