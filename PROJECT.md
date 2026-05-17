# FlowCity 项目说明

## 1. 产品定位

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent。

它区别于传统搜索推荐：不是让用户自己在大量商家和活动中筛选，而是让用户用一句自然语言表达目标后，系统自动拆解时间、预算、距离、人群、饮食、排队、预约等多重约束，并最终生成可履约、可执行的出行方案。

一句话定位：

```text
FlowCity = 开放式需求理解 + 多约束拆解 + 本地生活工具调用 + 履约校验 + 确认执行闭环
```

## 2. 阶段一结论

阶段一已经明确：

- 三个场景不是产品能力边界，只是 Demo 验证样例。
- 产品核心是开放式需求理解和个性化多约束拆解。
- 美团业务链路应覆盖用户需求、商家供给、平台匹配、交易转化和履约执行。
- 技术路线是 LLM 负责理解和规划，规则与工具负责事实查询和履约校验。

## 3. 阶段二目标与优化

阶段二完成 Constraint Extractor，也就是需求结构化器。

输入：

```text
用户自然语言需求
```

输出：

```text
符合 schema.json 的结构化 JSON
```

阶段二重点回答：

- 用户说了什么？
- 里面包含哪些时间、人群、预算、地点和偏好？
- 哪些是硬约束？
- 哪些是软偏好？
- 有哪些潜在冲突？
- 后续 Planner 应该输出什么类型的方案？

本轮阶段二已针对真实复杂需求做过优化：

- 关系模糊：例如“喜欢的女生”不直接判定为稳定情侣关系，而是标记为 `pursuing` 或 `ambiguous`。
- 多地点相聚：记录多人不同出发地，后续由 Planner 选择折中集合点。
- 城市群/跨城：识别咸阳到西安等跨城意图，但不擅自生成路线。
- 矛盾需求：例如“不想花钱但不想累”，写入 `potentialConflicts`。
- 定向活动：例如“就想滑雪/酒吧”，进入硬约束，而不是泛化成普通推荐。
- 防止脑补：不把用户没说的“少排队”“citywalk”“氛围好”等自动写入偏好。

阶段二不做：

- 不做 Mock API。
- 不做 POI 数据。
- 不做真实路线。
- 不做预约或下单。
- 不做完整履约校验。

## 4. 当前模块说明

### schema.json

定义大模型输出的结构化需求格式。它既给大模型参考，也给后端程序做校验。

关键字段：

- `rawInput`：用户原话。
- `scene`：场景判断。
- `timeWindow`：时间窗口。
- `people`：同行人和关系。
- `budget`：预算。
- `location`：出发地、区域、距离偏好、多出发地、跨城意图。
- `preferences`：活动、餐饮、体验偏好。
- `constraints`：硬约束、软偏好、动态约束。
- `potentialConflicts`：潜在冲突。
- `expectedOutput`：后续方案应包含什么。

### prompt.md

定义给大模型的抽取指令。它告诉模型：

- 只做需求理解，不做规划。
- 只输出 JSON，不输出解释。
- 不编造用户没说的信息。
- 按 `schema.json` 的字段输出。

### examples.json

存放 8 套标准样例：

- 亲子半日游。
- 情侣约会。
- 朋友小聚。
- 多大学相聚。
- 喜欢的女生但关系未定。
- 咸阳到西安跨城出行。
- 矛盾需求：不花钱但不想累。
- 定向活动：一定要滑雪。

它有两个作用：

- 给大模型做 few-shot 示例。
- 给开发者做测试基准。

### extractor.py

最小需求结构化原型。

流程：

```text
读取 prompt.md
读取 schema.json
读取 examples.json
拼接最终 Prompt
调用 DeepSeek API
解析 JSON
按 schema.json 校验
打印结果
```

### test_examples.py

阶段二批量测试脚本。

默认不调用大模型，检查：

- 8 个 examples 是否符合 `schema.json`。
- 8 个 examples 是否能进入阶段三 `mock_api.search_supply`。
- 阶段三关键行为是否符合业务约束，例如定向滑雪不返回展览、咸阳入城路线必须带成本、不想花钱不能被预算 0 误杀。

可选 `--llm` 调用 DeepSeek 批量评测模型输出质量。

## 5. 当前模型配置

当前默认使用 DeepSeek：

```text
model: deepseek-v4-flash
base_url: https://api.deepseek.com
JSON Output: enabled
```

真实 API Key 放在 `.env` 中，不进入 Git。

## 6. 后续阶段衔接

阶段二输出的结构化 JSON 会作为阶段三输入。

阶段三已经基于这些字段实现第一版函数式 Mock API：

- `data/mock_areas.json`：西安商圈数据。
- `data/mock_activities.json`：活动 POI。
- `data/mock_restaurants.json`：餐厅 POI。
- `data/mock_routes.json`：路线/通勤时间。
- `data/mock_availability.json`：排队、余票、座位和预约状态。
- `data/mock_deals.json`：团购/套餐库存。
- `mock_api.py`：读取 Mock 数据，完成硬约束过滤、软偏好打分、供给失败状态和路线成本挂载。
- `run_flow.py`：串联阶段二和阶段三，支持自然语言输入后直接查询 Mock 供给。

也就是说：

```text
阶段二：把用户需求拆清楚
阶段三：用本地 Mock 数据和工具响应这些需求
```

阶段三当前不调用大模型。它是确定性工具层，模拟本地生活平台的供给查询能力。

本轮阶段三优化明确了工具层边界：

- 不让 `mock_api.py` 重新理解自然语言。它只读取阶段二已经产出的结构化字段，例如 `preferences.activityTypes`、`constraints.hard`、`location.crossCityIntent`、`budget`。
- 定向活动属于硬约束。例如结构化结果中出现“滑雪”时，阶段三只召回滑雪相关供给；当前 Mock 数据没有滑雪，就返回硬约束失败，而不是推荐展览、手作或书房。
- 替代建议不在阶段三生成。阶段三只返回机器事实层的失败原因，后续阶段四 Planner 或阶段五 Replanner 再决定如何向用户解释和是否放宽约束。
- “不想花钱”不等于严格预算 0。阶段三把它处理为免费/低消费优先信号，保留低成本候选，避免误杀所有供给。
- “咸阳到西安”作为小都市圈周末出行能力进入路线成本。`mock_routes.json` 增加咸阳入城到西安主要商圈的 `cross_city_inbound` 路线，活动/餐厅候选会挂载 `routeSummary`、`estimatedRouteCost`、`estimatedTotalCostWithRoute`。

阶段三输出：

```text
activityCandidates
restaurantCandidates
routeCandidates
supplyStatus
filteredOut
toolLogs
```

其中 `filteredOut` 用于解释为什么某些活动或餐厅不可用，例如不适龄、超预算、无票、无座、排队过久或缺少目标日期动态状态。

`supplyStatus` 用于告诉后续 Planner 阶段三供给查询是否完整可用：

- `ok`：主要供给候选可用。
- `partial`：部分供给缺失，例如有活动没餐厅。
- `failed`：硬约束下没有可用供给，例如“必须滑雪”但 Mock 供给池没有滑雪。

## 7. 阶段四：规则约束下的 LLM Planner

阶段四已经完成第一版 AI 规划能力。它不提前做阶段五 Validator，也不做执行链路，而是专注回答一个问题：

```text
给定用户结构化需求和 Mock 供给事实，如何组合出一个合理的周末活动时间轴方案？
```

阶段四输入：

```text
structuredDemand + mockSupply
```

阶段四输出：

```text
timelinePlan
```

`timelinePlan` 固定包含：

- `status`：`ok | partial | failed`
- `summary`：一句话概览
- `timeline`：活动、餐饮、路线/通勤、缓冲时间
- `selectedItems`：被选中的活动、餐厅、路线候选
- `budgetEstimate`：活动、餐饮、路线、总价、人均
- `recommendationReasons`：为什么这样组合
- `riskTips`：排队、余票、座位、预算、通勤和供给不足风险
- `tradeoffs`：对矛盾需求的取舍说明
- `rawPlannerNotes`：可选调试摘要，不面向最终用户展示

阶段四的核心原则是：

```text
规则负责边界，LLM 负责规划。
```

规则层只做三件事：

- 压缩候选：把阶段三候选整理成 Planner 易读输入，避免上下文太长。
- 设定红线：不能编造 POI、价格、路线、库存或预约结果；硬约束失败时不能强行推荐无关方案。
- 轻量检查：字段齐全、`poiId` 来自 `mockSupply`、`routeRef` 来自 `routeCandidates`、预算和路线成本基本一致。

LLM 负责四件事：

- 在候选活动、餐厅和路线之间自主组合。
- 根据时间窗口排出合理时间轴。
- 解释预算、距离、排队、座位、余票等取舍。
- 面对冲突需求时明确偏向，例如省钱优先、轻松优先或体验优先。

## 8. 阶段四打磨记录

真实 LLM 测试后暴露出五类问题：

- API 连接偶发中断，例如 `WinError 10054`。
- Planner 输出 JSON 偶发截断或非法。
- “不想花钱/少花钱”偶尔被模型误抽成 `maxTotal: 0`。
- “最好免费/优先免费”和“预算 0/只能免费”容易在工具层被混成同一种低成本偏好。
- 跨城路线、预算和口头解释之间可能不一致。

对应修复：

- `extractor.py` 增加 LLM 调用重试，降低网络波动造成的测试中断。
- `planner.py` 在 JSON 解析失败时允许有限重试，并保留离线确定性草案。
- `prompt.md` 明确低成本语义：低成本是偏好，不等于预算 0；只有“预算 0 元/一分钱不能花/必须免费”才是严格 0。
- `normalize_structured_demand` 做兜底归一：如果模型把低成本误抽成 0，但用户没有明确零预算，就改回 `null + flexible`。
- `mock_api.py` 将预算语义拆成三档：
  - `low_cost_preferred`：不想花钱、少花钱、预算越低越好，只做低成本加权。
  - `free_preferred`：最好免费、优先免费、尽量免费，免费候选强加权，低消费可兜底。
  - `free_required`：预算 0 元、零预算、一分钱都不能花、必须免费、只能免费，硬过滤所有收费候选。
- `planner_prompt.md` 收紧事实边界，要求路线引用、金额、POI 都来自 Mock 供给。
- `validate_timeline_plan` 强化校验：POI 引用合法、路线引用合法、预算可加总、路线成本一致、跨区域必须有真实路线。

当前验证结果：

- `test_examples.py`：8 个基准样例离线通过。
- `py_compile`：核心脚本语法检查通过。
- 阶段四 CLI 链路已支持 `input -> structuredDemand -> mockSupply -> timelinePlan`。
- 真实 LLM 额外跑通 4 条预算语义用例：不想花钱、预算 0 只能免费、最好免费但可少花钱、预算越低越好。结果符合预期：低成本会保留低消费候选，免费硬约束无供给时输出 `failed`。

## 9. 阶段五：Validator 与局部重排

阶段五已经接入第一版 Validator 闭环。它的职责不是执行订单，而是把阶段四的推荐方案升级为“确认前可履约方案”。

阶段五输入：

```text
structuredDemand + mockSupply + timelinePlan
```

阶段五输出：

```text
validationResult + replanResult
```

`validationResult` 固定包含：

- `status`：`pass | warning | failed`
- `issues`：结构化失败或风险原因。
- `checkedDimensions`：已经检查的维度。
- `replanNeeded`：是否需要局部重排。
- `suggestedActions`：建议替换活动、餐厅、路线或低成本候选。

当前 Validator 覆盖：

- 时间窗口：检查时间轴是否可解析、是否超出用户时间。
- 营业时间：检查活动和餐厅是否在营业或可用时段内。
- 预算：重新计算活动、餐饮和路线总价。
- 人群适配：检查儿童年龄、亲子友好、低脂/清淡等约束。
- 动态供给：检查余票、座位和排队时间。
- 路线耗时：检查转场、跨城和少走路约束。

Local Replanner 当前只做一次局部替换：

- 活动失败时替换活动候选。
- 餐厅失败时替换餐厅候选。
- 路线失败时优先同商圈或更短路线。
- 预算失败时优先低成本或免费候选。

阶段五明确不做：

- 不订票。
- 不预约。
- 不下单。
- 不生成订单号。
- 不接真实美团 API。

## 10. 组合预算优化

阶段五接入后暴露出一个关键边界问题：阶段三按单个活动或单个餐厅过滤预算，但真实方案预算应该按整套组合计算。

例如：

```text
活动 294 元 + 餐饮 207 元 + 路线 75 元 = 576 元
```

如果用户总预算是 400 元，单看活动和单看餐厅都没超过 400 没有意义，因为整套方案已经超预算。

因此阶段四 Planner 已强化组合选择逻辑：

- 选择活动 + 餐厅 pair 时，直接计算 `活动费用 + 餐饮费用 + 路线费用`。
- 严格预算下，优先只选择组合总价不超预算的方案。
- 如果完全没有预算内组合，才选择超得最少的方案并交给阶段五解释。
- 跨商圈但没有可用路线的组合，不再参与候选比较。

这个调整后，亲子半日游样例会直接生成 324 元预算内方案，而不是先生成 576 元超预算方案再让 Validator 救火。

当前验证结果：

- `test_examples.py`：8 个基准样例离线通过。
- `py_compile`：核心脚本语法检查通过。
- `run_flow.py --example-id family_half_day --limit 3`：可输出预算内 `timelinePlan`、`validationResult` 和 `replanResult`。
