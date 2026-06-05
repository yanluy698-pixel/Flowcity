# FlowCity 项目说明

## 1. 产品定位

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent。

它的核心不是“给用户搜几个店”，而是把用户一句话里的隐性目标、硬边界、软偏好、时间预算、同行关系和履约风险拆清楚，再组合出能执行的时间轴。

```text
FlowCity = 开放式需求理解 + 语义画像桥 + 本地生活供给打分 + 时间轴调度 + 履约校验 + 交互式修改
```

## 2. 这次升级解决的问题

旧问题主要有三类：

- LLM 一步跨太大：既要理解“暧昧对象不尴尬”，又要直接决定具体 POI，结果不稳定。
- 后端容易写死：如果用 `if light_date then 避开面馆` 这类逻辑，场景一多就会变成无法维护的分支堆。
- 交互像机器：用户说“晚饭早一点”或“不安排活动”时，系统直接失败或硬重排，没有像人一样解释取舍。

本轮改成：

```text
LLM 负责共情抽取 -> 后端 taxonomy 补画像 -> 矩阵打分召回 -> Scheduler 确定性组合 -> 冲突时先引导确认
```

## 3. 语义画像桥

LLM 不再每次输出一大堆完整画像，也不直接决定去哪。它只负责输出较小的结构：

```text
socialIntent.primary
socialIntent.subScenario
explicitPreferredVibes
explicitAvoidVibes
confidence
evidence
```

后端的 `intent_taxonomy.py` 保存完整画像库：

- 每个 `primary/subScenario` 的默认 `preferredVibes`
- 默认 `avoidVibes`
- 画像权重
- 显式偏好 boost
- unknown/casual 的降级策略
- 中性 `general` 子场景和具体子场景证据词
- 标签作用域，例如 `清淡健康 -> restaurant`、`释放精力 -> activity`

这样做的好处是：

- Prompt 里只放短版 taxonomy，减少上下文膨胀。
- 完整画像留在代码配置里，稳定、可测试、可版本管理。
- 新增场景主要改 taxonomy 和数据标签，不需要在打分器里加业务 if。

### 3.1 防止“推荐结果反推用户意图”

系统严格区分三种来源：

- `用户明确偏好`：用户原话明确表达，可使用高权重软偏好。
- `画像辅助参考`：由有证据的 `primary/subScenario` 从 taxonomy 补全，只作为排序先验。
- `可执行依据`：候选自身事实，例如适龄、距离近、排队短、预算可行。

候选被选中不等于用户明确喜欢它。例如系统可以因为适龄、近、排队短推荐自然观察站，但不能因此声称用户隐含想体验自然观察。

### 3.2 具体子场景必须有证据

每个宽泛 `primary` 默认使用中性 `general`。具体子场景只有命中用户原话证据才激活：

```text
带孩子 -> family_care.kid_care
带孩子放放电 -> family_care.kid_energy_drain
和对象吃饭 -> light_date.general
第一次和对象见面 -> light_date.first_meet
```

即使 LLM 错选具体子场景，后端归一化也会检查证据并降级到安全默认。taxonomy 外且未进入显式偏好字段的 LLM 标签不会参与最终画像。

## 4. 显式偏好怎么处理

原则：用户原话优先于默认刻板画像，但不高于物理硬约束。

例如默认画像认为 `light_date.first_meet` 应避开“大排档/高噪/太市井”。但用户明确说：

```text
第一次和有好感的女生约会，她特别爱市井大排档烤肉。
```

系统会做排他合并：

- “市井大排档/烤肉”进入高权重 preferred。
- 与它冲突的默认 avoid 被剔除或削弱。
- 不会出现同一个标签既 preferred 又 avoid 的精神分裂。
- 仍然不会击穿营业、余票、座位、预算、儿童适龄等物理硬约束。

## 5. 矩阵打分怎么理解

这里的“矩阵”不是复杂数学库，而是把“用户画像标签”和“候选 POI 标签”放到同一张语义表里做交集加权。

候选总分：

```text
candidateScore = baseQualityScore + semanticScoreDelta + constraintFitScore + routeHintScore
```

字段含义：

- `baseQualityScore`：评分、排队短、可预约/有座、预算友好、商圈匹配。
- `semanticScoreDelta`：用户画像和 POI 标签的正负交集，例如“安静慢聊”命中加分，“高噪高动”踩雷扣分。
- `constraintFitScore`：明确约束匹配，例如儿童友好、清淡低脂、低体力、低成本、目标商圈。
- `routeHintScore`：轻量路线提示，例如同商圈少折腾、跨城路线成本、少走路。

核心点是：大排档本身不是坏标签。它只是在某些画像下可能是避雷，比如“初见轻约会”。如果用户是两个男生兄弟局，大排档可能反而命中“烟火气/轻松/兄弟局”，会加分。

语义交集还受标签作用域约束：餐饮标签只影响餐厅，活动标签只影响活动，通用氛围标签才允许同时影响两类候选。这样可以阻止“减脂 -> 清淡健康 -> 低压力活动”一类跨领域误匹配。

距离、预算、时间、交通、排队、预约等操作约束不进入 vibe 矩阵；它们分别由 `constraintFitScore`、`routeHintScore`、Scheduler 和 Validator 处理。即使 LLM 把“别太远/预算400”误写进显式 vibe，后端归一化也会将其移出语义画像。

语义矩阵只接受稳定标签注册表中的短 key。模型输出的“老婆减脂，需要清淡低脂”这类自由句子会保留在原始需求/evidence 中，但不会作为新标签进入集合交集；本地抽取会将其归一为 `清淡健康` 等稳定 key。

## 6. Unknown 为什么语义分为 0

如果用户只说：

```text
周六下午两人去高新吃个饭。
```

这种需求没有明显社交意图。此时如果强行补默认画像，比如“随便/放松”，会让有这些标签的店莫名上浮，反而污染结果。

所以策略是：

- `primary in {"unknown", "casual_meetup"}` 且无显式偏好时，不补默认 vibes。
- `semanticScoreDelta = 0`。
- Top-K 不再靠 JSON 文件顺序，而靠 `baseQualityScore/constraintFitScore/routeHintScore`。

这样语义不明确时，系统会退回到更可靠的本地生活基础因素：目标商圈、评分、预算、座位、排队、路线。

## 7. 多轮交互链路

前端时间轴卡片现在支持两种修改：

- 单节点修改：活动、餐厅、路线、过渡点分别有不同隐藏提示词。
- 整体大改：允许释放活动/餐厅/路线锁，重排整套方案。

用户点击卡片上的“修改”后，输入框上方会出现浅黄色修改上下文块。它不是直接替用户说话，而是把后端需要的结构化上下文一起发给 LLM：

```text
targetKind=restaurant/activity/route/filler/whole_plan
targetTitle
targetTimeRange
targetPoiId
allowedPatchKeys
用户补充
```

后端据此知道这不是普通闲聊，而是有目标的局部修改。

## 8. 冲突时怎么像人一样回复

当用户说“晚饭早一点”“早点回家”“这段路线别折腾”这类可能影响时间窗/营业/路线的需求时，系统不会立刻硬排或直接报错。

它会先进入引导确认：

```text
当前修改可能影响正餐时间和可预约餐厅。
4点多正餐选择会少一点，我建议：
1. 16:30 简餐/茶点垫一下
2. 保留原餐厅，提前到最早可约
3. 放宽到 17:30 正餐
```

这些选项不是泛泛聊天，每个选项都能转成后端补丁，例如：

```text
mealTiming=earlier
restaurantLock=keep/release
timeWindowEnd=null
budgetFlex=strict/flexible
```

## 9. 当前端到端流程

```text
1. 用户一句话输入
2. router 判断新规划/追问/解释/确认/局部修改
3. extractor 调 LLM 产出结构化需求
4. normalize_structured_demand 补齐字段并调用 taxonomy
5. mock_api 搜活动、餐厅、路线
6. Stage 3 综合分排序，修复 Top-K 漏斗
7. scheduler 组合活动 x 餐厅 x 路线，生成时间轴
8. validator 校验预算、营业、余票、座位、排队、路线
9. executor 生成执行草案
10. 前端展示时间轴卡片、理由 badge、修改入口
11. 用户继续追问时，带会话状态进入 refinement
```

## 10. 本轮新增和重点文件

- `intent_taxonomy.py`：语义画像库和显式偏好策略。
- `router.py`：多轮交互路由，识别局部修改、整体大改、解释、确认。
- `refinement.py`：会话内二次修改补丁，支持早饭/晚饭时间、少走路、只吃饭不活动等边界。
- `mock_api.py`：语义矩阵打分、基础质量分、约束分、路线提示分。
- `scheduler.py`：时间轴组合、早餐饮策略、跳过活动策略、失败人话建议。
- `backend/app/services/pipeline.py`：流式链路、LLM 修改器、引导确认、会话状态保存。
- `frontend/src/modifyIntents.ts`：前端点击修改时生成不同方向的隐藏上下文。
- `frontend/src/components/PlanCard.tsx`：时间轴卡片与节点修改入口。
- `frontend/src/components/ChatInput.tsx`：输入框中的修改上下文块。
- `frontend/src/components/StageProgress.tsx`：阶段处理中的 spinner。

## 11. 验证记录

本轮完成了四类验证：

```text
Python AST 检查：通过
test_examples.py：13 个离线样例通过
frontend npm run build：通过
10 组多轮真实 LLM 自测：PASSED=10/10
```

最终自测目录：

```text
D:\产品\美团\周末闲时活动规划\Flowcity_interaction_eval_runs\20260605_094648
```

10 组内容覆盖：

- 亲子早吃饭追问和少走路局部改。
- 轻约会餐厅换成自然不尴尬。
- 男生局室内活动和烟火气餐厅。
- 咸阳跨城路线提前返程。
- 低成本独处整体大改。
- 深聊场景避开书店、路线少折腾。
- casual unknown 转火锅局。
- 长辈下雨少走路、早吃饭。
- 亲子活动降噪和清淡餐厅。
- 不可能时间窗下，只吃饭不活动的人话确认。

速度验收使用赛题正式口径，而不是要求每一句聊天都在 30 秒内：

```text
方案生成/重排 <= 30 秒
本地 Mock 工具响应 <= 3 秒
每组完整多轮端到端流程 <= 2 分钟
```

为兼顾速度和抽取质量，默认首轮 Prompt 使用精简 Schema 和 3 个代表性 few-shot；完整 Schema、完整 examples 仍保留给本地校验，并可通过 `FLOWCITY_LLM_FULL_PROMPT=true` 恢复完整 Prompt 调试。

## 12. 仍然不做的事

- 不接真实美团 API。
- 不做真实预约、取号、订票、团购或支付。
- 不把最后推荐理由交给 LLM 润色，避免额外 2 到 4 秒串行延迟。
- 不把所有可能情况写死在后端 if 里；新增业务倾向优先改 taxonomy、数据标签和少量通用补丁。
