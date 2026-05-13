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

## 3. 阶段二目标

阶段二只完成 Constraint Extractor，也就是需求结构化器。

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

阶段二不做：

- 不做 Mock API。
- 不做 POI 数据。
- 不做真实路线。
- 不做预约或下单。
- 不做完整履约校验。

## 4. 当前模块说明

### schema.json

定义大模型输出的结构化需求格式。

它既给大模型参考，也给后端程序做校验。

关键字段：

- `rawInput`：用户原话。
- `scene`：场景判断。
- `timeWindow`：时间窗口。
- `people`：同行人。
- `budget`：预算。
- `location`：出发地、区域、距离偏好。
- `preferences`：活动、餐饮、体验偏好。
- `constraints`：硬约束、软偏好、动态约束。
- `potentialConflicts`：潜在冲突。
- `expectedOutput`：后续方案应包含什么。

### prompt.md

定义给大模型的抽取指令。

它告诉模型：

- 只做需求理解，不做规划。
- 只输出 JSON，不输出解释。
- 不编造用户没说的信息。
- 按 `schema.json` 的字段输出。

### examples.json

存放 3 套标准样例：

- 亲子半日游。
- 情侣约会。
- 朋友小聚。

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
做基础校验
打印结果
```

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

阶段三将基于这些字段设计：

- Mock POI 数据。
- Mock 路线/通勤时间。
- Mock 排队状态。
- Mock 预约时段。
- Mock 团购库存。
- Mock 执行动作。

也就是说：

```text
阶段二：把用户需求拆清楚
阶段三：准备能响应这些需求的数据和工具
```

