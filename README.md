# FlowCity

FlowCity 是一个面向周末本地生活短时活动的 AI 执行 Agent 原型。

项目目标不是做一个普通聊天机器人或固定场景推荐器，而是让用户用一句自然语言表达目标后，系统能够逐步完成：

```text
自然语言输入 -> 多约束拆解 -> 本地生活工具调用 -> 方案规划 -> 履约校验 -> 确认执行
```

当前进度处于阶段三：数据与 Mock API。

## 当前阶段

阶段二已经能把用户自然语言需求拆成稳定 JSON。
阶段三已经加入本地 Mock 供给数据和函数版 Mock API，用于模拟活动、餐厅、路线、排队、预约和团购库存查询。

当前已完成：

- `schema.json`：定义结构化需求字段。
- `prompt.md`：定义大模型抽取需求的 Prompt 模板。
- `examples.json`：提供 3 套标准输入样例和期望结构化输出。
- `extractor.py`：调用 DeepSeek OpenAI 兼容 API，将用户输入转成 JSON，并做基础校验。
- `data/*.json`：西安本地生活 Mock 数据，包括商圈、活动、餐厅、路线、动态状态和团购。
- `mock_api.py`：函数版 Mock API，读取本地 JSON，完成硬约束过滤和软偏好打分。
- `run_flow.py`：一条命令串联阶段二和阶段三。
- `api.py`：可选 FastAPI 包装层，后续前端或 HTTP 工具调用时使用。

当前不做：

- 不接真实美团 API。
- 不做预约、排队、下单。
- 不做完整行程规划。

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
  examples.json     # 标准样例
  extractor.py      # 需求结构化最小原型
  data/             # 阶段三 Mock 数据
  mock_api.py       # 阶段三函数版 Mock API
  run_flow.py       # 阶段二 + 阶段三串联脚本
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

在 PowerShell 中进入项目目录：

```powershell
cd "D:\产品\美团\周末闲时活动规划\Flowcity"
```

试运行，不调用模型，只查看最终 Prompt：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' extractor.py --input "周六下午2点到6点，带5岁孩子和老婆出去玩，预算400。" --dry-run
```

正式调用 DeepSeek：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' extractor.py --input "周六下午2点到6点，带5岁孩子和老婆出去玩，老婆在减肥，别太远，预算400。"
```

成功后会输出结构化 JSON。

完整链路试运行：自然语言 -> 阶段二结构化 -> 阶段三 Mock 供给查询。

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\run_flow.py --input "我们三个人，周天1到7点要去西安市区玩，怎么安排，我们在咸阳市区，得坐地铁去，预算一人100以内，三个男的" --limit 3
```

只测试阶段三，不调用大模型：

```powershell
& 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\mock_api.py --example-id friends_citywalk --limit 3
```

`run_flow.py` 输出结构：

```text
input
structuredDemand
mockSupply
  activityCandidates
  restaurantCandidates
  routeCandidates
  filteredOut
  toolLogs
```

## FastAPI 可选层

当前阶段优先使用 `mock_api.py` 和 `run_flow.py`。
如果后续要让前端或其他服务通过 HTTP 调用，可安装：

```powershell
pip install fastapi uvicorn
```

然后在 `Flowcity` 目录启动：

```powershell
uvicorn api:app --reload
```

## 当前技术选择

当前暂不使用 LangChain 或多 Agent 框架。

原因是当前任务很单一：

```text
阶段二：自然语言 -> 结构化 JSON
阶段三：结构化 JSON -> Mock 工具查询
```

直接调用模型 API 和本地确定性工具更清楚，也更适合学习和调试。后续进入 Planner、Validator 和 Replanner 循环后，再评估是否引入 Agent 框架。
