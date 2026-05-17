# FlowCity

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent 原型。

项目目标不是做普通聊天机器人，也不是固定场景推荐器，而是让用户用一句自然语言表达目标后，系统逐步完成：

```text
自然语言输入 -> 多约束拆解 -> 本地生活工具调用 -> 方案规划 -> 履约校验 -> 确认执行
```

当前进度：**阶段五：Validator 与局部重排**。

## 当前能力

阶段二已经完成需求结构化器，能把用户自然语言需求拆成稳定 JSON。阶段三已经加入本地 Mock 供给数据和函数版 Mock API，用于模拟活动、餐厅、路线、排队、预约和团购库存查询。阶段四已经加入规则约束下的 Planner：规则负责供给事实和边界校验，LLM 可在框架内自主组合时间轴方案；离线模式会使用确定性草案，保证测试和 Demo 稳定。阶段五已经加入 Validator 与 Local Replanner，用于校验时间、营业、预算、人群、余票、座位、排队和路线风险，并在失败时做一次局部替换。

已完成：

- `schema.json`：结构化需求 Schema，包含时间、人群、预算、位置、偏好、约束、潜在冲突等字段。
- `prompt.md`：大模型需求抽取 Prompt，强调不脑补、不生成 POI、不做路线规划。
- `examples.json`：8 套标准样例，覆盖亲子、情侣、朋友，以及多地点相聚、关系模糊、跨城、矛盾需求、定向活动。
- `extractor.py`：调用 DeepSeek OpenAI 兼容 API，把用户输入转成 JSON，并做 Schema 校验。
- `test_examples.py`：批量校验样例，不调用模型也能检查 Schema、阶段三兼容性和关键行为断言；可选 `--llm` 调用模型评测。
- `data/*.json`：西安本地生活 Mock 数据，包括商圈、活动、餐厅、路线、动态状态和团购。
- `mock_api.py`：函数版 Mock API，读取本地 JSON，完成硬约束过滤、软偏好打分、供给失败状态和路线成本挂载。
- `planner.py`：阶段四 Planner，基于结构化需求和 Mock 供给生成时间轴方案、推荐理由、预算估算和风险提示。
- `planner_prompt.md`：阶段四 Planner Prompt，约束 LLM 只能使用 Mock 供给内的候选，不编造 POI、价格、路线或执行结果。
- `validator.py`：阶段五 Validator 与 Local Replanner，校验阶段四方案是否可履约，并在失败时局部替换活动、餐厅或路线。
- `run_flow.py`：串联阶段二、阶段三、阶段四和阶段五，一条命令从自然语言输入跑到时间轴方案、校验结果和重排结果。
- `api.py`：可选 FastAPI 包装层，后续前端或 HTTP 工具调用时使用。

当前不做：

- 不接真实美团 API。
- 不做真实预约、排队、下单。
- 不做阶段六级别的真实订票、餐厅预约、线上取号、团购下单和订单号生成。

这些会在后续阶段推进。

## 文件结构

```text
Flowcity/
  .env.example      # 环境变量模板，不包含真实 Key
  .gitignore        # Git 忽略规则
  README.md         # 项目入口说明
  PROJECT.md        # 产品与技术说明
  TODO.md           # 后续任务清单
  schema.json       # 结构化需求 Schema
  prompt.md         # 需求抽取 Prompt 模板
  examples.json     # 标准样例和期望结构化输出
  extractor.py      # 阶段二需求结构化原型
  test_examples.py  # 阶段二批量测试脚本
  data/             # 阶段三 Mock 数据
  mock_api.py       # 阶段三函数版 Mock API
  planner.py        # 阶段四 AI 规划能力
  planner_prompt.md # 阶段四 Planner Prompt
  run_flow.py       # 阶段二 + 阶段三 + 阶段四串联脚本
  api.py            # 可选 FastAPI 包装层
```

## 配置环境变量

复制 `.env.example` 为 `.env`，并填写自己的 DeepSeek API Key。

```env
DEEPSEEK_API_KEY=你的真实key
FLOWCITY_LLM_MODEL=deepseek-v4-flash
FLOWCITY_LLM_BASE_URL=https://api.deepseek.com
FLOWCITY_LLM_JSON_OUTPUT=true
FLOWCITY_LLM_MAX_TOKENS=4096
```

注意：`.env` 包含真实 Key，不能提交到 Git，也不要截图或分享。

## 运行方式

进入项目目录：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity"
```

只查看最终 Prompt，不调用模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' extractor.py --input "周六下午2点到6点，带5岁孩子和老婆出去玩，预算400。" --dry-run
```

正式调用 DeepSeek：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' extractor.py --input "周六下午2点到6点，带5岁孩子和老婆出去玩，老婆在减肥，别太远，预算400。"
```

批量测试样例，不调用模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' test_examples.py
```

批量调用模型评测样例：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' test_examples.py --llm
```

完整链路试运行：自然语言 -> 阶段二结构化 -> 阶段三 Mock 供给查询 -> 阶段四时间轴方案。

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\run_flow.py --input "我们三个人，周天1到7点要去西安市区玩，怎么安排，我们在咸阳市区，得坐地铁去，预算一人100以内，三个男的" --limit 3
```

只测试阶段四，不调用大模型，使用离线确定性草案：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\planner.py --example-id xianyang_to_xian_city_trip --no-llm
```

使用阶段四 LLM Planner：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\run_flow.py --example-id family_half_day --planner-llm
```

只测试阶段三，不调用大模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\mock_api.py --example-id friends_citywalk --limit 3
```

阶段三关键能力样例：

```powershell
# 定向活动硬约束：没有滑雪供给时明确失败，不推荐展览替代
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\mock_api.py --example-id directed_skiing_activity --limit 3

# 小都市圈出行：咸阳到西安入城路线进入 routeCandidates 和候选成本
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\mock_api.py --example-id xianyang_to_xian_city_trip --limit 3

# 低成本语义：不想花钱优先免费/低消费，不再简单等于预算 0
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\mock_api.py --example-id contradictory_low_cost_not_tired --limit 3
```

## 阶段三本轮优化

本轮重点是让 Mock API 更像“确定性工具层”，而不是提前做阶段四 Planner：

- 定向硬约束：阶段三只读取结构化字段里的 `preferences.activityTypes` 和 `constraints.hard`。例如“就想滑雪”会被视为活动硬约束；当前供给池没有滑雪时，`activityCandidates` 为空，`supplyStatus.status` 为 `failed`。
- 供给失败状态：新增 `supplyStatus`，包含 `status`、`failedConstraints` 和 `reasons`，用于告诉后续 Planner 哪些硬约束在工具层失败。
- 小都市圈路线：`routeCandidates` 支持咸阳到西安主要商圈的 `cross_city_inbound` 入城路线，并带 `estimatedCostPerPerson`、`estimatedCostTotal`、`isCrossCityInbound`。
- 候选路线成本：活动和餐厅候选可带 `routeSummary`、`estimatedRouteCost`、`estimatedTotalCostWithRoute`，让“咸阳到西安”真的影响排序和预算判断。
- 低成本语义：不再把“不想花钱”粗暴转成预算 0，而是作为免费/低消费优先信号，避免把全部可行供给误杀。

## 阶段四 Planner 已跑通

阶段四的目标是只做 **AI 规划能力**：把阶段二的 `structuredDemand` 和阶段三的 `mockSupply` 交给 Planner，让 LLM 在规则边界内自主组合活动、餐饮、路线和缓冲时间，输出 `timelinePlan`。

阶段四固定边界：

- 规则层负责压缩候选、设定红线和轻量格式检查。
- LLM 负责组合活动、餐饮、路线、预算解释、风险提示和取舍说明。
- 如果阶段三 `supplyStatus.status == failed`，Planner 只能解释失败原因，不能强行推荐无关方案。
- 阶段四不做完整 Validator、不做执行前二次查询、不做 Replanner，这些留到阶段五。

阶段四输出包含：

```text
status
summary
timeline
selectedItems
budgetEstimate
recommendationReasons
riskTips
tradeoffs
rawPlannerNotes
```

本轮打磨后，Planner 额外强化了几类稳定性：

- 预算语义三档归一：
  - `不想花钱 / 少花钱 / 预算越低越好` 是低成本偏好，不等于严格预算 0。
  - `最好免费 / 优先免费 / 尽量免费` 是免费优先，低消费候选可作为备选。
  - `预算 0 元 / 零预算 / 一分钱都不能花 / 必须免费 / 只能免费` 是免费硬约束，会过滤所有收费候选。
- LLM 调用重试：需求抽取和 Planner JSON 解析失败时会有限重试。
- 引用校验：`selectedItems.poiId` 必须来自阶段三候选，`routeRef` 必须来自 `routeCandidates`。
- 预算校验：预算分项必须可加总，路线成本需要和选中路线保持一致。
- 跨城一致性：跨区域方案必须选真实路线，不能口头说“同商圈步行”。

真实 LLM 回归验证过 4 类预算表达：不想花钱、预算 0 只能免费、最好免费但可少花钱、预算越低越好。当前结果符合预期：低成本不会误杀供给，预算 0 会失败而不是硬凑收费方案。

## 当前技术选择

当前暂不使用 LangChain 或多 Agent 框架。原因是当前任务边界清楚：

```text
阶段二：自然语言 -> 结构化 JSON
阶段三：结构化 JSON -> Mock 工具查询
阶段四：结构化 JSON + Mock 供给 -> 规则约束下的 LLM 时间轴规划
```

直接调用模型 API 和本地确定性工具更清晰，也更适合学习、调试和比赛 Demo 快速推进。后续进入 Validator、Replanner、执行链路后，再评估是否引入 Agent 框架。
