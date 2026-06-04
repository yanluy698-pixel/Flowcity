import type { StageState, TimelineItem } from "../types";

type Props = {
  stages: StageState[];
  totalDurationMs?: number;
};

function seconds(ms?: number) {
  if (typeof ms !== "number") return "";
  return `${(ms / 1000).toFixed(1)} 秒`;
}

function textList(items: unknown[], limit = 4) {
  return items
    .slice(0, limit)
    .map((item: any) => item?.name ?? item?.title ?? item?.routeSummary ?? item?.routeRef)
    .filter(Boolean)
    .join("、");
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

function userText(value: string) {
  let text = value;
  for (const [from, to] of Object.entries(PLACE_LABELS)) {
    text = text.split(from).join(to);
  }
  return text
    .split("Mock ").join("")
    .split("Mock").join("")
    .split("后端").join("系统")
    .split("阶段三").join("候选查询")
    .split("阶段四").join("规划")
    .split("阶段五").join("确认前")
    .split("routeCandidates").join("路线")
    .split("mockSupply").join("候选")
    .trim();
}

function demandSummary(payload?: Record<string, unknown>) {
  const demand = payload?.structuredDemand as any;
  if (!demand) return "正在把自然语言拆成时间、人数、预算和偏好。";
  const people = demand.people?.total ? `${demand.people.total} 人` : "人数待定";
  const date = demand.timeWindow?.dateText ?? "时间待定";
  const time = [demand.timeWindow?.startTime, demand.timeWindow?.endTime].filter(Boolean).join("-");
  const budget = demand.budget?.maxTotal
    ? `总预算 ${demand.budget.maxTotal} 元`
    : demand.budget?.perPerson
      ? `人均 ${demand.budget.perPerson} 元`
      : "预算待定";
  const location = demand.location?.startPoint ?? demand.location?.preferredArea ?? "地点待定";
  const hard = (demand.constraints?.hard ?? [])
    .slice(0, 3)
    .map((item: string) => item.replace(/^避开用户明确不想去的地点或商圈：/, "避开 "))
    .join("；");
  return userText(`${people}，${date}${time ? ` ${time}` : ""}，${budget}，${location}${hard ? `。我会优先满足：${hard}` : ""}`);
}

function routerSummary(payload?: Record<string, unknown>) {
  const result = payload?.routerResult as any;
  if (!result) return "正在判断这是新规划、局部修改、解释方案还是确认执行。";
  const flags = result.actionFlags ?? {};
  const actions = [
    flags.needNewActivity ? "换活动" : "",
    flags.needNewRestaurant ? "换餐厅" : "",
    flags.modifyBudget ? "压预算" : "",
    flags.modifyDistance ? "少走路/更近" : "",
    flags.needRouteRefresh ? "重算路线" : "",
    flags.needExplanation ? "解释方案" : "",
    flags.confirmExecution ? "确认执行" : ""
  ].filter(Boolean);
  const locks = result.locks ?? {};
  const lockText = [
    locks.activityPoiId ? "保留原活动 POI" : "",
    locks.restaurantPoiId ? "保留原餐厅 POI" : ""
  ].filter(Boolean).join("，");
  const modeLabel: Record<string, string> = {
    new_plan: "全量新规划",
    refine: "局部微调",
    explain: "解释方案",
    confirm: "确认执行",
    clarify: "追问确认"
  };
  return userText(
    `识别为${modeLabel[result.mode] ?? result.mode}${actions.length ? `：${actions.join("、")}` : ""}${lockText ? `；${lockText}` : ""}。`
  );
}

function supplySummary(payload?: Record<string, unknown>) {
  const supply = payload?.mockSupply as any;
  if (!supply) return "正在找活动、吃饭地点、路线、余票、座位和排队情况。";
  const toolResults = (payload?.toolResults as any[]) ?? [];
  if (toolResults.length) {
    const activityTool = toolResults.find((item) => item.tool === "search_activities");
    const restaurantTool = toolResults.find((item) => item.tool === "search_restaurants");
    const routeTool = toolResults.find((item) => item.tool === "get_routes");
    return userText(
      `已同时查到 ${activityTool?.items?.length ?? 0} 个可玩活动、${restaurantTool?.items?.length ?? 0} 个吃饭地点、${routeTool?.items?.length ?? 0} 条路线。`
    );
  }
  const activities = textList(supply.activityCandidates ?? [], 8);
  const restaurants = textList(supply.restaurantCandidates ?? [], 8);
  const routeCount = supply.routeCandidates?.length ?? payload?.routeCount ?? 0;
  return userText(`可选活动：${activities || "暂无"}。可选吃饭地点：${restaurants || "暂无"}。可用路线 ${routeCount} 条。`);
}

function planSummary(payload?: Record<string, unknown>) {
  const plan = payload?.timelinePlan as any;
  if (!plan) return "正在组合活动、吃饭、路线和缓冲时间。";
  const scheduler = (payload?.schedulerResult as any) ?? plan.schedulerResult;
  if (scheduler?.strategy) {
    const evaluated = scheduler.evaluatedCombinationCount ?? 0;
    const feasible = scheduler.feasibleCombinationCount ?? 0;
    const rejected = scheduler.rejectedCombinations?.length ?? Math.max(0, evaluated - feasible);
    const selected = scheduler.selectedCombination;
    const picked = [selected?.activity, selected?.restaurant].filter(Boolean).join(" + ");
    return userText(
      `已用时间调度器评估 ${evaluated} 个组合，淘汰 ${rejected} 个不合适组合，选择 ${picked || "当前可执行方案"}。`
    );
  }
  const timeline = (plan.timeline ?? []) as TimelineItem[];
  const steps = timeline.map((item) => `${item.start ?? ""} ${item.title ?? item.type ?? "安排"}`);
  return userText(steps.slice(0, 5).join("；") || plan.summary || "已生成初版时间轴。");
}

function validationSummary(payload?: Record<string, unknown>) {
  if (!payload) return "正在看这套方案会不会超预算、排队太久、座位不稳或路线太赶。";
  const result = payload.validationResult as any;
  const issues = result?.issues ?? [];
  if (!issues.length) return "看起来可执行：预算、余票、座位、排队和路线都没有明显问题。";
  return userText(issues
    .slice(0, 3)
    .map((issue: any) => issue.message)
    .filter(Boolean)
    .join("；"));
}

function draftSummary(payload?: Record<string, unknown>) {
  const draft = payload?.executionDraft as any;
  if (!draft) return "正在整理确认前需要做的事，比如锁票、预约、取号和出发提醒。";
  const actions = draft.pendingActions ?? [];
  if (!actions.length) return userText(draft.blockedReason ?? "当前方案暂不能执行。");
  return userText(actions
    .slice(0, 5)
    .map((action: any) => action.title ?? action.actionType)
    .filter(Boolean)
    .join("；"));
}

function stageDetail(stage: StageState) {
  if (stage.stage === "router") return routerSummary(stage.payload);
  if (stage.stage === "extract") return demandSummary(stage.payload);
  if (stage.stage === "supply") return supplySummary(stage.payload);
  if (stage.stage === "plan") return planSummary(stage.payload);
  if (stage.stage === "validate") return validationSummary(stage.payload);
  if (stage.stage === "execute_draft") return draftSummary(stage.payload);
  return "等待上一步完成。";
}

function statusText(status: StageState["status"]) {
  if (status === "active") return "正在处理";
  if (status === "done") return "完成";
  if (status === "error") return "出错";
  return "等待";
}

export function StageProgress({ stages, totalDurationMs }: Props) {
  return (
    <div className="stage-card">
      <div className="stage-title">规划过程</div>
      {stages.map((stage, index) => (
        <div className={`stage-row ${stage.status}`} key={stage.stage}>
          <div className="stage-line">
            <strong>{index + 1}. {stage.label}</strong>
            <span>
              {stage.status === "active" && <i className="stage-spinner" aria-hidden="true" />}
              {statusText(stage.status)}
              {stage.durationMs ? ` · 用时 ${seconds(stage.durationMs)}` : ""}
            </span>
          </div>
          <p>{stageDetail(stage)}</p>
        </div>
      ))}
      {totalDurationMs && (
        <div className="total-time">总用时 {seconds(totalDurationMs)}</div>
      )}
    </div>
  );
}
