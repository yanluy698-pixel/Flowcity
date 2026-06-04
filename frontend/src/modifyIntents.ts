import type { ModifyDraft, TimelineItem } from "./types";

type ModifyIntentConfig = {
  labelPrefix: string;
  suggestion: string;
  targetKind: string;
  allowedPatchKeys: string[];
  decisionOptions: string[];
  focus: string[];
  preserve: string[];
  promptGoal: string;
};

const MODIFY_INTENTS: Record<string, ModifyIntentConfig> = {
  restaurant: {
    labelPrefix: "修改吃饭",
    suggestion: "这个吃饭节点想调整一下，可以更早一点、清淡一点、少排队，或者换个同商圈更合适的选择。",
    targetKind: "restaurant",
    allowedPatchKeys: [
      "mealTiming",
      "restaurantLock",
      "foodPreference",
      "budgetFlex",
      "preferredArea",
      "avoidCuisine",
      "queueLimit"
    ],
    decisionOptions: [
      "保留原活动，只换/调餐厅",
      "保留原餐厅，只调到更早/更晚可约时段",
      "同商圈换更清淡/更便宜/少排队的店",
      "如果时间冲突，先问用户是否接受茶点/简餐替代正餐"
    ],
    focus: ["预约时段", "排队", "预算", "人群口味", "儿童/老人/减脂边界", "是否同商圈"],
    preserve: ["原活动", "总预算", "出发地", "孩子/老人/饮食硬边界"],
    promptGoal: "优先围绕餐饮节点局部修改，不要无故替换活动。"
  },
  activity: {
    labelPrefix: "修改活动",
    suggestion: "这个活动节点想调整一下，可以更适合孩子、轻松一点、室内一点，路线别更折腾。",
    targetKind: "activity",
    allowedPatchKeys: [
      "activityLock",
      "activityPreference",
      "physicalIntensityMax",
      "indoorRequired",
      "preferredArea",
      "budgetFlex",
      "routePreference"
    ],
    decisionOptions: [
      "保留餐厅，只换更合适的活动",
      "同商圈换低体力/亲子/室内活动",
      "如果余票或营业时间冲突，先问用户是否接受换商圈",
      "路线变长时，优先提醒用户取舍"
    ],
    focus: ["活动时段", "余票", "儿童/老人适配", "体力强度", "室内外", "转场路线"],
    preserve: ["吃饭安排", "总预算", "出发地", "明确避雷项"],
    promptGoal: "优先围绕活动节点局部修改，不要无故替换餐厅。"
  },
  filler: {
    labelPrefix: "修改过渡",
    suggestion: "这个过渡节点想调整一下，可以换成更舒服的休息点，少走路，也别影响后面的吃饭时间。",
    targetKind: "filler",
    allowedPatchKeys: ["fillerPreference", "preferredArea", "routePreference", "mealTiming", "budgetFlex"],
    decisionOptions: [
      "保留主活动和餐厅，只换休息/等位点",
      "同商圈找可坐下、低消费、少排队的地方",
      "如果时间不够，建议缩短过渡或直接去餐厅等位"
    ],
    focus: ["可坐下", "低消费", "距离餐厅近", "是否适合等位", "不影响后续预约"],
    preserve: ["主活动", "餐厅", "总预算", "时间窗"],
    promptGoal: "只优化过渡/等位/休息节点，尽量不改变主活动和餐厅。"
  },
  route: {
    labelPrefix: "修改路线",
    suggestion: "这段路线有点折腾，帮我换成更近、更稳，或者少转场的安排。",
    targetKind: "route",
    allowedPatchKeys: ["transportPreference", "routePreference", "preferredArea", "activityLock", "restaurantLock"],
    decisionOptions: [
      "优先保留 POI，只换交通方式",
      "如果路线仍然太长，改成同商圈活动和餐厅",
      "如果必须跨城/多出发点，先提示到达时间风险"
    ],
    focus: ["通勤时间", "交通方式", "转场次数", "跨城/多出发点公平", "是否能同商圈"],
    preserve: ["体验目标", "预算", "硬时间窗", "明确要去/不去的地点"],
    promptGoal: "优先优化路线和商圈布局，不要只换一个看起来分数高但更绕的 POI。"
  },
  generic: {
    labelPrefix: "修改节点",
    suggestion: "帮我优化这个时间段，尽量不影响其他安排。",
    targetKind: "selected_node",
    allowedPatchKeys: ["timePreference", "preferredArea", "budgetFlex", "routePreference"],
    decisionOptions: ["优先局部修改", "如果冲突明显，先问用户放宽哪一项"],
    focus: ["时间段", "预算", "路线", "上下游节点"],
    preserve: ["其它时间轴节点", "原始硬约束"],
    promptGoal: "围绕用户点击的单一节点做局部优化。"
  }
};

function intentKeyForItem(item: TimelineItem): keyof typeof MODIFY_INTENTS {
  if (item.type === "restaurant") return "restaurant";
  if (item.type === "activity") return "activity";
  if (item.type === "filler") return "filler";
  if (item.type === "route" || item.type === "multi_origin_route") return "route";
  return "generic";
}

function compactText(items: string[]) {
  return items.map((item) => `- ${item}`).join("\n");
}

export function buildNodeModifyDraft(item: TimelineItem): ModifyDraft {
  const config = MODIFY_INTENTS[intentKeyForItem(item)];
  const title = item.title ?? item.type ?? "这个节点";
  const timeRange = `${item.start ?? "待定"}-${item.end ?? "待定"}`;
  const poiId = item.poiId ?? "无";
  return {
    label: `${config.labelPrefix}：${title}`,
    suggestion: config.suggestion,
    systemPrompt: [
      "【节点修改上下文】",
      `targetKind=${config.targetKind}`,
      `targetTitle=${title}`,
      `targetTimeRange=${timeRange}`,
      `targetPoiId=${poiId}`,
      `allowedPatchKeys=${config.allowedPatchKeys.join(",")}`,
      "",
      "用户在前端时间轴中主动点击了这个节点的“修改”按钮，所以这是一个有目标的局部修改，不是普通闲聊。",
      config.promptGoal,
      "",
      "必须优先关注：",
      compactText(config.focus),
      "",
      "默认尽量保留：",
      compactText(config.preserve),
      "",
      "如果用户补充不够明确，追问必须给出可执行选项，例如：",
      compactText(config.decisionOptions),
      "",
      "请把用户补充映射到后端可执行补丁；如果能直接重排，就局部重排；如果会冲突，就像人一样解释原因并给选择。"
    ].join("\n")
  };
}

export function buildMajorChangeDraft(plan: Record<string, unknown>): ModifyDraft {
  const summary = String(plan.summary ?? "当前方案");
  return {
    label: "整体大改",
    suggestion: "我对整体安排不太满意，帮我换一个思路重新规划，重点优化体验和路线。",
    systemPrompt: [
      "【整体大改上下文】",
      "targetKind=whole_plan",
      "allowedPatchKeys=majorChange,preferredVibes,avoidVibes,preferredArea,budgetFlex,routePreference,activityPreference,foodPreference,mealTiming",
      `currentSummary=${summary}`,
      "",
      "用户在前端点击了“整体大改”，不是只修改单个节点。",
      "请允许重排活动、餐厅、路线和时间轴，但必须保留原始硬约束：人数、预算、时间窗、出发地、儿童/老人/饮食边界。",
      "如果用户补充很模糊，追问必须给出可执行选项，例如：更亲子、更轻松、更近、更省钱、更有氛围、少排队。",
      "如果能直接重排，不要被上一版 POI 锁死，优先给出更符合用户补充方向的新组合。"
    ].join("\n")
  };
}
