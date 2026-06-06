# FlowCity

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent 原型。

它不是普通搜索推荐，也不是写死场景的聊天机器人。用户用一句自然语言说出目标后，系统会完成：

```text
自然语言输入 -> LLM 需求抽取 -> 语义画像补全 -> 本地供给召回打分 -> Scheduler 组合时间轴 -> Validator 校验 -> 执行草案 -> 交互式修改
```

当前进度：**语义画像桥 v5 + 固定渐进式区域召回 + 本地向量开放假设召回 + 受控自进化审核链路 + Web Demo 交互式修改已跑通**。

## 当前能力

- 一句话规划：识别时间、人群、预算、出发地、关系、饮食、儿童/老人、跨城和潜在冲突。
- 语义画像桥：LLM 只输出 `primary/subScenario/显式偏好`，后端用 `intent_taxonomy.py` 补全默认画像和权重。
- 证据门控：宽泛场景默认落到中性 `general`；`初次约会/儿童放电/地标打卡/桌游破冰` 等具体剧本必须能在用户原话中找到证据。
- 标签作用域：餐饮、活动和通用氛围标签按候选类型参与打分，避免“减脂/清淡”误给活动加分。
- 约束隔离：距离、预算、时间、交通、排队等操作约束不进入 vibe 矩阵，由对应约束与调度模块处理。
- 理由溯源：推荐理由区分“用户明确偏好 / 画像辅助参考 / 可执行依据”，不会把被推荐的项目类型反推成用户隐含需求。
- 矩阵打分：活动/餐厅候选用 `baseQualityScore + semanticScoreDelta + constraintFitScore + routeHintScore` 综合排序，避免只靠 JSON 顺序取 Top-K。
- 显式偏好优先：用户明确喜欢的标签会击穿默认避雷；用户明确避开的标签会击穿默认偏好。
- Unknown 降级：`unknown/casual_meetup` 且无显式偏好时不补默认画像，让语义分为 0，候选回到评分、预算、商圈、排队、座位和路线等基础分。
- 交互式修改：前端时间轴每个节点可点“修改”，输入框会带上对应的隐藏上下文提示词，后端按餐厅、活动、路线、过渡点、整体大改分别处理。
- 自由追问路由：用户不点击卡片、只说“这个吃的地方不太行”这类自然追问时，会先由轻量 LLM Router 判断修改餐厅、活动、路线还是整体方案；本地关键词规则只作为失败兜底。
- 人话确认：当用户追问“晚饭早一点”“早点回家”“只吃饭不安排活动”等可能冲突的需求时，系统会先给可执行选项，而不是直接报错。
- Mock 执行：默认只生成执行草案；用户显式确认后才生成 Mock 票码、预约号、取号号或路线提醒。
- 固定渐进式召回：所有请求都先粗排商圈/景点圈，点名目的地永远保留；探索区域才参与淘汰。
- 显式偏好保真：用户明确改成火锅、烤肉、大排档时，系统要么命中真实品类，要么进入引导协商，不能拿相近氛围糊弄。
- 受控自进化：长尾模糊需求作为开放假设进入向量召回，用户删除、修改、确认、模拟执行形成匿名反馈；稳定后只生成待审提案，不会自动改正式画像库。

## 目录结构

```text
Flowcity/
  backend/                 # FastAPI 流式接口
  frontend/                # Vite + React 移动端 Demo
  data/                    # 西安活动、餐厅、路线、动态状态、团购 Mock 数据
  demand_profile.py        # 推荐评分唯一画像真相源：事实、硬约束、底层维度、目的地锚点、开放假设
  area_retrieval.py        # 商圈/景点圈粗排，点名目的地保护，供给不足时渐进扩展
  poi_profiles.py          # POI 基础画像，把活动/餐厅转成稳定数值属性
  semantic_retrieval.py    # 本地 Embedding + 内存余弦召回
  learning_events.py       # 匿名学习事件：展示、删除、修改、确认、选择
  ontology_evolution.py    # 自进化判官：聚类、阻断、提案、审批
  intent_taxonomy.py       # 本地语义画像库、默认标签、权重和显式偏好策略
  extractor.py             # LLM 需求抽取和结构化归一
  mock_api.py              # Stage 3 供给过滤、矩阵打分、Top-K 海选
  scheduler.py             # 活动 x 餐厅 x 路线组合与时间轴调度
  validator.py             # 预算、营业、余票、座位、排队、路线风险校验
  executor.py              # Mock 执行草案与确认后 Mock 执行
  router.py                # 多轮交互路由：新规划、局部修改、解释、确认
  refinement.py            # 会话内二次修改补丁
  run_flow.py              # 命令行完整链路
  test_examples.py         # 离线回归测试
  backend/app/routers/admin.py       # 受 Token 保护的 POI 数据管理 API
  backend/app/services/admin_auth.py # 后台接口统一鉴权
  frontend/src/components/AdminConsole.tsx # POI 与自进化审核台
  schema.json              # 结构化需求 Schema
  prompt.md                # 需求抽取 Prompt
  PROJECT.md               # 产品和架构说明
```

自测产物不保存在项目目录内。本轮 v5 真实 LLM 自测保存在：

```text
D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\20260605_224256
D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\20260606_120249
D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\evolution_20260606_120136
```

`20260605_224256` 是上一轮基线留档；`20260606_120249` 是本轮 10 组多轮产品链路最终验收；`evolution_20260606_120136` 是自进化专项验收报告。

## 环境变量

复制 `.env.example` 为 `.env`，填写自己的 DeepSeek API Key。

```env
DEEPSEEK_API_KEY=你的真实key
FLOWCITY_LLM_MODEL=deepseek-v4-flash
FLOWCITY_LLM_BASE_URL=https://api.deepseek.com
FLOWCITY_LLM_JSON_OUTPUT=true
FLOWCITY_LLM_MAX_TOKENS=4096
FLOWCITY_EMBEDDING_ENABLED=true
FLOWCITY_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
FLOWCITY_EMBEDDING_CACHE_DIR=
FLOWCITY_LEARNING_DB=
FLOWCITY_APPROVED_LEARNING_ENABLED=true
FLOWCITY_ADMIN_TOKEN=
FLOWCITY_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
FLOWCITY_SESSION_TTL_SECONDS=7200
FLOWCITY_SESSION_MAX_COUNT=500
```

`.env` 包含真实 Key，不能提交到 Git。

DeepSeek Chat API 用于结构化理解。本地向量召回使用轻量中文 Embedding 模型 `BAAI/bge-small-zh-v1.5`。当前约 142 个 POI，启动时预计算向量，请求时只对开放假设生成向量并在内存里做余弦相似度，无需部署向量数据库。

## POI 供给原则

- `name` 只写真实地点、门店或品牌名，例如 `钟楼`、`大雁塔北广场`、`北院门风情街`；不要把“散步线、短逛、休息点、等位”写进名字。
- 动作和适用场景放进 `behaviorTags`、`vibeTags`、`audienceTags`、`mockBasis`，让算法理解它适合慢聊、低体力、补位或亲子，但不污染用户看到的地点名。
- POI 标签只写稳定事实画像：人群、体力、噪声、可坐下、室内外、预约、排队、消费层级；不要把“老婆减脂/想放松一下”这类用户故事写成正式标签。
- 每个正式商圈都要覆盖活动、餐厅和过渡补位点，并尽量有免费/低价/中价/高价层，避免预算变化后系统无解。
- 动态异常放在 `mock_runtime_status.json`。活动和餐厅是一对一 POI 运行时影子表，当前 142 个 POI 对应 142 条运行时状态，其中约 40% 变化；路线和团购券是额外动态对象，不参与 POI 异常比例。
- 新长尾需求先进入开放假设和学习提案，只有经过后台审核后才参与后续召回，不自动改正式 taxonomy。

## 本地运行

进入项目：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity"
```

安装前端依赖：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\frontend"
npm install
```

启动后端：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\backend"
uvicorn app.main:app --reload --port 8010
```

启动前端：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\frontend"
npm run dev
```

访问：

```text
http://localhost:5173
```

前端通过 Vite proxy 调用后端 `http://localhost:8010`。

## 常用命令

只查看需求抽取 Prompt，不调用模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' extractor.py --input "周六下午2点到6点，带5岁孩子和老婆出去玩，预算400。" --dry-run
```

跑完整命令行链路：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\run_flow.py --input "周六下午2点到6点，我从曲江池附近出发，带5岁孩子和老婆，老婆最近减脂，别太远，总预算400。" --limit 3
```

离线回归测试，不调用模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' test_examples.py
```

前端构建：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\frontend"
npm run build
```

10 组多轮真实 LLM 自测，产物保存到项目外：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\run_eval_v5.py"
```

受控自进化专项验收：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "D:\产品\美团\周末闲时活动规划\Flowcity_v5_eval_runs\run_evolution_acceptance.py"
```

学习提案审核 CLI：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' ontology_evolution.py --list-proposals
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' ontology_evolution.py --approve proposal_xxx
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' ontology_evolution.py --reject proposal_xxx
```

供后台页面使用的管理 API：

```text
GET    /api/admin/datasets
GET    /api/admin/coverage
POST   /api/admin/datasets/{slug}/{collection_key}
PUT    /api/admin/datasets/{slug}/{collection_key}/{record_index}
DELETE /api/admin/datasets/{slug}/{collection_key}/{record_index}
GET  /api/learning/analysis
GET  /api/learning/proposals
POST /api/learning/proposals/{proposal_id}/review
```

这些接口不应该放进普通用户聊天页面，只给开发者或运营审核使用。

`/api/admin/*` 和 `/api/learning/*` 默认不挂载。只有配置 `FLOWCITY_ADMIN_TOKEN` 后才启用，并且请求必须携带：

```text
X-FlowCity-Admin-Token: 你的后台token
```

前端已有轻量后台页面：

```text
http://localhost:5173/#admin
```

这个页面可以查看当前 POI 覆盖 KPI、商圈供给缺口、POI 一对一运行时影子表比例，也可以用字段表单或 JSON 精修 `data/*.json` 里的 POI/商圈/路线/动态状态/团购数据，并审核自进化聚类、批准或拒绝学习提案。已有的 `D:\产品\美团\周末闲时活动规划\MockAPI数据管理台` 仍可作为旧版通用 JSON 编辑壳子参考，但不再是主项目的管理入口。

多轮对话使用轻量会话隔离，不需要注册登录：

- 前端把随机 `sessionId` 存在本浏览器 `localStorage`，刷新同一页面可继续。
- 点击“新规划”会生成新的 `sessionId` 并清空聊天。
- 后端 `SESSION_STORE` 有 TTL 和容量上限，默认 2 小时、最多 500 个会话。
- 确认模拟执行时，前端只传 `sessionId + planId`；后端从会话里取保存的执行草案，避免信任前端回传的完整方案。

## 本轮验证结果

- `test_examples.py`：13 个离线样例通过。
- `frontend npm run build`：通过。
- `test_architecture_v5.py`：v5 架构回归通过。
- 10 组多轮真实 LLM 自测：`PASSED=10/10`，最大单轮 19.298 秒。
- 自进化专项验收：通过。未审批前正向模式召回率 0%；审批后留出集规范化假设召回率 100%；负向和分歧模式误晋升均为 0%。
- 速度口径按真实演示优先级验证：每组完整多轮端到端流程不超过 2 分钟；方案生成/重排目标压在 30 秒内。

本轮重点修复了两个真实自测暴露的问题：

- 点击节点修改后，即使系统先追问确认，也会保留 `planControl.clickedModify`，不会丢失“用户改的是路线/餐厅/活动”的上下文。
- 用户后续明确说“只吃饭、不安排活动”时，会覆盖第一次输入里的活动需求，不再让旧画像污染新约束。
- 宽泛同行信息不再自动升级成具体体验目的，例如“带孩子”不会被自动解释成“儿童放电/自然观察”。
- 整体大改会把原方案放进上下文并降低用户不满意目标的优先级；如果用户新要求其实已经被当前方案满足，会保护当前 POI，不会先排除再误报冲突。
- POI 名称只保留真实地点、店铺或品牌名；“散步、短逛、等位、休息”等动作语义进入标签、供给依据或时间轴描述，不写进景点名字。
- taxonomy 外且没有显式证据的 LLM 标签不会进入最终画像；餐饮标签也不会跨域污染活动分数。
- 显式餐饮偏好新增结果保真检查：有真实供给就优先命中；没有供给时向用户解释“保留地点还是保留偏好”，不静默替换。
- 自进化采用“匿名反馈 -> 聚类统计 -> 待审提案 -> 人工批准 -> 参与新请求召回”的闭环，不自动污染正式 taxonomy。

## 明天前端怎么继续

普通用户主界面建议只做三件事：

- 时间轴更清晰：活动、餐厅、路线分别成为可点击节点，节点旁边保留“修改”入口。
- 输入框上下文块更细：点击修改后显示“正在修改：餐厅/活动/路线/整体方案”，用户可一键删除上下文块。
- 冲突确认更像产品：当后端返回 `assistantMessage.quickReplies` 时，渲染为可点选项，而不是普通报错。

自进化审核不要放在普通用户页面。未来可做一个隐藏 Admin Review 页面：

- 展示候选画像提案、样本原话、匿名会话数、确认率、删除率、语义聚合度、留出集效果。
- 操作只有批准、拒绝、继续观察。
- 批准后只是让该开放假设作为“已审核学习模式”参与召回；是否升级为正式底层维度或 taxonomy 别名，仍应离线评估后人工合并。

## 设计边界

- 不接真实美团 API。
- 不做真实支付、订票、预约、排队取号或团购下单。
- 真实执行只到 Mock 草案和 Mock 确认结果。
- LLM Planner 默认关闭；默认链路保持 1 次 LLM 抽取，其余为本地画像补全、供给打分、Scheduler 组合和校验。
