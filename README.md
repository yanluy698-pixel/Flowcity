# FlowCity

FlowCity 是一个面向周末本地生活短时出行的 AI 执行 Agent。用户只需要用一句自然语言说出时间、地点、人群、预算和偏好，系统会把需求拆成可执行的本地生活计划，并在确认前检查时间、路线、预算、余票、座位、排队和运行时风险。

```text
自然语言输入
-> LLM 需求抽取
-> demandProfile / planningPolicy 归一
-> 区域与 POI 渐进式召回
-> 多节点时间窗 Scheduler
-> Validator 履约校验
-> Execution Draft
-> 确认、分享和模拟执行
```

## 系统能力

- 一句话规划：识别时间窗、人数、同行关系、预算、出发地、目的地、饮食偏好、儿童/老人、跨城和结束时间。
- 语义画像桥：LLM 负责抽取结构化语义，后端将其归一为稳定的 `demandProfile`，包含事实、硬约束、底层需求维度、目的地锚点和开放假设。
- 出行语义策略：`planningPolicy` 判断是集合后开始还是门到门出行，是否计算出发/返程，目标体验块数量、最大空窗和跨商圈转场上限。
- 渐进式召回：先粗排商圈和景点圈，再展开活动、餐饮、二级商圈、茶饮休息和路线候选，点名目的地始终保留。
- 多节点时间窗：Scheduler 不再固定拼接一个活动和一个餐厅，而是在主活动、补充体验、开放商圈、餐饮、路线和小缓冲里搜索完整时间轴。
- 长空窗约束：连续无意义等待超过策略阈值会淘汰；供给不足时明确说明，不把长等待包装成合理休息。
- 弹性时长：开放街区、商场、书店、茶饮、博物馆等可按时间窗压缩或拉长；电影、演出、剧本杀等固定场次保持固定时长。
- 饭点分支：默认尊重正常正餐节奏；当结束时间与饭点冲突时，前端展示可选择的走法，选择会以结构化 `mealTiming` 进入后端。
- 交互式修改：时间轴节点可单独修改，整体方案也可重排；前端传递结构化修改上下文，后端根据目标节点和用户补充重新规划。
- 受控学习闭环：长尾模糊需求进入开放假设和匿名反馈，聚类后生成待审学习提案，只有人工批准后才参与新请求召回。
- 模拟履约：默认生成执行草案；用户确认后生成模拟票码、预约号、取号号、路线提醒和分享卡片，不执行真实支付或真实下单。

## 目录结构

```text
Flowcity/
  backend/app/             # FastAPI API 层：流式规划、会话、确认执行、后台接口
    routers/               # flow/admin/learning 路由
    schemas/               # HTTP 请求响应模型
    services/pipeline.py   # API 到核心规划引擎的编排适配层

  frontend/src/            # Vite + React 用户端和后台管理台
    components/            # 聊天、时间轴、分享、确认、后台组件
    flowClient.ts          # 前后端 API 适配
    modifyIntents.ts       # 节点修改上下文

  data/                    # 西安活动、餐厅、路线、动态状态、团购与二级商圈数据

  FlowCity Core            # 核心规划引擎，位于项目根目录，供 API、CLI 和测试复用
    extractor.py           # LLM 需求抽取、schema 校验和结构化归一
    demand_profile.py      # 事实、硬约束、底层维度、目的地锚点、开放假设
    planning_policy.py     # 时间窗含义、出发/返程、体验块、空窗和转场策略
    area_retrieval.py      # 商圈/景点圈粗排，点名目的地保护
    mock_api.py            # 供给召回、矩阵打分、Top-K 海选
    scheduler.py           # 多节点时间窗调度
    validator.py           # 预算、营业、余票、座位、排队、路线风险校验
    executor.py            # 模拟执行草案与确认后模拟执行
    router.py              # 多轮交互路由
    refinement.py          # 会话内二次修改
    *_identity.py          # POI/路线稳定身份
    *_supply.py / *_quality.py / *_governance.py

  schema.json / prompt.md / planner_prompt.md
  test_examples.py / test_architecture_v5.py
  run_flow.py / run_llm_capability_eval.py
```

根目录 Python 模块是 FlowCity Core 领域层。它们承载需求理解、供给召回、调度、校验和执行边界；FastAPI 只负责 HTTP、会话和流式输出适配。

## 环境变量

复制 `.env.example` 为 `.env`，填写自己的模型 Key。

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

## 本地运行

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\backend"
uvicorn app.main:app --reload --port 8010
```

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\frontend"
npm install
npm run dev
```

用户端入口：

```text
http://localhost:5173
```

前端通过 Vite proxy 调用后端 `http://localhost:8010`。

后台管理接口默认不挂载。配置 `FLOWCITY_ADMIN_TOKEN` 后，后台页面可通过 `http://localhost:5173/#admin` 查看 POI 覆盖、商圈供给、运行时影子表、自进化学习提案，并对 `data/*.json` 做受控编辑。

## 常用命令

只查看需求抽取 Prompt，不调用模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' extractor.py --input "周六下午2点到6点，带5岁孩子和老婆出去玩，预算400。" --dry-run
```

跑完整命令行链路：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\run_flow.py --input "周六下午2点到6点，我从曲江池附近出发，带5岁孩子和老婆，老婆最近减脂，别太远，总预算400。" --limit 3
```

离线回归测试：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' test_examples.py
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' test_architecture_v5.py
```

前端构建：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity\frontend"
npm run build
```

LLM 能力覆盖评估：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B run_llm_capability_eval.py --limit 12
```

## POI 与数据原则

- Mock API 是工具适配层，用于模拟 POI 召回、路线、余票、座位、排队、团购、运行时异常和确认前重排；接入真实平台时替换 Tool Adapter，需求画像、调度、校验和执行边界保持不变。
- `name` 只写真实地点、门店或品牌名，例如 `钟楼`、`大雁塔北广场`、`北院门风情街`；动作和场景语义放入 `behaviorTags`、`vibeTags`、`audienceTags` 或 `mockBasis`。
- POI 标签只保存稳定事实画像：人群、体力、噪声、可坐下、室内外、预约、排队、消费层级；用户故事不会写成正式标签。
- 二级商圈使用 `poiLevel=sub_area` 和 `open_access` 动态供给，不进入正式 POI 库存和运行时影子表。
- 路线数据使用 `routeId` 做稳定身份，允许同一组 from/to 下存在公交、打车等多种交通方式。
- POI 治理字段由 `supply_governance.py` 在加载时派生补齐，包括 `sourceType`、`confidence`、`lastVerifiedAt`、`factTags`、`constraintTags`。
- 新长尾需求先进入开放假设和学习提案，只有经过后台审核后才参与新请求召回。

## 会话与执行边界

- 首页默认创建新规划，不静默复用历史 `sessionId`。
- 最近 2 小时内的历史会话只通过历史入口显式恢复。
- 点击“新规划”会生成新的 `sessionId` 并清空聊天。
- 后端会话保存本次 `plan/demand/supply/executionDraft`，确认模拟执行时前端只传 `sessionId + planId`。
- FlowCity 不做真实支付、订票、预约、排队取号或团购下单；真实执行停留在模拟草案和模拟确认结果。

## 质量验证

核心质量检查覆盖四层：

- `test_examples.py`：离线业务样例回归。
- `test_architecture_v5.py`：架构约束、后台开关、路线身份、二级商圈、饭点选择和低成本约束回归。
- `npm run build`：前端 TypeScript 和构建检查。
- `run_llm_capability_eval.py --limit 12`：真实 LLM 能力覆盖，包含首页案例、多轮修改、预算、饭点、路线和画像迁移。

## 设计边界

- 不接真实美团 API。
- 不做真实交易、支付、订票、预约或排队取号。
- 不把所有场景写成后端 if；新增业务倾向优先通过 taxonomy、稳定标签、数据治理和通用调度策略表达。
- 不让开放假设自动污染正式画像库；学习结果必须经过审核。
