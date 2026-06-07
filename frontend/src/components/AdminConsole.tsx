import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Copy,
  Database,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
  XCircle
} from "lucide-react";
import {
  createAdminRecord,
  deleteAdminRecord,
  fetchAdminCoverage,
  fetchAdminDatasets,
  fetchLearningAnalysis,
  fetchLearningProposals,
  reviewLearningProposal,
  saveAdminRecord
} from "../api/flowClient";

type AdminRecord = Record<string, unknown>;

type AdminCollection = {
  key: string;
  count: number;
  fields: string[];
  records: AdminRecord[];
};

type AdminDataset = {
  slug: string;
  label: string;
  filename: string;
  description: string;
  version?: string;
  city?: string;
  dataType?: string;
  note?: string;
  updatedAt?: string;
  collections: AdminCollection[];
};

type LearningCluster = {
  clusterKey?: string;
  examples?: string[];
  sessionCount?: number;
  confirmRate?: number;
  deleteRate?: number;
  semanticCohesion?: number;
  status?: string;
  groupingSource?: string;
};

type LearningProposalPayload = {
  proposalId?: string;
  clusterKey?: string;
  examples?: string[];
  metrics?: LearningCluster;
  status?: string;
};

type LearningProposalRow = {
  proposal_id?: string;
  status?: string;
  payload?: LearningProposalPayload;
};

type LearningAnalysis = {
  clusters?: LearningCluster[];
  eventCount?: number;
};

type CoverageArea = {
  areaId: string;
  areaName: string;
  activityCount: number;
  restaurantCount: number;
  fillerCount: number;
  subareaCount?: number;
  openAccessStatus?: string;
  activityPriceBuckets: Record<string, number>;
  restaurantPriceBuckets: Record<string, number>;
  gaps: string[];
};

type CoverageReport = {
  principles?: string[];
  kpis?: {
    areaCount: number;
    activityCount: number;
    restaurantCount: number;
    subareaCount?: number;
    openAccessCount?: number;
    poiCount: number;
    fillerCount: number;
    poiRuntimeTotal: number;
    poiRuntimeChanged: number;
    extensionRuntimeTotal: number;
    extensionRuntimeChanged: number;
  };
  governanceCoverage?: {
    total: number;
    sourceType: number;
    confidence: number;
    lastVerifiedAt: number;
    factTags: number;
    constraintTags: number;
  };
  areas?: CoverageArea[];
  runtimeStatus?: {
    total: number;
    abnormal: number;
    normal: number;
    abnormalRatio: number;
    targetAbnormalRatio: number;
    withinTolerance: boolean;
    scope?: string;
    activityRuntimeTotal?: number;
    restaurantRuntimeTotal?: number;
    extensionRuntimeTotal?: number;
    extensionRuntimeChanged?: number;
  };
};

const ADMIN_TOKEN_KEY = "flowcity.adminToken";

const SAMPLE_PROPOSAL: LearningProposalPayload = {
  proposalId: "demo_open_hypothesis_low_walk_family",
  clusterKey: "低步行亲子半日",
  status: "demo_sample",
  examples: [
    "带 5 岁孩子和家人玩半天，希望别太远、少走路、能按正常饭点吃。",
    "亲子出行里多次出现低步行、可坐下、餐厅清淡这些共同偏好。",
    "该候选通过运营审核后，才会进入下一轮召回权重。"
  ],
  metrics: {
    sessionCount: 7,
    confirmRate: 0.71,
    deleteRate: 0.08,
    semanticCohesion: 0.84,
    status: "demo_sample",
    groupingSource: "demo"
  }
};

function recordTitle(record: AdminRecord, index: number) {
  return String(
    record.name ??
      record.areaId ??
      record.id ??
      record.dealId ??
      record.poiId ??
      record.routeId ??
      `第 ${index + 1} 条`
  );
}

function recordSubtitle(record: AdminRecord) {
  return [
    record.category ? `类型 ${record.category}` : "",
    record.cuisine ? `菜系 ${record.cuisine}` : "",
    record.areaId ? `区域 ${record.areaId}` : "",
    record.avgPricePerPerson !== undefined ? `人均 ${record.avgPricePerPerson}` : "",
    record.pricePerPerson !== undefined ? `票价 ${record.pricePerPerson}` : "",
    record.queueMinutes !== undefined ? `排队 ${record.queueMinutes} 分` : ""
  ]
    .filter(Boolean)
    .join(" / ");
}

function getProposal(row: LearningProposalRow): LearningProposalPayload {
  return row.payload ?? { proposalId: row.proposal_id, status: row.status };
}

function formatFieldValue(value: unknown) {
  if (Array.isArray(value)) {
    return value.map((item) => (typeof item === "string" || typeof item === "number" ? String(item) : JSON.stringify(item))).join("\n");
  }
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value, null, 2);
  }
  return String(value ?? "");
}

function parseArrayField(text: string, originalValue: unknown[]) {
  const hasComplexItem = originalValue.some((item) => typeof item === "object" && item !== null);
  if (hasComplexItem) {
    return JSON.parse(text);
  }
  return text
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function AdminConsole() {
  const [token, setToken] = useState(() => window.localStorage.getItem(ADMIN_TOKEN_KEY) ?? "");
  const [datasets, setDatasets] = useState<AdminDataset[]>([]);
  const [coverage, setCoverage] = useState<CoverageReport>({});
  const [analysis, setAnalysis] = useState<LearningAnalysis>({});
  const [proposals, setProposals] = useState<LearningProposalRow[]>([]);
  const [selectedSlug, setSelectedSlug] = useState("");
  const [selectedCollection, setSelectedCollection] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [query, setQuery] = useState("");
  const [editorText, setEditorText] = useState("{}");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const activeDataset = datasets.find((item) => item.slug === selectedSlug);
  const activeCollection = activeDataset?.collections.find((item) => item.key === selectedCollection);
  const records = activeCollection?.records ?? [];
  const activeRecord = records[selectedIndex];
  const parsedEditor = useMemo(() => {
    try {
      return { record: JSON.parse(editorText) as AdminRecord, error: "" };
    } catch {
      return { record: null, error: "JSON 暂时不合法，字段表单会等你修好后再同步。" };
    }
  }, [editorText]);

  const filteredRecords = useMemo(
    () =>
      records
        .map((record, index) => ({ record, index }))
        .filter(({ record }) => JSON.stringify(record).toLowerCase().includes(query.trim().toLowerCase())),
    [records, query]
  );

  function persistToken(next: string) {
    setToken(next);
    window.localStorage.setItem(ADMIN_TOKEN_KEY, next);
  }

  async function loadAdminData(activeToken = token) {
    if (!activeToken.trim()) {
      setError("先输入后端 FLOWCITY_ADMIN_TOKEN。");
      return;
    }
    setIsLoading(true);
    setError("");
    try {
      const [datasetPayload, coveragePayload, learningPayload, proposalPayload] = await Promise.all([
        fetchAdminDatasets(activeToken),
        fetchAdminCoverage(activeToken),
        fetchLearningAnalysis(activeToken),
        fetchLearningProposals(activeToken)
      ]);
      const nextDatasets = datasetPayload.datasets as AdminDataset[];
      setDatasets(nextDatasets);
      setCoverage(coveragePayload as CoverageReport);
      setAnalysis(learningPayload as LearningAnalysis);
      setProposals((proposalPayload.proposals ?? []) as LearningProposalRow[]);
      const nextDataset = nextDatasets.find((item) => item.slug === selectedSlug) ?? nextDatasets[0];
      const nextCollection =
        nextDataset?.collections.find((item) => item.key === selectedCollection) ?? nextDataset?.collections[0];
      setSelectedSlug(nextDataset?.slug ?? "");
      setSelectedCollection(nextCollection?.key ?? "");
      setSelectedIndex(0);
      setMessage("管理台已刷新。");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "管理台加载失败");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    if (activeRecord) {
      setEditorText(JSON.stringify(activeRecord, null, 2));
    } else {
      setEditorText("{}");
    }
  }, [activeRecord]);

  useEffect(() => {
    if (token) {
      loadAdminData(token);
    }
    // Only auto-load once; manual refresh keeps later intent explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSaveRecord() {
    if (!activeDataset || !activeCollection) return;
    try {
      const record = JSON.parse(editorText) as AdminRecord;
      const payload = await saveAdminRecord(token, activeDataset.slug, activeCollection.key, selectedIndex, record);
      setDatasets((items) => items.map((item) => (item.slug === activeDataset.slug ? payload.dataset : item)));
      setMessage("记录已保存。");
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "保存失败");
    }
  }

  async function handleCloneRecord() {
    if (!activeDataset || !activeCollection || !activeRecord) return;
    const clone = { ...activeRecord, id: `${recordTitle(activeRecord, selectedIndex)}_copy_${Date.now()}` };
    try {
      const payload = await createAdminRecord(token, activeDataset.slug, activeCollection.key, clone);
      setDatasets((items) => items.map((item) => (item.slug === activeDataset.slug ? payload.dataset : item)));
      setSelectedIndex(Number(payload.index ?? 0));
      setMessage("已复制为新记录。");
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "复制失败");
    }
  }

  async function handleDeleteRecord() {
    if (!activeDataset || !activeCollection || !activeRecord) return;
    const ok = window.confirm(`确认删除「${recordTitle(activeRecord, selectedIndex)}」吗？`);
    if (!ok) return;
    try {
      const payload = await deleteAdminRecord(token, activeDataset.slug, activeCollection.key, selectedIndex);
      setDatasets((items) => items.map((item) => (item.slug === activeDataset.slug ? payload.dataset : item)));
      setSelectedIndex(0);
      setMessage("记录已删除。");
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "删除失败");
    }
  }

  async function handleReviewProposal(proposalId: string, status: "approved" | "rejected") {
    try {
      await reviewLearningProposal(token, proposalId, status);
      await loadAdminData(token);
      setMessage(status === "approved" ? "画像候选已批准。" : "画像候选已拒绝。");
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "审核失败");
    }
  }

  function updateEditorField(key: string, value: unknown) {
    const base = parsedEditor.record ?? activeRecord ?? {};
    setEditorText(JSON.stringify({ ...base, [key]: value }, null, 2));
  }

  return (
    <main className="admin-page">
      <section className="admin-shell">
        <header className="admin-topbar">
          <div>
            <span className="admin-eyebrow">
              <ShieldCheck size={15} /> FlowCity 后台
            </span>
            <h1>运营数据管理台</h1>
            <p>统一查看 POI 供给健康、动态状态、路线与画像候选，支持运营审核和数据修正。</p>
          </div>
          <a className="admin-back" href="#">
            返回用户端
          </a>
        </header>

        <div className="admin-auth">
          <label>
            管理员 Token
            <input
              value={token}
              onChange={(event) => persistToken(event.target.value)}
              placeholder="输入管理员访问 Token"
              type="password"
            />
            <span className="admin-token-hint">访问 Token 由后端环境变量配置。</span>
          </label>
          <button type="button" onClick={() => loadAdminData()} disabled={isLoading}>
            <RefreshCw size={15} /> 刷新后台数据
          </button>
        </div>

        {(message || error) && (
          <div className={`admin-toast ${error ? "error" : ""}`}>{error || message}</div>
        )}

        <section className="admin-grid">
          <div className="admin-panel supply-panel">
            <div className="admin-section-title">
              <Database size={17} />
              <div>
                <strong>POI / Mock 数据</strong>
                <span>当前按 FlowCity 新字段直接读取 data/*.json</span>
              </div>
            </div>

            <div className="coverage-panel">
              {coverage.kpis && (
                <div className="admin-kpi-grid">
                  <article>
                    <span>总 POI</span>
                    <strong>{coverage.kpis.poiCount}</strong>
                    <em>活动 {coverage.kpis.activityCount} / 餐厅 {coverage.kpis.restaurantCount}</em>
                  </article>
                  <article>
                    <span>正式商圈</span>
                    <strong>{coverage.kpis.areaCount}</strong>
                    <em>补位点 {coverage.kpis.fillerCount}</em>
                  </article>
                  <article>
                    <span>开放二级商圈</span>
                    <strong>{coverage.kpis.openAccessCount ?? 0}</strong>
                    <em>不按座位/余票库存校验</em>
                  </article>
                  <article>
                    <span>POI 影子表</span>
                    <strong>
                      {coverage.kpis.poiRuntimeChanged}/{coverage.kpis.poiRuntimeTotal}
                    </strong>
                    <em>一对一动态校验</em>
                  </article>
                  <article>
                    <span>扩展动态</span>
                    <strong>
                      {coverage.kpis.extensionRuntimeChanged}/{coverage.kpis.extensionRuntimeTotal}
                    </strong>
                    <em>路线/团购券另算</em>
                  </article>
                </div>
              )}
              <div className="coverage-principles">
                {(coverage.principles ?? []).slice(0, 6).map((principle) => (
                  <span key={principle}>{principle}</span>
                ))}
              </div>
              {coverage.runtimeStatus && (
                <div className={`runtime-ratio ${coverage.runtimeStatus.withinTolerance ? "ok" : "warn"}`}>
                  <strong>{Math.round(coverage.runtimeStatus.abnormalRatio * 100)}%</strong>
                  <span>
                    POI 运行时影子表，目标 {Math.round(coverage.runtimeStatus.targetAbnormalRatio * 100)}%，
                    {coverage.runtimeStatus.abnormal}/{coverage.runtimeStatus.total} 个 POI 变化
                  </span>
                  <em>
                    活动 {coverage.runtimeStatus.activityRuntimeTotal ?? "-"} / 餐厅{" "}
                    {coverage.runtimeStatus.restaurantRuntimeTotal ?? "-"}；路线/券扩展状态{" "}
                    {coverage.runtimeStatus.extensionRuntimeChanged ?? "-"}/
                    {coverage.runtimeStatus.extensionRuntimeTotal ?? "-"} 条另算
                  </em>
                </div>
              )}
              {coverage.governanceCoverage && (
                <div className="runtime-ratio ok">
                  <strong>{coverage.governanceCoverage.factTags}/{coverage.governanceCoverage.total}</strong>
                  <span>POI 治理派生覆盖：来源、置信度、事实标签和约束标签在加载时统一补齐</span>
                  <em>
                    source {coverage.governanceCoverage.sourceType} / confidence {coverage.governanceCoverage.confidence} /
                    constraints {coverage.governanceCoverage.constraintTags}
                  </em>
                </div>
              )}
              <div className="coverage-grid">
                {(coverage.areas ?? []).map((area) => (
                  <article className={area.gaps.length ? "coverage-card warn" : "coverage-card"} key={area.areaId}>
                    <div>
                      <strong>{area.areaName}</strong>
                      <span>{area.areaId}</span>
                    </div>
                    <p>
                      活动 {area.activityCount} / 餐饮 {area.restaurantCount} / 补位 {area.fillerCount} / 开放二级{" "}
                      {area.subareaCount ?? 0}
                    </p>
                    <p>
                      活动价层 F{area.activityPriceBuckets.free ?? 0} L{area.activityPriceBuckets.low ?? 0} M
                      {area.activityPriceBuckets.mid ?? 0} H{area.activityPriceBuckets.high ?? 0}
                    </p>
                    <p>
                      餐饮价层 L{area.restaurantPriceBuckets.low ?? 0} M{area.restaurantPriceBuckets.mid ?? 0} H
                      {area.restaurantPriceBuckets.high ?? 0}
                    </p>
                    {area.gaps.length > 0 && <em>{area.gaps.join("、")}</em>}
                  </article>
                ))}
              </div>
            </div>

            <div className="admin-dataset-tabs">
              {datasets.map((dataset) => {
                const count = dataset.collections.reduce((sum, collection) => sum + collection.count, 0);
                return (
                  <button
                    key={dataset.slug}
                    type="button"
                    className={dataset.slug === selectedSlug ? "active" : ""}
                    onClick={() => {
                      setSelectedSlug(dataset.slug);
                      setSelectedCollection(dataset.collections[0]?.key ?? "");
                      setSelectedIndex(0);
                    }}
                  >
                    <strong>{dataset.label}</strong>
                    <span>{count} 条</span>
                  </button>
                );
              })}
            </div>

            {activeDataset && (
              <div className="admin-dataset-body">
                <div className="dataset-meta">
                  <strong>{activeDataset.filename}</strong>
                  <span>{activeDataset.description}</span>
                </div>
                <div className="dataset-summary-grid">
                  <article>
                    <span>文件</span>
                    <strong>{activeDataset.filename}</strong>
                  </article>
                  <article>
                    <span>类型</span>
                    <strong>{activeDataset.dataType ?? "-"}</strong>
                  </article>
                  <article>
                    <span>城市</span>
                    <strong>{activeDataset.city ?? "-"}</strong>
                  </article>
                  <article>
                    <span>更新时间</span>
                    <strong>{activeDataset.updatedAt ?? "-"}</strong>
                  </article>
                </div>
                {activeDataset.note && <div className="dataset-note">{activeDataset.note}</div>}
                <div className="collection-tabs">
                  {activeDataset.collections.map((collection) => (
                    <button
                      key={collection.key}
                      type="button"
                      className={collection.key === selectedCollection ? "active" : ""}
                      onClick={() => {
                        setSelectedCollection(collection.key);
                        setSelectedIndex(0);
                      }}
                    >
                      {collection.key} ({collection.count})
                    </button>
                  ))}
                </div>
                <input
                  className="admin-search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索名称、标签、商圈、菜系"
                />
                {activeCollection && (
                  <div className="field-cloud">
                    {activeCollection.fields.map((field) => (
                      <span key={field}>{field}</span>
                    ))}
                  </div>
                )}
                <div className="record-editor-grid">
                  <div className="record-list">
                    {filteredRecords.map(({ record, index }) => (
                      <button
                        key={`${activeDataset.slug}-${selectedCollection}-${index}`}
                        type="button"
                        className={index === selectedIndex ? "active" : ""}
                        onClick={() => setSelectedIndex(index)}
                      >
                        <strong>{recordTitle(record, index)}</strong>
                        <span>{recordSubtitle(record) || `第 ${index + 1} 条记录`}</span>
                      </button>
                    ))}
                  </div>
                  <div className="json-editor">
                    <div className="json-editor-toolbar">
                      <span>{activeRecord ? recordTitle(activeRecord, selectedIndex) : "未选择记录"}</span>
                      <div>
                        <button type="button" onClick={handleCloneRecord} disabled={!activeRecord}>
                          <Copy size={14} /> 复制
                        </button>
                        <button type="button" onClick={handleDeleteRecord} disabled={!activeRecord}>
                          <Trash2 size={14} /> 删除
                        </button>
                        <button type="button" className="primary" onClick={handleSaveRecord} disabled={!activeRecord}>
                          <Save size={14} /> 保存
                        </button>
                      </div>
                    </div>
                    {parsedEditor.error && <div className="field-editor-warning">{parsedEditor.error}</div>}
                    {parsedEditor.record && (
                      <div className="field-editor">
                        <div className="field-editor-head">
                          <strong>字段表单</strong>
                          <span>常用字段可直接改，复杂对象仍可在下方 JSON 精修</span>
                        </div>
                        <div className="field-grid">
                          {Object.entries(parsedEditor.record).map(([key, value]) => {
                            const isArray = Array.isArray(value);
                            const isObject = typeof value === "object" && value !== null && !isArray;
                            return (
                              <label className={isArray || isObject ? "field-row wide" : "field-row"} key={key}>
                                <span>{key}</span>
                                {typeof value === "boolean" ? (
                                  <select
                                    value={value ? "true" : "false"}
                                    onChange={(event) => updateEditorField(key, event.target.value === "true")}
                                  >
                                    <option value="true">true</option>
                                    <option value="false">false</option>
                                  </select>
                                ) : typeof value === "number" ? (
                                  <input
                                    type="number"
                                    value={Number.isFinite(value) ? value : 0}
                                    onChange={(event) => updateEditorField(key, Number(event.target.value))}
                                  />
                                ) : isArray ? (
                                  <textarea
                                    defaultValue={formatFieldValue(value)}
                                    key={`${selectedSlug}-${selectedCollection}-${selectedIndex}-${key}`}
                                    onBlur={(event) => {
                                      try {
                                        updateEditorField(key, parseArrayField(event.target.value, value));
                                        setError("");
                                      } catch {
                                        setError(`${key} 不是合法 JSON 数组。`);
                                      }
                                    }}
                                  />
                                ) : isObject ? (
                                  <textarea
                                    defaultValue={formatFieldValue(value)}
                                    key={`${selectedSlug}-${selectedCollection}-${selectedIndex}-${key}`}
                                    onBlur={(event) => {
                                      try {
                                        updateEditorField(key, JSON.parse(event.target.value));
                                        setError("");
                                      } catch {
                                        setError(`${key} 不是合法 JSON 对象。`);
                                      }
                                    }}
                                  />
                                ) : (
                                  <input
                                    value={formatFieldValue(value)}
                                    onChange={(event) => updateEditorField(key, event.target.value)}
                                  />
                                )}
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    )}
                    <textarea
                      value={editorText}
                      onChange={(event) => setEditorText(event.target.value)}
                      spellCheck={false}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="admin-panel learning-panel">
            <div className="admin-section-title">
              <Sparkles size={17} />
              <div>
                <strong>自进化审核队列</strong>
                <span>稳定聚类先进入候选池，人工批准后才影响召回和排序</span>
              </div>
            </div>
            <div className="learning-summary">
              <div>
                <strong>{analysis.eventCount ?? 0}</strong>
                <span>学习事件</span>
              </div>
              <div>
                <strong>{analysis.clusters?.length ?? 0}</strong>
                <span>开放假设聚类</span>
              </div>
              <div>
                <strong>{proposals.length}</strong>
                <span>待/已审核候选</span>
              </div>
            </div>

            <div className="learning-proof">
              <strong>审核规则</strong>
              <ol>
                <li>用户修改、删除、确认会沉淀为匿名学习事件。</li>
                <li>相似开放假设会先聚成候选画像，不直接污染正式画像库。</li>
                <li>候选通过人工审核后，才参与后续召回和适配打分。</li>
                <li>负向样本、低确认率和分歧样本会阻断误晋升。</li>
              </ol>
            </div>

            <div className="proposal-list">
              {proposals.length === 0 && (
                <p className="empty-hint">当前暂无线上候选，下面保留一条审核样例用于展示候选字段和操作状态。</p>
              )}
              {(proposals.length > 0 ? proposals.map(getProposal) : [SAMPLE_PROPOSAL]).map((proposal) => {
                const metrics: LearningCluster = proposal.metrics ?? {};
                const proposalId = String(proposal.proposalId ?? "");
                const status = String(proposal.status ?? "pending_review");
                const isSample = status === "demo_sample";
                return (
                  <article className={`proposal-card${isSample ? " sample" : ""}`} key={proposalId}>
                    <div className="proposal-heading">
                      <div>
                        <strong>{proposal.clusterKey ?? "未命名画像候选"}</strong>
                        <span>{isSample ? "审核样例" : status}</span>
                      </div>
                      <div className="proposal-actions">
                        <button
                          type="button"
                          onClick={() => handleReviewProposal(proposalId, "approved")}
                          disabled={!proposalId || isSample}
                        >
                          <CheckCircle2 size={14} /> 批准
                        </button>
                        <button
                          type="button"
                          onClick={() => handleReviewProposal(proposalId, "rejected")}
                          disabled={!proposalId || isSample}
                        >
                          <XCircle size={14} /> 拒绝
                        </button>
                      </div>
                    </div>
                    <div className="proposal-metrics">
                      <span>会话 {metrics.sessionCount ?? 0}</span>
                      <span>确认率 {Math.round(Number(metrics.confirmRate ?? 0) * 100)}%</span>
                      <span>删除率 {Math.round(Number(metrics.deleteRate ?? 0) * 100)}%</span>
                      <span>凝聚度 {Number(metrics.semanticCohesion ?? 0).toFixed(2)}</span>
                    </div>
                    <ul>
                      {(proposal.examples ?? []).slice(0, 3).map((example) => (
                        <li key={example}>{example}</li>
                      ))}
                    </ul>
                  </article>
                );
              })}
            </div>

            <div className="cluster-list">
              {(analysis.clusters ?? []).slice(0, 8).map((cluster) => (
                <article className="cluster-card" key={cluster.clusterKey ?? cluster.examples?.[0] ?? "cluster"}>
                  <strong>{cluster.clusterKey ?? "未命名聚类"}</strong>
                  <span>
                    {cluster.status} / {cluster.groupingSource}
                  </span>
                  <p>{cluster.examples?.[0] ?? "暂无样例"}</p>
                </article>
              ))}
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}
