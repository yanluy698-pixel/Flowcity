# FlowCity

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent 原型。

它不是普通搜索推荐，也不是写死场景的聊天机器人。用户用一句自然语言说出目标后，系统会完成：

```text
自然语言输入 -> LLM 需求抽取 -> 语义画像补全 -> 本地供给召回打分 -> Scheduler 组合时间轴 -> Validator 校验 -> 执行草案 -> 交互式修改
```

当前进度：**语义画像桥 + Web Demo + 交互式局部修改链路已跑通**。

## 当前能力

- 一句话规划：识别时间、人群、预算、出发地、关系、饮食、儿童/老人、跨城和潜在冲突。
- 语义画像桥：LLM 只输出 `primary/subScenario/显式偏好`，后端用 `intent_taxonomy.py` 补全默认画像和权重。
- 矩阵打分：活动/餐厅候选用 `baseQualityScore + semanticScoreDelta + constraintFitScore + routeHintScore` 综合排序，避免只靠 JSON 顺序取 Top-K。
- 显式偏好优先：用户明确喜欢的标签会击穿默认避雷；用户明确避开的标签会击穿默认偏好。
- Unknown 降级：`unknown/casual_meetup` 且无显式偏好时不补默认画像，让语义分为 0，候选回到评分、预算、商圈、排队、座位和路线等基础分。
- 交互式修改：前端时间轴每个节点可点“修改”，输入框会带上对应的隐藏上下文提示词，后端按餐厅、活动、路线、过渡点、整体大改分别处理。
- 人话确认：当用户追问“晚饭早一点”“早点回家”“只吃饭不安排活动”等可能冲突的需求时，系统会先给可执行选项，而不是直接报错。
- Mock 执行：默认只生成执行草案；用户显式确认后才生成 Mock 票码、预约号、取号号或路线提醒。

## 目录结构

```text
Flowcity/
  backend/                 # FastAPI 流式接口
  frontend/                # Vite + React 移动端 Demo
  data/                    # 西安活动、餐厅、路线、动态状态、团购 Mock 数据
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
  schema.json              # 结构化需求 Schema
  prompt.md                # 需求抽取 Prompt
  PROJECT.md               # 产品和架构说明
```

自测产物不保存在项目目录内。本轮 10 组多轮 LLM 自测保存在：

```text
D:\产品\美团\周末闲时活动规划\Flowcity_interaction_eval_runs\20260605_094648
```

里面每个 case/turn 都包含 `events.json`、`events.ndjson`、`finalPayload.json`，以及可拆出的 `structuredDemand.json`、`mockSupply.json`、`timelinePlan.json`、`validationResult.json` 等链路文件。

## 环境变量

复制 `.env.example` 为 `.env`，填写自己的 DeepSeek API Key。

```env
DEEPSEEK_API_KEY=你的真实key
FLOWCITY_LLM_MODEL=deepseek-v4-flash
FLOWCITY_LLM_BASE_URL=https://api.deepseek.com
FLOWCITY_LLM_JSON_OUTPUT=true
FLOWCITY_LLM_MAX_TOKENS=4096
```

`.env` 包含真实 Key，不能提交到 Git。

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
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' "D:\产品\美团\周末闲时活动规划\Flowcity_interaction_eval_runs\run_eval_interaction_upgrade.py"
```

## 本轮验证结果

- `test_examples.py`：13 个离线样例通过。
- `frontend npm run build`：通过。
- Python AST 检查：`pipeline.py/router.py/refinement.py/scheduler.py` 通过。
- 10 组多轮真实 LLM 自测：`PASSED=10/10`。
- 速度口径按赛题要求验证：方案生成/重排轮次不超过 30 秒，工具查询不超过 3 秒，每组完整多轮端到端流程不超过 2 分钟。

本轮重点修复了两个真实自测暴露的问题：

- 点击节点修改后，即使系统先追问确认，也会保留 `planControl.clickedModify`，不会丢失“用户改的是路线/餐厅/活动”的上下文。
- 用户后续明确说“只吃饭、不安排活动”时，会覆盖第一次输入里的活动需求，不再让旧画像污染新约束。

## 设计边界

- 不接真实美团 API。
- 不做真实支付、订票、预约、排队取号或团购下单。
- 真实执行只到 Mock 草案和 Mock 确认结果。
- LLM Planner 默认关闭；默认链路保持 1 次 LLM 抽取，其余为本地画像补全、供给打分、Scheduler 组合和校验。
