# FlowCity 项目说明

## 1. 产品定位

FlowCity 是一个面向周末本地生活短时出行的 AI 执行 Agent。它解决的不是“搜几个店”问题，而是把用户一句自然语言里的时间、人群、预算、地理、关系、软偏好和履约风险拆清楚，再组合成可以确认、分享和模拟执行的完整时间轴。

```text
FlowCity = 开放式需求理解
         + 语义画像桥
         + 出行语义策略
         + 本地生活供给召回
         + 多节点时间窗调度
         + 履约校验
         + 交互式修改
         + 受控学习闭环
```

## 2. 核心问题

本地生活短时规划的难点集中在三类：

- 自然语言跨度大：用户会混合表达集合时间、出发地、人群、预算、饮食、距离和情绪目标。
- 后端容易写死：如果用 `if 场景 then 推荐某类店` 的方式扩展，场景一多就会变成不可维护的规则堆。
- 履约约束强：活动余票、餐厅座位、排队、路线、营业、预算和时间窗都可能让一个“听起来不错”的方案不可执行。

FlowCity 的核心链路是：

```text
LLM 共情抽取
-> demandProfile / planningPolicy 归一
-> 区域与 POI 渐进式召回
-> 语义矩阵 + 供给质量 + 路线成本打分
-> 多节点 Scheduler 组合时间轴
-> Validator 校验硬约束
-> Executor 生成执行草案
-> 前端交互式修改、确认、分享
```

核心结构是让 LLM 做开放语义理解，让后端负责稳定、可测试、可治理的规划与履约边界。

## 3. 需求画像与出行策略

LLM 不直接决定去哪，而是输出较小的结构化语义。后端将其归一成两个稳定真相源：

```text
demandProfile：
  facts              人数、年龄、同行关系、时间、预算、出发地等事实
  hardConstraints    明确预算上限、时间窗、适龄、明确排除项
  dimensions         活动强度、互动程度、聊天友好、噪音、正式度、休息便利等底层需求
  sceneHypotheses    有证据的场景猜测，仅用于解释和补充召回
  openHypotheses     模型发现的新需求，低权重参与开放召回和学习观察
  evidence/source    每个判断的来源与置信度

planningPolicy：
  timeScope          集合后开始 / 门到门出行 / 未知
  routePolicy        是否计算出发和返程
  targetBlocks       目标体验块数量
  maxIdleMinutes     最大连续无意义空窗
  transferPolicy     是否允许跨商圈、最大转场分钟数
```

底层需求维度使用稳定 key，例如：

```text
activityIntensity / interactionLevel / conversationFriendly
noiseLevel / formality / privacy / novelty
restAvailability / familyAccessibility / weatherResilience
routeConvenience / pricePreference
```

来源权重默认分层：

- 用户明确表达：高权重。
- 可靠上下文推理：中权重。
- 弱需求猜测：低权重。
- 开放假设：只参与补充召回和学习观察，不直接污染正式画像库。

## 4. POI 基础画像

POI 不存储“适合约会”“适合儿童放电”这类具体剧本标签，而保存稳定事实与基础画像：

- 数值维度：强度、噪音、互动、聊天友好、正式度、休息便利、安全性。
- 事实字段：类别、菜系、年龄范围、室内外、营业时间、价格、商圈、预约能力。
- 动态字段：余票、座位、排队、套餐库存、实时价格。

同一个地点不会被永久定义为“约会避雷”或“兄弟局首选”。它只拥有“噪音较高、互动较强、正式度较低、消费低”等事实，在不同需求向量下自然得到不同分数。

## 5. 区域与 POI 召回

FlowCity 使用固定渐进式检索链路：

```text
用户需求
-> 提取地理锚点、时间窗、距离容忍、同行约束
-> 商圈/景点圈粗排
-> 点名目的地强制保留
-> 展开入围区域 POI
-> 活动/餐厅/二级商圈/休息节点多路召回
-> Scheduler 组合时间轴
```

区域粗召回综合考虑：

- 路线与时间可行性。
- 需求维度覆盖率。
- 活动和餐饮供给丰富度。
- 价格区间匹配。
- 基础质量和小权重商业机会。

区域只在绝对不可行时过滤，例如明确距离上限不可达、总路程加最低活动时长已超过时间窗、硬需求完全没有供给、多出发地无法在时间窗内公平集合。

区域内 POI 召回固定运行多通道：

- 硬约束与结构化属性召回。
- 底层需求向量相似度召回。
- 高评分、高质量召回。
- 高平台预期收益召回。
- 场景假设和开放假设的补充语义召回。

各通道合并去重后统一使用底层维度评分，场景假设不能直接加减分。

## 6. 语义矩阵打分

候选总分：

```text
candidateScore = baseQualityScore
               + semanticScoreDelta
               + constraintFitScore
               + routeHintScore
```

字段含义：

- `baseQualityScore`：评分、排队短、可预约/有座、预算友好、商圈匹配。
- `semanticScoreDelta`：用户画像和 POI 标签的正负交集。
- `constraintFitScore`：明确约束匹配，例如儿童友好、清淡低脂、低体力、低成本。
- `routeHintScore`：同商圈少折腾、跨城路线成本、少走路、公平集合。

语义交集受标签作用域约束：餐饮标签只影响餐厅，活动标签只影响活动，通用氛围标签才允许同时影响两类候选。距离、预算、时间、交通、排队和预约属于操作约束，由对应模块处理，不进入 vibe 矩阵。

## 7. 多节点时间窗规划

4-6 小时本地出行默认不是“一个活动 + 一个餐厅”。Scheduler 的目标结构是：

```text
至少 1 个主活动
+ 1 个补充体验 / 二级商圈 / 短逛 / 茶饮休息
+ 1 顿符合用户要求的餐饮
+ 必要路线和小缓冲
```

核心约束：

- `planningPolicy.maxIdleMinutes` 默认 45 分钟，用户明确想休息时可放宽。
- 超过阈值的连续无意义等待默认淘汰。
- 空窗越长，越允许 20-30 分钟跨商圈转场；短空窗优先同商圈。
- 商业排序只在体验合格方案里生效，不能让高收益覆盖明显糟糕的时间轴。
- 活动和餐饮时长可弹性选择，开放街区/商场/书店/茶饮可压缩或拉长；电影、演出、剧本杀等固定场次保持固定。

二级商圈 open-access 节点用于表达“大商圈内部可逛的一小时空间”，例如回民街、骡马市、赛格、开元商城。它们参与活动候选池和时间窗评分，但不进入库存/运行时影子表。

## 8. 饭点与冲突协商

晚饭默认尊重正常正餐节奏。当用户时间窗和正常饭点冲突时，系统不偷偷替用户改饭点，而是给出可执行分支：

```text
正常晚饭：尊重正餐时间，可能需要少安排一站或放宽结束时间。
提前轻松吃一顿：把餐饮前置，适合必须早结束的场景。
```

前端按钮会把结构化补丁传给后端，例如：

```text
constraintsPatch.mealTiming = normal / earlier
```

自然语言只作为用户可读说明，不作为后端唯一判断依据。

## 9. 多轮交互

时间轴卡片支持两类修改：

- 单节点修改：活动、餐厅、路线、过渡点分别携带不同结构化上下文。
- 整体大改：释放活动、餐厅和路线锁，重排整套方案。

用户点击“修改”后，输入框上方出现浅黄色上下文块。它只展示用户需要知道的对象，例如“修改：钟楼茶食坐坐馆”，同时把后端需要的结构化上下文随请求发送：

```text
targetKind
targetTitle
targetTimeRange
targetPoiId
allowedPatchKeys
userText
```

如果用户直接在聊天框里说“这个吃的地方不太行”“便宜一点”“路线别折腾”，系统会先用轻量 Router 判断修改目标，再进入局部重排或引导确认。关键词规则只作为低置信度兜底。

## 10. 履约与执行

Validator 检查：

- 时间窗口。
- 营业时间。
- 预算。
- 适龄和人群适配。
- 余票、座位、排队。
- 路线风险。
- 运行时异常。

Executor 默认只生成执行草案。用户确认后才进入模拟执行，并且前端只传 `sessionId + planId`，后端从会话里取保存的执行草案，避免信任前端回传完整方案。

```text
前端确认
-> sessionId + planId
-> 后端校验本次会话方案
-> 重新检查执行草案
-> 生成模拟票码/预约号/取号号/路线提醒
```

FlowCity 不做真实支付、订票、预约、排队取号或团购下单。

## 11. 受控学习闭环

FlowCity 的学习闭环是受控的：

```text
发现新需求
-> openHypothesis 参与本地向量召回
-> 记录匿名反馈：展示、删除、修改、确认、选择
-> 聚合同类开放假设
-> 过滤负向和分歧样本
-> 生成待审学习提案
-> 开发者/运营批准
-> 作为已审核学习模式参与新请求召回
```

它不会让 AI 自动把新句子写进标签库。用户表达会非常发散，正式打分仍归一到稳定底层维度，例如 `清淡健康`、`低脂少油`、`可坐下聊天`。

## 12. 管理台

主项目内置受保护的轻量后台入口，用于数据治理和学习提案审核：

```text
http://localhost:5173/#admin

GET    /api/admin/datasets
GET    /api/admin/coverage
POST   /api/admin/datasets/{slug}/{collection_key}
PUT    /api/admin/datasets/{slug}/{collection_key}/{record_index}
DELETE /api/admin/datasets/{slug}/{collection_key}/{record_index}
GET    /api/learning/analysis
GET    /api/learning/proposals
POST   /api/learning/proposals/{proposal_id}/review
```

后台能力：

- 查看 POI 覆盖 KPI、商圈供给缺口、POI 一对一运行时影子表比例。
- 编辑 `data/*.json` 供给数据，支持字段表单和 JSON 精修。
- 审核自进化学习提案，展示样本、确认率、删除率、语义凝聚度，并支持批准或拒绝。

后台接口默认关闭。只有配置 `FLOWCITY_ADMIN_TOKEN` 才挂载 `/api/admin/*` 和 `/api/learning/*`，普通用户聊天页不会看到后台入口，也不会携带管理员 Token。

## 13. 代码结构

```text
frontend/src
  用户端聊天、时间轴、分享、确认、历史会话和后台管理台。

backend/app
  FastAPI API、鉴权、会话、流式输出和核心引擎编排适配。

项目根目录 FlowCity Core
  需求理解、画像归一、区域召回、供给打分、时间窗调度、
  履约校验、模拟执行、学习闭环和离线测试共用领域层。

data
  活动、餐厅、商圈、二级商圈、路线、动态状态、团购和学习数据。
```

重点模块：

- `extractor.py`：LLM 需求抽取、schema 校验和结构化归一。
- `demand_profile.py`：事实、硬约束、底层需求维度、目的地锚点、开放假设。
- `planning_policy.py`：出行语义策略。
- `intent_taxonomy.py`：语义画像库、显式偏好和标签作用域。
- `mock_api.py`：多路召回、语义矩阵和候选打分。
- `scheduler.py`：多节点时间窗调度。
- `timeline_quality.py`：时间利用率、空窗、路线和体验块质量度量。
- `validator.py`：硬约束与履约风险校验。
- `executor.py`：执行草案和模拟执行。
- `router.py` / `refinement.py`：多轮追问和局部修改。
- `supply_governance.py`：POI 治理派生层。
- `route_identity.py` / `poi_identity.py`：路线和 POI 稳定身份。
- `backend/app/services/pipeline.py`：流式链路、会话状态和前后端协议编排。
- `frontend/src/components/PlanCard.tsx`：方案卡、时间轴、确认和分享。
- `frontend/src/components/AdminConsole.tsx`：POI 供给与学习提案管理台。

## 14. 数据治理

数据覆盖：

```text
活动 POI：82
餐厅 POI：60
主 POI：142
二级商圈 open-access 节点：18
规划候选总节点：160
正式商圈：6 个
POI 运行时影子表：142 条
POI 动态异常：约 40%
路线/团购券扩展动态：149 条
```

治理规则：

- `name` 只写用户能理解的真实地点、门店或品牌名。
- “可散步、可短休、适合等位、适合低体力”属于行为标签或依据说明，不属于地点名称。
- “亲子、朋友、轻约会、长辈照顾”属于受众标签；“自然不尴尬、安静慢聊、烟火气”属于氛围标签。
- “老婆减脂、孩子想放电、朋友想坐下聊”是用户证据或开放假设，不直接写成正式 POI 标签。
- 路线使用 `routeId`、`routeRef`、`legacyRouteRef` 管理稳定身份和旧引用兼容。
- 二级商圈是 `open_access` 供给，不要求库存影子表。
- `supply_governance.py` 在加载时派生来源、置信度、验证时间和事实/约束标签。

## 15. 质量验证

质量检查由四组任务覆盖：

```text
test_examples.py              离线业务样例回归
test_architecture_v5.py       架构约束与关键边界回归
frontend npm run build        前端构建与类型检查
run_llm_capability_eval.py    真实 LLM 能力覆盖评估
```

覆盖重点：

- 首页案例和多轮修改。
- 集合点语义、门到门出行、跨城入城、返程。
- 低成本、预算贴近、饭点分支、少走路。
- 长空窗淘汰、多节点时间窗、二级商圈 open-access。
- 执行草案、确认阻断、模拟执行、分享卡片。
- 后台开关、学习提案、路线身份和 POI 治理字段。

## 16. 设计边界

- 不接真实美团 API。
- 不做真实交易、支付、订票、预约或排队取号。
- 不把每个用户故事写成后端分支；新增业务倾向优先沉淀到 taxonomy、稳定标签、数据治理和通用调度策略。
- 不把开放假设自动升级为正式画像；学习结果必须经过审核。
- 不在普通用户页面展示内部调试字段，例如 `mockBasis`、治理覆盖细节和学习样本。
