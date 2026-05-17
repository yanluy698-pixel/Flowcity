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

后续阶段四 Planner 将基于阶段三候选供给生成时间轴方案。
