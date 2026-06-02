import { Check, LoaderCircle, Search, Sparkles, TriangleAlert } from "lucide-react";
import type { StageState } from "../types";

type Props = {
  stages: StageState[];
};

function StageIcon({ status }: { status: StageState["status"] }) {
  if (status === "done") return <Check size={14} />;
  if (status === "error") return <TriangleAlert size={14} />;
  if (status === "active") return <LoaderCircle size={14} className="spin" />;
  return <Search size={14} />;
}

function stageSummary(stage: StageState) {
  if (stage.status === "pending") return "等待上一步结果";
  if (stage.status === "active") return "正在处理真实链路";
  if (stage.status === "error") return "这里出了问题";
  if (stage.stage === "supply") {
    const count = stage.payload?.activityCount as number | undefined;
    const restaurants = stage.payload?.restaurantCount as number | undefined;
    return `找到 ${count ?? 0} 个活动、${restaurants ?? 0} 个餐厅候选`;
  }
  if (stage.stage === "plan") return "已组合活动、路线和用餐时间轴";
  if (stage.stage === "validate") return "已完成预算、余票、座位和路线校验";
  if (stage.stage === "execute_draft") return "已生成待确认执行草案";
  return "已完成拆解";
}

export function StageProgress({ stages }: Props) {
  return (
    <div className="stage-card">
      <div className="stage-title">
        <Sparkles size={15} />
        FlowCity 正在把事情做完
      </div>
      {stages.map((stage) => (
        <div className={`stage-row ${stage.status}`} key={stage.stage}>
          <span className="stage-dot">
            <StageIcon status={stage.status} />
          </span>
          <div>
            <strong>{stage.label}</strong>
            <p>{stageSummary(stage)}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
