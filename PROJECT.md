# FlowCity 项目说明

## 1. 产品定位

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent。

它的核心不是“给用户搜几个店”，而是把用户一句话里的隐性目标、硬边界、软偏好、时间预算、同行关系和履约风险拆清楚，再组合出能执行的时间轴。

```text
FlowCity = 开放式需求理解 + 语义画像桥 + 出行语义策略 + 本地生活供给打分 + 多节点时间窗调度 + 履约校验 + 交互式修改
```

## 2. 这次升级解决的问题

旧问题主要有三类：

- LLM 一步跨太大：既要理解“暧昧对象不尴尬”，又要直接决定具体 POI，结果不稳定。
- 后端容易写死：如果用 `if light_date then 避开面馆` 这类逻辑，场景一多就会变成无法维护的分支堆。
- 交互像机器：用户说“晚饭早一点”或“不安排活动”时，系统直接失败或硬重排，没有像人一样解释取舍。

本轮改成：

```text
LLM 负责共情抽取 -> 后端 demandProfile/planningPolicy 归一 -> 矩阵打分召回 -> 多节点 Scheduler 确定性组合 -> 冲突时先引导确认
```

本轮最关键的结构变化是：Scheduler 不再是“活动 x 餐厅”的二元拼装器，而是面向 4-6 小时本地出行的多节点时间窗规划器。它会把主活动、补充体验、二级商圈 open-access 节点、茶饮休息、餐饮、路线和小缓冲一起放进同一个搜索空间，并用时间利用率、长空窗、路线成本、预算和商业收益分层排序。

## 3. 语义画像桥

LLM 不再每次输出一大堆完整画像，也不直接决定去哪。它先输出结构化需求，再由后端归一成两个稳定真相源：

```text
demandProfile：事实、硬约束、底层需求维度、目的地锚点、开放假设
planningPolicy：时间窗含义、出发/返程、目标体验块、最大空窗、跨商圈转场策略
```

旧版 `socialIntent` 仍保留兼容，但正式召回和调度逐步以 `demandProfile` 与 `planningPolicy` 为准。LLM 负责输出较小的语义结构：

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

如果用户没有点击卡片，而是直接在聊天框里说“这个吃的地方不太行”“换个更适合聊天的”“路线太折腾”，系统会先调用轻量 LLM Router 判断 `targetKind`：

```text
restaurant -> 释放餐厅锁，保留活动
activity   -> 释放活动锁，保留餐厅
route      -> 优先刷新路线/商圈布局
whole_plan -> 活动、餐厅、路线都可重排
unclear    -> 进入引导确认
```

关键词规则只作为 LLM Router 不可用或低置信度时的兜底，不再作为自由追问的主要理解方式。

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

晚饭默认仍按 17:30 后处理。但当用户明确 18:00 前结束或时间窗与正常晚饭冲突时，系统不会偷偷把 16:30 包装成正常晚饭，而是在前端展示两个可执行走法：

```text
正常晚饭：尊重 17:30 后吃饭，但可能需要少安排一站或放宽结束时间。
提前轻松吃一顿：把餐饮前置，适合必须早结束的场景。
```

这类选择应该以结构化补丁进入后端，例如 `mealTiming=normal/earlier`，自然语言只作为用户可读说明。

## 9. 当前端到端流程

```text
1. 用户一句话输入
2. router 判断新规划/追问/解释/确认/局部修改
3. extractor 调 LLM 产出结构化需求
4. normalize_structured_demand 补齐字段并调用 taxonomy
5. mock_api 搜活动、餐厅、二级商圈 open-access 节点和路线
6. Stage 3 综合分排序，修复 Top-K 漏斗
7. scheduler 组合主活动、补充体验、餐饮、路线和缓冲，生成多节点时间轴
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
- `planning_policy.py`：出行语义策略，统一处理集合后开始、门到门出行、返程、体验块和空窗阈值。
- `scheduler.py`：多节点时间窗调度、弹性活动/餐饮时长、长空窗淘汰、饭点分支、失败人话建议。
- `timeline_quality.py`：时间利用率、空窗、路线和体验块质量度量。
- `subarea_supply.py`：二级商圈 open-access 供给导入与字段校验。
- `temporal_utils.py`：周六/周日/周天/周末等共享时间语义。
- `backend/app/services/pipeline.py`：流式链路、LLM 修改器、引导确认、会话状态保存。
- `frontend/src/modifyIntents.ts`：前端点击修改时生成不同方向的隐藏上下文。
- `frontend/src/components/PlanCard.tsx`：时间轴卡片与节点修改入口。
- `frontend/src/components/ChatInput.tsx`：输入框中的修改上下文块。
- `frontend/src/components/StageProgress.tsx`：阶段处理中的 spinner。
- `backend/app/routers/admin.py`：受 Token 保护的 POI/Mock 数据管理接口。
- `backend/app/services/admin_auth.py`：后台接口统一管理员鉴权。
- `frontend/src/components/AdminConsole.tsx`：POI 供给与自进化审核台。

## 11. 验证记录

本轮完成了四类验证：

```text
Python AST 检查：通过
test_examples.py：13 个离线样例通过
frontend npm run build：通过
test_architecture_v5.py：通过
run_llm_capability_eval.py --limit 12：21/21，通过 7 个能力 x 3 条用例，首页 5 个案例全通过
10 组多轮真实 LLM 自测：PASSED=10/10
自进化专项验收：通过
```

最终自测目录：

```text
D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\20260606_120249
D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\evolution_20260606_120136
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
方案生成/重排目标 <= 30 秒，最终联网自测最大单轮 19.298 秒
本地 Mock 工具响应 <= 3 秒
每组完整多轮端到端流程 <= 2 分钟
```

为兼顾速度和抽取质量，默认首轮 Prompt 使用精简 Schema 和 3 个代表性 few-shot；完整 Schema、完整 examples 仍保留给本地校验，并可通过 `FLOWCITY_LLM_FULL_PROMPT=true` 恢复完整 Prompt 调试。

## 12. 仍然不做的事

- 不接真实美团 API。
- 不因为使用 Mock API 就降低数据结构要求；Mock API 必须模拟 POI 召回、路线、余票、座位、排队、团购、运行时异常和确认前重排。
- 不做真实预约、取号、订票、团购或支付。
- 不把最后推荐理由交给 LLM 润色，避免额外 2 到 4 秒串行延迟。
- 不把所有可能情况写死在后端 if 里；新增业务倾向优先改 taxonomy、数据标签和少量通用补丁。

## 13. v5 渐进式区域召回

本轮进一步把“先全量扫 POI”改成更接近真实本地生活平台的固定检索链路：

```text
用户需求
-> 提取地理锚点、时间窗、距离容忍、同行约束
-> 商圈/景点圈粗排
-> 点名目的地强制保留
-> 只展开入围区域 POI
-> 活动/餐厅分别海选 Top-K
-> Scheduler 组合时间轴
```

这个链路对所有用户一致，不会因为某个语义场景临时换一套检索流程。区别只在于同一套流程里的分数权重不同：

- 用户点名“就去大雁塔/曲江/小寨”：该目的地区域永远进入下一轮。
- 用户只说“近一点/别太远”：远区域会在商圈粗排中降权或淘汰。
- 用户没有地理偏好：系统用时间窗、出发地、供给密度、评分、路线成本做通用粗排。
- 点名目的地不可行：进入引导式冲突对话，不能静默替换成别的区域。

这样既保留用户明确目标，也避免每次都把全部 POI 拿出来细算。

## 13.1 多节点时间窗规划

4-6 小时本地出行默认不是“一个活动 + 一个餐厅”。当前 Scheduler 的目标是：

```text
至少 1 个主活动
+ 1 个补充体验/二级商圈/短逛/茶饮休息
+ 1 顿正餐或符合用户要求的餐饮
+ 必要路线和小缓冲
```

核心约束：

- `planningPolicy.maxIdleMinutes` 默认 45 分钟，用户明确想休息时可放宽。
- 超过阈值的连续无意义等待默认淘汰；如果供给不足，要在风险里说明，不能伪装成合理安排。
- 空窗越长，越允许 20-30 分钟跨商圈转场；短空窗优先同商圈。
- 商业排序只在体验合格方案里生效，不能让高收益覆盖明显糟糕的时间轴。
- 活动和餐饮时长可弹性选择，开放街区/商场/书店/茶饮可压缩或拉长；电影、演出、剧本杀等固定场次保持固定。

二级商圈 open-access 节点是为了表达“一个大商圈内部仍有可逛的一小时空间”，例如回民街、骡马市、赛格、开元商城这类不一定精确到店铺的开放供给。它们参与活动候选池和时间窗评分，但不进入库存/运行时影子表。

## 14. v5 底层画像与开放假设

正式打分不依赖“儿童放电”这种剧本化句子，而是尽量拆成稳定底层维度：

```text
同行结构：成人、儿童、老人、情侣、朋友
体力强度：低体力、中等活动、高消耗
环境需求：低噪、可落座、室内、避雨、少排队
餐饮需求：清淡、低脂、正餐、火锅、烤肉、可预约
社交氛围：自然不尴尬、适合慢聊、烟火气、轻松热闹
操作约束：预算、时间窗、距离、点名目的地、必须/不要
```

LLM 可以做隐性推理，但必须标注来源：

- `explicit`：用户直接说过，高权重。
- `inferred`：从上下文合理推断，中低权重。
- `open_hypothesis`：正式维度表达不完整的长尾猜测，只参与开放召回和学习观察，不直接污染 taxonomy。

例如“带 5 岁孩子和老婆，老婆最近减脂，别太远”会被拆成：

```text
儿童同行、亲子友好、低体力、少走路、低脂清淡、距离敏感
```

它不会自动变成“自然观察”或“儿童放电”。如果用户明确说“想带孩子放放电”，才会给“高消耗/释放精力”更高权重。

## 15. 受控自进化闭环

当前项目已经具备一个轻量但完整的受控学习闭环：

```text
发现新需求
-> 作为 openHypothesis 做本地向量召回
-> 记录匿名反馈：展示、删除、修改、确认、选择
-> 聚合同类开放假设
-> 过滤负向和分歧样本
-> 生成待审学习提案
-> 开发者/运营批准
-> 作为已审核学习模式参与后续召回
```

它不是让 AI 自动把新句子写进标签库。原因是用户表达会非常发散：

```text
老婆最近想减脂
需要清淡一点
想吃低脂餐
不要太油腻
最近控制体重
```

这些句子如果直接进入正式标签库，标签会越来越乱。当前策略是：原话只作为证据和学习样本保存，正式打分仍归一到稳定底层维度，例如 `清淡健康`、`低脂少油`。

本轮自进化的边界：

- 可以实现：开放假设召回、匿名反馈、聚类统计、待审提案、人工批准后参与召回。
- 不自动实现：直接修改 `intent_taxonomy.py`、自动新增正式 POI 标签、自动替代人工运营审核。
- 当前数据量约 142 个 POI，不需要向量数据库；启动缓存 POI 向量，请求时内存余弦计算即可。
- 未来百万级数据再替换 Faiss/HNSW，业务流程保持不变。

## 16. 管理台与提交边界

主文件夹外已有一个旧版 `MockAPI数据管理台`，它是通用 JSON 编辑器，能打开当前 `Flowcity/data/*.json`，因为它会动态读取字段。

但它不是 v5 自进化审核台：

- 它能编辑 `tags/vibeTags/behaviorTags/audienceTags`，但不知道这些标签的作用域和权重。
- 它能增删 POI，但不会校验画像矩阵、区域召回、显式偏好保真和学习提案。
- 它没有展示 `openHypotheses`、聚类质量、确认率、删除率、批准/拒绝状态。

所以本轮不把旧管理台原封不动塞进主项目，而是在主项目里新增一个受保护的轻量后台入口：

```text
http://localhost:5173/#admin

GET    /api/admin/datasets
GET    /api/admin/coverage
POST   /api/admin/datasets/{slug}/{collection_key}
PUT    /api/admin/datasets/{slug}/{collection_key}/{record_index}
DELETE /api/admin/datasets/{slug}/{collection_key}/{record_index}
GET  /api/learning/analysis
GET  /api/learning/proposals
POST /api/learning/proposals/{proposal_id}/review
```

当前后台已经能做两件事：

- 看 POI 覆盖 KPI、商圈供给缺口、POI 一对一运行时影子表比例。
- 编辑 FlowCity 当前 `data/*.json` 供给数据，保留旧工具最实用的字段表单，同时保留 JSON 精修入口，避免复杂对象被表单误改。
- 审核自进化学习提案，展示样本、确认率、删除率、语义凝聚度，并支持批准/拒绝。

后续如果继续增强运营/开发者审核台，最合理的方式是复用旧管理台的“三栏编辑器交互”，再补 v5 专用能力：

- 样本证据、正负反馈、聚类指标。
- 批准、拒绝、继续观察。
- 批准后只进入已审核学习模式，不自动改正式 taxonomy。

## 17. POI 供给与命名原则

这次补 POI 不靠“钟楼广场散步”“回民街短逛线”这种伪地点名堆数据。正式规则是：

- `name` 只写用户能在地图或商场里理解的真实地点、门店或品牌名，例如 `钟楼`、`大雁塔北广场`、`北院门风情街`、`霸王茶姬(高新万达店)`。
- “可散步、可短休、适合等位、适合低体力”属于 `behaviorTags` 或 `mockBasis`，不属于地点名称。
- “亲子、朋友、轻约会、长辈照顾”属于 `audienceTags`；“自然不尴尬、安静慢聊、烟火气”属于 `vibeTags`。
- “老婆减脂、孩子想放电、朋友想坐下聊”是用户证据或开放假设，不直接写成 POI 标签；后端会归一到 `清淡健康`、`高消耗`、`可坐下聊天` 等稳定底层维度。
- 每个正式商圈至少要有活动、餐厅、过渡补位点三类供给，并尽量覆盖免费/低价/中价/高价层。
- 有消费的场景会在预算内尽量贴近用户上限，但预算、时间、营业、余票、座位和儿童适龄仍是硬边界。
- `mock_runtime_status.json` 中活动和餐厅是一对一 POI 运行时影子表，约 40% 变化，用来验证确认前无票、无座和排队变化；路线变慢和套餐库存变化是扩展动态对象，不参与 POI 异常比例。

当前覆盖情况：

```text
活动 POI：82
餐厅 POI：60
主 POI：142
二级商圈 open-access 节点：18
规划候选总节点：160
正式商圈：6 个，每个都有活动、餐厅和至少 2 个补位点
POI 运行时影子表：142 条，对应 142 个 POI
POI 动态异常：57 条，约 40.1%
路线/团购券扩展动态：149 条，单独用于模拟拥堵和库存变化
```

Mock 是比赛允许的工具层实现方式，但 Mock 不能像临时假数据。路线、区域、动态供给、治理字段和管理台健康检查都要能自洽。后续治理优先级：

```text
1. 路线补 routeId，并补齐 from/to 引用的 origin/area。
2. 管理台明确展示二级商圈是 open_access，不是缺库存。
3. schema/prompt/后端 normalization 对 planningPolicy 保持一致。
4. 饭点选择按钮直接传结构化 mealTiming 补丁。
5. 前端和管理台避免暴露 mockBasis 这类补库痕迹。
```

## 18. 会话与模拟执行边界

当前项目不做注册、登录和用户中心，只做轻量会话隔离：

```text
浏览器 localStorage 保存随机 sessionId
-> 同一浏览器刷新可继续
-> 点击“新规划”生成新 sessionId 并清空聊天
-> 后端 SESSION_STORE 保存当前 plan/demand/supply/executionDraft
-> 超过 TTL 或容量上限自动清理
```

默认配置：

```text
FLOWCITY_SESSION_TTL_SECONDS=7200
FLOWCITY_SESSION_MAX_COUNT=500
```

确认执行也改成可信 Mock 边界：

```text
前端只传 sessionId + planId
后端校验 planId 是否等于当前会话保存的 currentPlanId
后端使用保存的 executionDraft 执行 Mock 确认
```

这仍然不是接入真实美团交易。前端文案统一为“模拟执行”，避免把 Mock 票码、Mock 预约号说成真实下单。

后台接口默认关闭。只有配置 `FLOWCITY_ADMIN_TOKEN` 才挂载 `/api/admin/*` 和 `/api/learning/*`，并且每次请求必须带 `X-FlowCity-Admin-Token`。普通用户聊天页不会看到后台入口，也不会携带管理员 Token。
