# mat-planner

**推进剂与含能材料实验智能体系统**
*Experiment Design Agent + Knowledge-Grounded RAG for Propellant & Energetic Materials Research*

面向含能材料研究的全栈 AI 系统，核心是一个**实验设计 Agent**：输入研究目标，自动检索文献知识库、推荐配方、预测性能（燃速/密度/比冲）、执行安全门控，并将实验结果写回知识库支持迭代改进。底层依托混合检索、知识图谱和 MCP 工具集成，并配备基于实验员视角的 19 题细粒度评估套件（239 分，当前得分 ~96%）。

前端为 **PyroPlanner**——一个基于 Next.js 的对话式界面，支持流式输出、对话历史管理和多轮上下文连续对话。

---

## 快速部署

> 两步启动：先跑后端，再跑前端，打开浏览器即可使用。

### 第一步：启动后端

```bash
# 克隆仓库
git clone https://github.com/AGI4S/pyro-planning-agent.git
cd pyro-planning-agent

# 安装 Python 依赖（需要 uv）
uv sync

# 配置环境变量
cp app/.env.example app/.env
# 编辑 app/.env，填入你的 LLM API 地址和 Key：
#   LLM_BASE_URL=http://your-llm-endpoint
#   LLM_API_KEY=sk-...
#   LLM_MODEL=your-model-name

# 初始化数据库
uv run python scripts/init_db.py

# 摄取样例文献（可选，用于体验检索功能）
uv run python scripts/ingest_sample.py

# 启动后端（监听 8000 端口）
uv run uvicorn app.api.main:app --reload --port 8000
```

### 第二步：启动前端

```bash
cd frontend

# 安装依赖（需要 Node.js 18+）
npm install

# 启动开发服务器（默认 3000 端口，被占用时自动改用 3001）
npm run dev
```

打开终端输出中显示的本地地址（通常为 **http://localhost:3000**）即可使用 PyroPlanner。

---

## 界面预览

**首页**——淡蓝白底色，带分类标签的示例卡片，左侧白色侧栏展示历史会话：

![PyroPlanner 首页](docs/screenshots/homepage.png)

**对话中**——用户消息蓝色渐变气泡，助理回答白色卡片（含工具调用计划标签、Markdown 表格渲染、底部响应耗时分段显示），侧栏激活项蓝色渐变指示：

![PyroPlanner 对话界面](docs/screenshots/chat_view.png)

---

## 目录

- [设计逻辑](#设计逻辑)
- [核心特性](#核心特性)
- [完整配置参考](#完整配置参考)
- [PyroPlanner Chat UI](#pyroplanner-chat-ui)
- [系统架构](#系统架构)
- [规划 Agent 详解](#规划-agent-详解)
- [API 接口](#api-接口)
- [MCP 工具](#mcp-工具)
- [摄取流水线](#摄取流水线)
- [检索系统](#检索系统)
- [评估框架](#评估框架)
- [项目结构](#项目结构)
- [当前功能状态](#当前功能状态)

---

## 设计逻辑

> 本节说明系统每一层功能的设计动机——**为什么要加这个，它解决了什么问题，又引出了什么新问题**。所有功能都从同一个根问题生长出来，而不是随意堆砌。

### 根问题

含能材料实验研究的核心工作流是一个**闭环**：

> *设定目标 → 查文献、设计配方 → 评估安全性 → 做实验 → 分析偏差 → 调整配方 → 再做实验……*

这个循环在现实中高度依赖专家经验，存在三个痛点：

1. **知识散落在文献里**：研究者需要翻阅大量论文才能找到相关配方和性能数据，且同一属性在不同文献中数值常有冲突。
2. **性能预测缺乏约束**：LLM 直接生成预测值容易产生幻觉（如燃速 5.74 mm/s 而非正确的 10.0 mm/s），设计方案无法被物理定律核验。
3. **每轮实验是孤岛**：实测结果停留在纸面，不能自动反哺下一轮设计，迭代改进全靠研究者手动记忆。

下面是系统如何一层一层打通这个闭环的。

---

### 第一层：建立知识底座（文献摄取 + 结构化检索）

**问题**：实验设计需要参考文献中的配方数据、工艺条件、性能参数。没有结构化的知识库，设计完全依赖 LLM 内置记忆，无法溯源、极易幻觉。

**方案**：
- **分层分块**（child 300字 用于检索，parent 4000字 用于阅读）：小检索、大阅读，兼顾精度与上下文完整性。
- **混合检索**（关键词 + BGE-M3 向量 + RRF 融合 + HyDE + LLM Reranker）：覆盖"燃速"与"burning rate"之间的词汇鸿沟。
- **实体属性抽取**：从"AP/HTPB/Al=68/20/12，燃速 12.3 mm/s @7MPa"中结构化提取配方与数值，存入知识库供后续精确查询。
- **知识图谱**：实体共现图 + 属性比较边，检索 AP 时自动补充与 AP 强关联实体（HMX、HTPB 等）的相关段落。

**引出的新问题**：知识库建好了，但研究者的需求是"设计一个燃速 15mm/s 的推进剂"——这是一个设计任务，不是检索任务。如何从目标出发自动生成配方方案？

---

### 第二层：从目标到方案（实验设计 Agent）

**问题**：研究者输入的是自然语言目标（"AP/HTPB/Al 推进剂，燃速 12-15 mm/s @7MPa，密度 1.72 g/cm³"），还可能包含约束（"不含 AP"、"最小烟雾"）。需要把这个目标解析成配方、步骤和预测结果。

**方案**（`ExperimentDesignService`）：
1. **目标解析**：提取性能指标、组分约束（排除 AP、指定粒径等）、压强条件。
2. **文献检索**：在知识库中搜索相关配方和性能数据，作为设计依据。
3. **配方生成**：LLM 结合检索结果和约束生成 1-3 个候选配方，每个配方包含组分比例、粒径规格、工艺步骤。
4. **Vieille 上下文注入**：在交给 LLM 之前，先用 `BurningRateEstimator` 计算经验燃速估算值，注入 LLM 的 system prompt——让 LLM 的预测有物理约束锚点，不会偏离太远。

**引出的新问题**：配方有了，LLM 给出的性能预测是否在物理合理范围内？光靠 LLM 不可信。

---

### 第三层：硬约束性能预测（化学计算模型）

**问题**：LLM 预测"粗粉 AP @7MPa 燃速 ≈ 5.74 mm/s"，而正确值是 10.0 mm/s。根本原因是 LLM 没有内置正确的 Vieille 定律参数。如果把错误的估算值注入上下文，LLM 会顺着这个方向预测，结果全盘偏离。

**方案**（`app/eval/chemistry.py`）：

- **BurningRateEstimator**：Vieille 定律 `r = r₇ × (P/7)^n`，其中 `r₇` 是 7MPa 下的校准参考值（从文献拟合）。粒径（粗/细）、Al 含量、催化剂均有修正系数。关键修正：用 `r₇` 而非 `a` 作为基准，确保在参考压强下与实测值一致。
- **DensityEstimator**：调和平均（TMD = 1/∑(xᵢ/ρᵢ)），覆盖 AP、HTPB、Al、RDX 等 20+ 组分，自动计算实用密度范围（TMD × 0.92-0.99）。内置识别率保护：若已识别组分质量分数 < 50%，返回 None 而非物理上不可能的值（避免因 LLM 输出非标准组分名导致 TMD=6.35 等异常结果）。
- **OxygenBalanceCalculator**：按组分分子式加权计算 OB%，区分"good / acceptable / lean / rich"区间。
- **Kamlet-Jacobs 公式**：单质炸药爆速/爆压估算（`D = 1.01(N M^0.5 Q^0.5)^0.5 (1 + 1.30ρ)`），覆盖 RDX/HMX/TNT/PETN 等。

这些模型的估算值在实验设计时注入 LLM 上下文作为"理论锚点"，在评估时作为硬指标检验 Agent 预测是否合理。

**引出的新问题**：配方性能合理，但安全吗？含能材料的危险性需要在实验前专门评估。

---

### 第四层：实验前安全门控（SafetyCheckerService）

**问题**：AP + 硫磺是已知的危险组合（接触可引发爆燃），NG 纯品极度敏感，某些组分在高温下会分解。LLM 不一定每次都会主动识别这些风险，不能依赖 LLM 判断安全性。

**方案**（`app/services/safety_checker.py`）：
- **组分相容性规则**（`_INCOMPATIBLE_PAIRS`）：对 AP+S、RDX+酸、TATP 等已知危险对硬编码规则检查，每对危险组合附带独立的机理描述和具体替代建议（`dict[tuple, tuple[描述, 建议]]` 结构），如"AP+S → 移除硫磺，改用 Mg 或 Al 粉"。
- **高感度材料清单**：TATP、HMTD、NG、ETN 等触发警告，要求特殊操作规程。
- **温度超限检查**：遍历实验步骤，检测操作温度是否超出组分安全范围（如 NG 在 50°C 以上风险急剧上升）。
- **三级输出**：`approved`（可执行）/ `needs_review`（需专家确认）/ `blocked`（禁止），每条问题附带机理说明和替代建议。

**引出的新问题**：安全审查通过，实验做完了，实测结果和预测不一样怎么办？

---

### 第五层：结果分析与偏差诊断（ExperimentAnalysisService）

**问题**：实测燃速 9.6 mm/s，设计目标 12 mm/s，偏低 20%。研究者需要知道：偏差是多少？主要原因是什么？下一步应该怎么调整？这是含能材料研究中最需要专业判断的环节。

**方案**（`app/services/experiment_analysis.py`）：
- **偏差计算**：对比每个属性的预测值与实测值，计算偏差百分比和方向（偏高/偏低）。
- **文献驱动根因分析**：把偏差情况检索到文献知识库，结合已知机理生成诊断建议（如"燃速偏低 → AP 粒径过大 / 催化剂不足 / 固化不完全"）。
- **结构化报告**：按偏差严重程度分级，优先输出最需要关注的问题，并给出量化调整建议（"将 AP 粒径从 200μm 减小至 50μm，预计燃速提升 15-20%"）。

**引出的新问题**：分析报告有了，这次实验的数据能不能直接为下一轮设计所用？

---

### 第六层：实验数据写回知识库（闭环知识积累）

**问题**：每次实验产生的实测数据是比文献更可信的直接知识——文献值可能来自不同样品、不同工艺，而自己实验室的数据是针对自己配方的。如果这些数据只停留在报告里，下一轮设计仍然只能依赖文献。

**方案**：
- 分析完成后，将实测数据自动写回知识库，创建配方实体（`formulation:AP_HTPB_Al:<exp_id>`），属性标注 `source=experiment`。
- 后续设计检索时，来自实验的数据比来自文献的数据有更高的置信度权重。
- 知识库实体与实验记录双向关联，支持按实验 ID 追溯所有相关数据。

**引出的新问题**：有了历史实验数据，第二轮设计如何利用第一轮结果做出更有针对性的改进？

---

### 第七层：迭代改进闭环（多轮学习）

**问题**：单次实验不够，真实研究是多轮迭代的。理想状态是：第二轮设计自动感知到"上次燃速 9.6 mm/s，目标是 12 mm/s，偏低 20%"，然后有依据地调整——而不是每次都当作全新任务重新设计。

**方案**：
- **KB 感知设计**：`ExperimentDesignService` 检索时优先匹配 `source=experiment` 的已验证数据，第二轮目标"基于上次结果改进"会自动找到第一轮的实测记录。
- **迭代目标解析**：支持"提高燃速至 14-16 mm/s @7MPa"这类相对目标，自动与上轮数据对比计算需要的调整幅度。
- **评估验证（IL1/IL2）**：19 题评估中专门有两题测试迭代改进质量——第二轮方案是否引用了第一轮的实测数据，知识库写回是否准确。

**引出的新问题**：实验闭环跑通了，如何让外部客户端（Claude Desktop 等）直接调用这些能力？

---

### 第八层：对外集成（MCP + ToolRegistry + REST API）

**问题**：实验设计、安全审查、结果分析都是独立的能力，需要通过标准接口暴露，让 Claude Desktop 等外部 AI 客户端能够直接调用，同时保持 Agent 内部工具与 MCP 工具的 schema 一致。

**方案**：
- **MCP 协议**：FastMCP 暴露 12 个工具（含 `design_experiment`、`check_safety`、`log_experiment_result`），Claude Desktop 可以直接发现并调用实验设计全流程。
- **ToolRegistry**：`@ToolRegistry.register` 装饰器统一注册，Agent 和 MCP server 从同一份注册表读取 schema，永远不会不一致。
- **REST API**：`/experiments` 端点支持提交目标→查询状态→上传实测结果→获取分析报告的完整工作流。

**引出的新问题**：闭环跑通了，每次改动如何知道是进步还是退步？

---

### 第九层：闭环评估（实验员视角细粒度评测）

**问题**：没有量化指标，就不知道 BurningRateEstimator 的公式修正（`a*P^n` → `r₇*(P/7)^n`）到底让 LLM 预测改善了多少；不知道 SA1 的安全建议加了 Mg 替代方案后有没有被识别；更不知道下一步改什么最有价值。

**方案**（`scripts/run_experiment_eval_v2.py`）：
- **19 题 239 分**，覆盖实验闭环的每一个关键决策点：
  - 化学合理性（配方 OB%、TMD 是否正确）
  - 性能预测（燃速、密度、爆速是否在物理合理范围）
  - 工艺完整性（固化体系、TNT 熔铸步骤、感度测试规范）
  - 安全审查（危险对识别、误报率、替代建议）
  - 诊断方向（偏差根因分析是否指向正确方向）
  - 迭代改进（第二轮是否真正利用了第一轮数据）
- **化学硬指标自动核验**：数值类维度（燃速、密度、OB%）直接用 `app/eval/chemistry.py` 的物理模型计算，不依赖 LLM 裁判，结果可复现。
- **否定语境感知**：`_json_search_unnegated()` 在检查关键词时会跳过前 12 字符内含"不应/避免/禁止"等否定词的匹配，防止"不应减少 AP 用量"被误识别为"提高 AP 是正确建议"。
- **并行评估**：`--workers N` 使用 `ThreadPoolExecutor` 并发执行多题，Wall-clock 时间缩短约 4×（默认 4 线程）。LLM API 调用完全并行；DB 写操作在 SQLite WAL 模式下若遇到写锁竞争，会自动重试最多 3 次（指数退避 3/6/9s），避免 `PendingRollbackError`。
- **得分演进**：53.1% → 80.8% → 88% → **~96%**，每次改动后重跑评估，立刻看到效果。

---

### 功能与逻辑层的对应关系

```
根问题（实验闭环：目标→设计→安全→实验→分析→迭代）
   │
   ├─► 第一层：知识底座
   │     文献摄取 · 混合检索（RRF+HyDE）· 实体属性抽取 · 知识图谱
   │
   ├─► 第二层：实验设计 Agent
   │     目标解析 · KB 检索 · 配方生成 · Vieille 上下文注入
   │
   ├─► 第三层：硬约束性能预测
   │     BurningRateEstimator（r₇*(P/7)^n）· DensityEstimator（调和平均+识别率保护）· OB% · Kamlet-Jacobs
   │
   ├─► 第四层：安全门控
   │     相容性规则（每对附具体替代建议）· 感度清单 · 温度检查 · approved/needs_review/blocked
   │
   ├─► 第五层：结果分析
   │     偏差计算 · 文献驱动根因诊断 · 量化调整建议
   │
   ├─► 第六层：知识回写
   │     实测数据 → KB（source=experiment）· 配方实体创建 · 双向关联
   │
   ├─► 第七层：迭代改进
   │     KB 感知多轮设计 · 相对目标解析 · 跨轮数据引用
   │
   ├─► 第八层：对外集成
   │     MCP 12工具 · ToolRegistry · /experiments REST API
   │
   └─► 第九层：闭环评估
         19题239分 · 化学硬指标自动核验 · 否定语境感知 · --workers并行 · 53%→80.8%→96%
```

每一层都是上一层暴露出新问题的直接答案，没有凭空添加的功能。

---

## 核心特性

| 功能 | 说明 |
|------|------|
| **混合检索** | 关键词（ILIKE）+ 向量（BGE-M3）+ RRF 融合 + LLM Reranker |
| **HyDE 增强** | 生成假想文档段落辅助向量检索，弥合用词鸿沟 |
| **查询扩展** | 领域缩写词典 + LLM 同义词生成（最多 4 条查询） |
| **知识图谱** | 实体共现图 + 属性比较边，支持图谱感知检索扩展 |
| **规划 Agent** | LangGraph StateGraph，8 阶段流水线，内联引用 `[N]` |
| **置信度评分** | 基于证据数量、来源分散度、冲突检测给出 high/medium/low/none |
| **MCP 服务** | 12 个工具通过 FastMCP 协议暴露，供 Claude Desktop 直接调用 |
| **工具注册表** | `@ToolRegistry.register` 装饰器，新增工具零配置接入 |
| **摄取流水线** | 可插拔 `ExtractionStage`，支持规则 + LLM + 配方 + 章节摘要四路提取 |
| **章节记忆** | 摄取时为 parent chunk 生成 LLM 摘要 + 关键词，供导航 Agent 读大纲 |
| **Agentic 检索** | LLM 读章节大纲导航文档树，精准定位跨节证据，不再依赖向量最近邻 |
| **实验设计 Agent** | 输入研究目标，自动检索文献、推荐配方、预测属性（燃速/密度/比冲）、生成步骤，输出结构化方案 |
| **安全门控** | 规则引擎检查组分相容性（AP+S 等危险对）、感度、温度限制，输出 approved/needs_review/blocked，并给出具体替代建议 |
| **结果分析回写** | 上传实测值，自动对比预测、生成偏差分析报告，将验证数据写回知识库支持迭代改进 |
| **化学计算工具** | 8 个 chem_skills 工具：OB%、TMD/密度、Vieille 燃速、爆速/爆压（Kamlet-Jacobs）、RDKit 分子属性、PubChem 解析、相似物搜索、配方综合验证 |
| **TTL 查询缓存** | 重复查询 5 分钟内直接命中缓存，跳过全部 LLM 调用 |
| **流式 SSE 输出** | `start → plan → progress → token → done` 五段事件序列，实时推送阶段状态与逐 token 答案 |
| **Phase 2 三段式兜底** | 流式失败时依次尝试：重试流式（3s 后）→ 非流式兜底（分 80 字节块推送），全程不返回错误字符串 |
| **Phase 1 超时保护** | 使用显式 `httpx.Timeout(connect=10s, read=45s)` 防止 TCP keepalive 绕过超时导致 LLM 调用无限挂起 |
| **GraphRecursionError 恢复** | 图到达 recursion_limit 时从 `_partial` 中间状态提取已有证据，尝试生成降级答案 |
| **多轮对话** | 基于 `session_id` 的后端对话历史，上下文感知 |
| **PyroPlanner Chat UI** | Next.js 对话界面，含对话历史侧栏、流式状态指示、响应耗时显示、Markdown 渲染 |
| **对话历史（本地存储）** | 所有会话保存在 localStorage，支持恢复任意历史对话并继续多轮问答 |
| **实验 Agent 评估套件 v2** | 19 题 239 分，实验员视角细粒度评分，底层化学硬指标自动核验，当前 ~96% |

---

## 完整配置参考

### 后端（FastAPI + SQLite）

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp app/.env.example app/.env
# 编辑 app/.env，填入 LLM 地址和 API Key

# 3. 初始化数据库（新项目）
uv run python scripts/init_db.py

# 4. 如果是已有数据库，执行 schema 迁移
uv run python scripts/migrate_add_summaries.py
# 如遇 "no such column: documents.summary" 错误，在 SQLite 中执行：
# ALTER TABLE documents ADD COLUMN summary TEXT;

# 5. 摄取样例文档
uv run python scripts/ingest_sample.py

# 6. 运行测试
uv run pytest -q

# 7. 启动后端服务
uv run uvicorn app.api.main:app --reload --port 8000
```

打开 http://localhost:8000/docs 查看交互式 API 文档。

### 前端（PyroPlanner Chat UI）

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

打开终端输出的本地地址（默认 http://localhost:3000，被占用时自动改用 3001 等）使用 PyroPlanner 对话界面。

> **注意**：前端默认请求 `http://localhost:8000`，需确保后端服务已启动。如需修改后端地址，编辑 `frontend/lib/api.ts` 中的 `BASE_URL`。后端 CORS 已配置为允许所有 `localhost` 端口，无需手动更新白名单。

### 生产部署（PostgreSQL + pgvector）

```bash
# 启动容器（PostgreSQL + App）
docker compose -f deploy/docker-compose.yml up -d

# 建表 + pgvector 扩展 + HNSW 索引
docker compose -f deploy/docker-compose.yml exec app \
    uv run python scripts/setup_postgres.py

# 迁移旧 SQLite 数据（可选）
docker compose -f deploy/docker-compose.yml exec app \
    uv run python scripts/setup_postgres.py --backfill
```

`.env` 中的 PostgreSQL 配置：

```env
DATABASE_URL=postgresql+psycopg2://matplanner:matplanner@postgres:5432/matplanner
POSTGRES_USER=matplanner
POSTGRES_PASSWORD=matplanner
POSTGRES_DB=matplanner
```

### Claude Desktop 集成（MCP）

在 Claude Desktop 配置文件中添加：

```json
{
  "mcpServers": {
    "mat-planner": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

启动服务后，Claude Desktop 即可直接调用 12 个知识库工具。

---

## PyroPlanner Chat UI

PyroPlanner 是系统的对话式前端，基于 Next.js 构建，专为含能材料研究场景优化。

### 界面功能

| 功能 | 说明 |
|------|------|
| **流式状态指示** | 按 `正在理解问题… → 正在检索文献数据… → 正在深度推理… → 正在整理证据…` 序列实时反馈进度 |
| **检索计划展示** | 每条回答顶部展示 Agent 的工具调用计划（step + tool name） |
| **响应耗时显示** | 回答完成后在气泡底部显示总耗时，并细分为检索耗时和生成耗时，例如 `47s（检索 40s · 生成 7s）` |
| **Markdown 渲染** | 支持表格、代码块、数学公式（LaTeX）、标题层级等富文本格式 |
| **对话历史侧栏** | 左侧展示所有历史会话，显示标题、时间和消息数；点击可恢复并继续多轮对话 |
| **会话恢复** | 恢复历史会话时自动携带 `session_id`，后端保留上下文，支持无缝续问 |
| **新对话** | 点击右上角"新对话"清空当前会话，开始全新问答 |
| **内置示例** | 首页预设 4 个典型问题，点击即发送 |

### SSE 事件序列

后端 `/chat/stream` 接口以 SSE 格式推送五类事件：

```
{"type": "start"}
{"type": "plan", "plan": [...], "tool_names": [...], "confidence": "high", "phase1_secs": 42}
{"type": "progress", "phase": "generating", "message": "正在生成回答…"}
{"type": "token", "content": "RDX 的晶体密度为..."}
{"type": "done", "session_id": "xxx", "elapsed_secs": 49, "phase1_secs": 42}
```

| 事件 | 触发时机 |
|------|---------|
| `start` | 请求到达后立即发出，前端据此显示"正在理解问题…" |
| `plan` | LLM Planner 生成工具调用计划后发出；携带 `phase1_secs`（检索阶段耗时） |
| `progress` | 每个 Agent 节点（tool_executor / multihop / evidence_judge / generating）执行时发出 |
| `token` | Phase 2 LLM 生成答案时逐 token 推送 |
| `done` | 流结束，携带 `session_id`、`elapsed_secs`（总耗时）、`phase1_secs`（检索耗时）供前端展示 |

### 对话历史存储

所有会话以 `ConversationEntry` 格式保存在浏览器 `localStorage`（key: `pyroplanner_history`）：

```typescript
interface ConversationEntry {
  session_id: string   // 与后端 session 对应，用于续问
  title: string        // 首条用户消息（截取 80 字符）
  created_at: string   // ISO 时间戳
  messages: SavedMessage[]
}
```

最多保留 50 条历史；删除操作仅删除本地记录，后端 session 不受影响。

---

## 系统架构

```
┌────────────────────────────────────────────────────────────────┐
│              用户 / PyroPlanner (Next.js :3000)                │
└─────────────────────────┬──────────────────────────────────────┘
                          │ HTTP / SSE
                          ▼
┌────────────────────────────────────────────────────────────────┐
│                    FastAPI  REST API  (:8000)                   │
│  POST /chat  POST /chat/stream  POST /jobs  GET /documents     │
│  POST /retrieval/query  GET /graph  GET /entities              │
│  /mcp  ←──── FastMCP Server（MCP 协议，12 个工具）              │
└──────┬─────────────────────────────┬──────────────────────────┘
       │                             │
       ▼                             ▼
┌──────────────────────────┐  ┌──────────────────────────────────┐
│  规划 Agent（LangGraph） │  │    ToolRegistry（12 工具）        │
│                          │  │  search_memory                   │
│  ① 查询理解（LLM）        │  │  get_entity_card                 │
│  ② 子问题分解（并行）     │  │  trace_evidence                  │
│  ③ LLM Planner           │  │  find_tables                     │
│  ④ 工具执行器             │  │  compare_values                  │
│  ⑤ Multi-hop ReAct        │  │  get_source_snippet              │
│  ⑥ 证据裁判（置信度）     │  │  find_related_entities           │
│  ⑦ 答案构建（内联引用）   │  │  get_formulation                 │
│  ⑧ 自我反思与校验         │  │  agentic_search                  │
│  [Replanner] 空结果重查   │  │  design_experiment               │
│  [部分状态恢复] 递归降级  │  │  check_safety                    │
└──────┬───────────────────┘  │  log_experiment_result           │
       └──────────────────────┴──────────────┬───────────────────┘
                                             │ SQLAlchemy
                                             ▼
┌────────────────────────────────────────────────────────────────┐
│                      摄取流水线                                  │
│  文件 → Parser → SectionBuilder → ChunkBuilder                 │
│       → ExtractionPipeline（规则 + LLM NER + 配方）            │
│       → EmbeddingService → GraphBuilder                        │
└──────────────────────────────┬─────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────┐
│               数据库（SQLite / PostgreSQL）                     │
│  Document ─► Section ─► Chunk ─► DocumentAsset                │
│  DomainEntity ─► PropertyValue ─► EvidenceLink                │
│  MemoryGraphNode ─► MemoryGraphEdge                           │
│  RetrievalRun ─► RetrievalStep  │  Formulation                │
└────────────────────────────────────────────────────────────────┘
```

---

## 规划 Agent 详解

### 8 阶段流水线

| 阶段 | 驱动方式 | 功能说明 |
|------|----------|---------|
| **① 查询理解** | LLM（1次） | 分类问题类型（entity_property / comparison / table / formulation / general），提取实体名和属性名 |
| **② 子问题分解** | LLM（1次） | 检测复杂查询，并行 ThreadPoolExecutor 分解执行；触发后跳过③④ |
| **③ LLM Planner** | LLM（1次） | 生成 JSON 格式工具执行计划；子问题分解后跳过 |
| **④ 工具执行器** | 纯规则 | 按计划依次调用工具；子问题分解后跳过 |
| **⑤ Multi-hop ReAct** | LLM（1-2次） | 检查结果是否充分，决定是否补充工具调用（最多 2 轮） |
| **⑥ 证据裁判** | 规则 + LLM（1次） | 结构化提取数值证据，应用 6 条硬规则，计算置信度 |
| **⑦ 答案构建** | LLM（1次） | 基于有效证据生成答案，内联 `[1][2]…` 引用标记 |
| **⑧ 自我反思** | LLM（1次） | 检查单位一致性、幻觉、矛盾；发现问题自动修正 |
| **Replanner** | LLM（1次） | 仅在结果为空时触发，重新构造查询 |

**典型 LLM 调用次数：6–10 次**，由 `MULTIHOP_MAX_ROUNDS=2`、`retry_count`、`REFLECTION_ENABLED` 上界控制。

### 流式输出实现

Agent 使用 `compiled.stream(stream_mode="updates")` 执行，每个节点完成后立即 yield 进度事件，前端无需等待整个 Agent 流水线完成即可获得反馈。

**Phase 1 超时保护**：所有 Phase 1 LLM 调用使用显式 `httpx.Timeout(connect=10s, read=45s, write=10s, pool=5s)`，防止 API 代理通过 TCP keepalive 绕过简单的 `timeout=N` 浮点数配置导致调用无限挂起。

**Phase 2 三段式兜底**：答案构建使用独立 `stream_client`（`timeout=None`，`max_retries=2`）进行流式生成；若流式连接中途异常（0 tokens 时）：① 等待 3 秒后重试流式；② 若仍失败，切换非流式请求（`max_retries=3`）并将完整响应分 80 字节块推送。全程不向前端返回错误字符串，保证输出连续性。

**响应计时**：`_t_start` 在 `start` 事件前打点，`_t_phase1` 在 Phase 1 完成后打点；`done` 事件携带 `elapsed_secs`（总耗时）和 `phase1_secs`（检索耗时），前端气泡底部显示分段耗时。

### 规则路由（Planner 默认行为）

| query_type | 强制工具序列 |
|---|---|
| `entity_property` | 对每个实体调用 `get_entity_card` |
| `comparison` | 先调用 `compare_values`（一次汇总所有实体），≤2 实体才用 `get_entity_card` |
| `table` | `find_tables` + `search_memory` |
| `formulation` | `get_formulation` |
| `general` | `search_memory` |

### 6 条硬验证规则（证据裁判）

| 规则 | 触发条件 | 处理方式 |
|------|---------|---------|
| **Rule 1** | 无 evidence_id | 标记为 (unverified) |
| **Rule 2** | 属性值缺少单位 | 拒绝该值 |
| **Rule 3** | 检索结果为空 | 触发 Replanner 重新查询 |
| **Rule 4** | 多条冲突数值 | 答案中列出全部来源 |
| **Rule 5** | 表格类问题 | 强制 find_tables 进入工具序列 |
| **Rule 6** | 实体属性问题 | 强制 get_entity_card 优先执行 |

### 置信度评分

```
high   ≥ 3 条来源证据 + 数值分散度 < 5% + 无 CONFLICT 错误
medium 1-2 条证据，或分散度 5-20%
low    全部证据未经验证（无 evidence_id）
none   无任何证据，或结果为空
```

---

## API 接口

### 问答

```bash
# 普通问答
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "RDX的密度是多少？", "namespace": "default"}'

# 流式问答（SSE）
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "HMX的爆速和爆压？"}'

# 多轮对话
curl -X POST http://localhost:8000/chat \
  -d '{"question": "那它的爆速呢？", "session_id": "<上一轮返回的session_id>"}'
```

SSE 事件格式：

```
{"type": "start"}
{"type": "plan", "plan": [...], "tool_names": [...], "confidence": "high"}
{"type": "progress", "phase": "retrieving", "message": "正在检索文献数据…"}
{"type": "token", "content": "RDX 的晶体密度为..."}
{"type": "done", "session_id": "xxx", "confidence": "high"}
```

### 完整接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat` | 规划 Agent 问答（支持 session_id 多轮） |
| `POST` | `/chat/stream` | 流式问答（SSE） |
| `GET` | `/chat/sessions` | 列出所有活跃会话 |
| `GET` | `/chat/sessions/{id}` | 获取会话历史消息 |
| `DELETE` | `/chat/{session_id}` | 清除会话 |
| `POST` | `/jobs` | 提交摄取任务（异步，立即返回 202 + job_id） |
| `POST` | `/jobs/batch` | 批量提交（paths 列表 或 directory + glob） |
| `GET` | `/jobs` | 列出所有任务 |
| `GET` | `/jobs/{id}` | 查询任务状态（pending/processing/done/failed） |
| `WS` | `/jobs/{id}/ws` | WebSocket 实时进度推送 |
| `GET` | `/documents` | 列出文档（支持分页、namespace 过滤） |
| `GET` | `/documents/{id}` | 文档详情 + 摘要 + 统计 |
| `GET` | `/documents/{id}/sections` | 章节树 |
| `GET` | `/documents/{id}/chunks` | 分块列表（支持类型、命中次数过滤） |
| `DELETE` | `/documents/{id}` | 删除文档及所有派生数据 |
| `POST` | `/retrieval/query` | 直接检索（不经过 Agent） |
| `GET` | `/retrieval/runs` | 检索历史记录 |
| `GET` | `/retrieval/stats` | 检索统计（零结果查询、高频查询、分布） |
| `GET` | `/entities` | 实体列表 |
| `GET` | `/entities/{name}` | 实体卡片 + 属性证据 |
| `GET` | `/graph/nodes` | 知识图谱节点列表 |
| `GET` | `/graph/edges` | 知识图谱边列表 |
| `GET` | `/graph/neighbors/{label}` | 实体邻居 |
| `GET` | `/graph/stats` | 图谱统计 |
| `POST` | `/graph/rebuild` | 异步重建知识图谱 |
| `GET` | `/health` | 健康检查 |
| `*` | `/mcp` | FastMCP Server（MCP 协议） |

---

## MCP 工具

所有工具通过 `ToolRegistry` 统一注册，单一来源维护名称、描述和 OpenAI schema。

| 工具 | 说明 |
|------|------|
| `search_memory` | 混合检索（关键词 + 向量 + RRF），支持 namespace 过滤 |
| `get_entity_card` | 获取实体完整属性卡片（密度、爆速、燃速等，含来源证据） |
| `trace_evidence` | 溯源属性数值到原文引用位置（章节 + 页码 + 原文） |
| `find_tables` | 按关键词在文档中搜索数据表格（含 caption 匹配） |
| `compare_values` | 跨实体属性比较，返回均值/极值/排名（支持 namespace） |
| `get_source_snippet` | 按 chunk_id 获取完整原文段落 |
| `find_related_entities` | 知识图谱近邻查询（CO_OCCURS_WITH / COMPARED_BY_\*） |
| `get_formulation` | 查找包含某成分的复合推进剂/混合炸药配方 |
| `agentic_search` | 深度导航检索：LLM 读章节大纲，精准定位跨节证据（复杂问题首选） |
| `design_experiment` | 目标→文献检索→配方推荐→属性预测→步骤生成→安全检查，输出 1-3 个候选方案 |
| `check_safety` | 对任意配方执行安全审查，返回 approved / needs_review / blocked |
| `log_experiment_result` | 上传实测结果，生成偏差分析报告，将实验数据写回知识库 |

### 工具按 Skills 分组

工具按职责聚合到三个 skill 文件，不再逐工具建文件：

| 文件 | 包含工具 | 场景 |
|------|---------|------|
| `retrieval_skills.py` | search_memory · get_entity_card · trace_evidence · find_tables · compare_values · get_source_snippet · find_related_entities · get_formulation · agentic_search | 知识库检索 |
| `experiment_skills.py` | design_experiment · check_safety · log_experiment_result | 实验 Agent 闭环 |
| `chem_skills.py` | calc_oxygen_balance · calc_density · calc_burning_rate · calc_detonation_params · validate_formulation · … | 化学纯计算 |

### 新增工具（示例）

```python
# 在对应的 skill 文件里追加一个类
# e.g. app/tools/retrieval_skills.py
from app.tools.base import BaseTool, ToolResult
from app.tools.registry import ToolRegistry

@ToolRegistry.register
class MyTool(BaseTool):
    name = "my_tool"
    description = "工具描述，自动出现在 LLM 工具列表和 MCP schema 中"
    parameters = {
        "query": {"type": "string", "description": "查询参数"},
    }
    required = ["query"]

    def __init__(self, db):
        self.db = db

    def run(self, query: str, **kwargs) -> ToolResult:
        # 实现逻辑
        return ToolResult(success=True, data={"result": ...})
```

然后在 `app/mcp/server.py` 中加一个 `@mcp.tool()` 函数即可，其他地方零改动。

---

## 摄取流水线

### 支持格式

| 格式 | 解析器 |
|------|--------|
| `.md` | Markdown 解析器（标题感知分块） |
| `.pdf` | PyMuPDF + MinerU 结构化解析 |
| `.txt` | 纯文本解析器 |
| `.docx / .rst / .tex / .html` | 基础文本提取 |

### 流水线阶段

```
文件
 └─► Parser（格式解析）
 └─► SectionBuilder（章节层次树）
 └─► ChunkBuilder（分块：parent 4000字 + child 300字）
 └─► AssetExtractor（图表资产提取）
 └─► ExtractionPipeline（可插拔提取）
      ├── RulesExtractionStage  （正则 + 实体词典）
      ├── LLMExtractionStage    （LLM NER，去重合并）
      ├── FormulationStage      （配方/组成识别）
      └── ChunkSummaryStage     （parent chunk LLM 摘要 + 关键词）
 └─► EmbeddingService（BGE-M3 批量向量化）
 └─► GraphBuilder（知识图谱重建）
```

### 批量摄取

```bash
# 批量摄取目录
curl -X POST http://localhost:8000/jobs/batch \
  -H "Content-Type: application/json" \
  -d '{
    "directory": "/data/papers",
    "glob_pattern": "**/*.pdf",
    "recursive": true,
    "namespace": "propellants"
  }'

# 强制重新摄取（覆盖已存在的文档）
curl -X POST http://localhost:8000/jobs \
  -d '{"path": "/data/paper.pdf", "namespace": "default", "force": true}'
```

### 已覆盖实体与属性

**已知实体（66 种）**：RDX、HMX、TNT、PETN、CL-20、FOX-7、HTPB、AP、AN、Al、TATB、NTO、TKX-50、GAP、NG、NC、DNTF、ADN、DNAN 等

**抽取属性**：density（密度）、burning_rate（燃速）、detonation_velocity（爆速）、heat_of_explosion（爆热）、detonation_pressure（爆压）、melting_point（熔点）、particle_size（粒径）、oxygen_balance（氧平衡）、impact_sensitivity（撞击感度）、specific_impulse（比冲）等

---

## 检索系统

### 检索流程

```
查询
 ├── 查询扩展（领域缩写词典 + LLM 同义词，最多 4 条）
 ├── HyDE（生成假想文档段落，平均嵌入向量）
 ├── 关键词搜索（ILIKE，top_k×3 候选）
 ├── 向量搜索（BGE-M3，SQLite numpy / pgvector HNSW）
 ├── 文档摘要搜索（跨文档召回）
 ├── 3路 RRF 融合（击中次数加权）
 ├── LLM Reranker（listwise 相关性评分）
 ├── 章节 BFS 扩展（父节点 + 兄弟节点上下文）
 └── 知识图谱扩展（相关实体 chunk 补充）
```

### 性能优化

- **嵌入矩阵内存缓存**：首次加载后常驻进程（~35MB/9k chunk），ingestion 后自动失效
- **TTL 查询缓存**：相同查询 5 分钟内直接返回，跳过 HyDE + 扩展 + Reranker 等 LLM 调用
- **检索命中计数**：被引用的 chunk 在 RRF 中获得 log(hit_count)×0.05 加分
- **预取加速**：查询到达时立即在后台启动查询扩展和 HyDE 计算，与用户输入处理并行

---

## 评估框架

### 通用问答评估

```bash
# 准备评估语料
uv run python scripts/prepare_eval.py --data-dir /path/to/papers --limit 8

# 离线评估（直接调用 Agent，无需服务器）
uv run python scripts/run_eval.py --mode offline

# 对比两份评估报告
uv run python scripts/run_eval.py --compare eval/report_a.json eval/report_b.json
```

22 个问题，指标：**Faithfulness**（LLM 裁判）+ **Entity Recall**（实体关键词命中率）

### 实验 Agent 细粒度评估 v2

```bash
# 全量评估（19 题，~60-90 分钟）
uv run python scripts/run_experiment_eval_v2.py

# 并行评估（4 线程，~15-25 分钟）
uv run python scripts/run_experiment_eval_v2.py --workers 4

# 指定题目
uv run python scripts/run_experiment_eval_v2.py --ids PR1 PR2 DG1 EC2
```

| 维度 | 题目 | 分值 | 测试点 |
|------|------|------|--------|
| 化学合理性 | CV1/CV2 | 18 | OB%、TMD、质量分数 |
| 性能预测 | PR1/PR2/PR3 | 32 | 燃速（Vieille 基准）、密度、爆速 |
| 工艺完整性 | PC1/PC2/PC3 | 47 | 固化体系、TNT 熔铸、感度测试方案 |
| 安全审查 | SA1/SA2/SA3 | 30 | 危险对识别、误报率、温度风险 |
| 诊断方向 | DG1/DG2/DG3/DG4 | 56 | 燃速/密度/感度/压强指数偏差诊断 |
| 迭代改进 | IL1/IL2 | 28 | 跨轮数据引用、知识库写回 |
| 边缘情况 | EC1/EC2 | 28 | 矛盾目标识别、AP 排除设计 |

**评分底层**：`app/eval/chemistry.py` 提供 BurningRateEstimator（Vieille 定律，含粒径/催化剂修正）、DensityEstimator（调和平均 TMD）、OxygenBalanceCalculator、ProcessabilityChecker，所有数值维度均为自动化硬指标，不依赖 LLM 裁判。

**得分演进**：

| 版本 | 得分 | 关键改动 |
|------|------|---------|
| 基线 | 53.1% | 初始实现 |
| v1 fixes | 80.8% | 检索、知识库写回、EC2 AP 排除 |
| v2 fixes | 88% | BurningRateEstimator 公式修正（r7*(P/7)^n）、PR3 ground truth 修正、SA1 recommendation 修复、DG1 否定语境误判修复、DensityEstimator 识别率保护 |
| v3 fixes | **~96%** | `--workers` 并行评估 + SQLite 写锁重试（PendingRollbackError → 最多3次退避重试），消除 DB 锁崩溃导致的零分 |

---

## 项目结构

```
mat-planner/
├── app/
│   ├── api/
│   │   ├── main.py              # FastAPI 入口 + lifespan
│   │   └── routes/
│   │       ├── chat.py          # POST /chat, /chat/stream, 会话管理
│   │       ├── jobs.py          # 摄取任务（单个 + 批量 + WebSocket）
│   │       ├── documents.py     # 文档 CRUD + 章节 + 分块
│   │       ├── retrieval.py     # 检索查询 + 历史 + 统计
│   │       ├── entities.py      # 实体卡片
│   │       ├── graph.py         # 知识图谱浏览
│   │       └── health.py
│   ├── core/
│   │   ├── config.py            # 配置（pydantic-settings，含特性开关）
│   │   └── database.py          # SQLAlchemy 引擎 + 会话
│   ├── mcp/
│   │   └── server.py            # FastMCP Server（委托给 ToolRegistry）
│   ├── models/
│   │   ├── orm/                 # ORM 模型（Document、Chunk、Entity、Graph…）
│   │   └── schemas/             # Pydantic 响应 schema
│   ├── pipeline/
│   │   ├── base.py              # ExtractionContext + ExtractionStage ABC
│   │   ├── extraction.py        # ExtractionPipeline（可插拔提取阶段）
│   │   ├── parsers/             # Markdown / PDF / MinerU / Text 解析器
│   │   ├── router.py            # 按文件扩展名路由到解析器
│   │   ├── section_builder.py   # 章节树构建
│   │   ├── chunk_builder.py     # 层级分块（parent + child）
│   │   ├── domain_extractor.py  # 规则 + 词典实体属性抽取
│   │   ├── llm_ner.py           # LLM NER 抽取
│   │   ├── formulation_extractor.py  # 配方/组成识别
│   │   ├── asset_extractor.py   # 图表资产提取
│   │   ├── graph_builder.py     # 知识图谱构建
│   │   ├── summary_stage.py     # ChunkSummaryStage（章节摘要 + 关键词）
│   │   └── vlm_captioner.py     # VLM 图像标注
│   ├── eval/
│   │   └── chemistry.py         # 化学计算库：OB% / TMD / Vieille燃速 / 工艺检查 / 诊断验证
│   ├── services/
│   │   ├── agent.py             # 规划 Agent（LangGraph StateGraph + 流式SSE + 降级恢复）
│   │   ├── ingestion.py         # 摄取服务（编排流水线）
│   │   ├── retrieval.py         # 混合检索服务（含缓存 + 预取）
│   │   ├── agentic_retrieval.py # Agentic 三阶段导航检索服务
│   │   ├── experiment_design.py # 实验设计服务（目标解析→检索→配方→属性预测→步骤）
│   │   ├── experiment_analysis.py # 结果分析（预测 vs 实测偏差报告 + KB 写回）
│   │   ├── safety_checker.py    # 安全门控（组分相容性/感度/温度，输出建议替代方案）
│   │   ├── embedding.py         # BGE-M3 嵌入服务
│   │   ├── entity.py            # 实体查询服务
│   │   ├── session.py           # 会话存储
│   │   ├── job_store.py         # 任务状态存储
│   │   └── llm_client.py        # LLM 客户端工厂
│   └── tools/
│       ├── base.py              # BaseTool（含 openai_schema 自动生成）
│       ├── registry.py          # ToolRegistry（@register 装饰器）
│       ├── retrieval_skills.py  # 9个知识库检索工具（search_memory/get_entity_card/…/agentic_search）
│       ├── experiment_skills.py # 3个实验Agent工具（design_experiment/check_safety/log_experiment_result）
│       └── chem_skills.py       # 化学计算工具（OB%/密度/燃速/爆速/RDKit/PubChem/ISP/Cantera…）
├── frontend/                    # PyroPlanner Chat UI（Next.js）
│   ├── app/
│   │   ├── page.tsx             # 主页面：流式问答、会话管理
│   │   └── globals.css
│   ├── components/
│   │   ├── MessageBubble.tsx    # 消息渲染（Markdown + 状态指示 + 检索计划）
│   │   ├── Sidebar.tsx          # 对话历史侧栏（localStorage）
│   │   ├── PlanBar.tsx          # Agent 工具调用计划展示
│   │   └── ExperimentPanel.tsx
│   └── lib/
│       ├── api.ts               # SSE 流式请求封装（streamChat）
│       └── history.ts           # 对话历史 localStorage 读写
├── eval/
│   ├── eval_questions.json            # 22 个通用评估问题（v1）
│   ├── experiment_eval_questions.json # 16 个实验 Agent 评估问题（v1）
│   └── experiment_eval_v2.json        # 19 个实验员视角细粒度评估问题（v2，239 分）
├── sample_data/                 # 样例推进剂文献（.md）
├── scripts/
│   ├── init_db.py
│   ├── ingest_sample.py
│   ├── prepare_eval.py
│   ├── run_eval.py                    # 评估 + 对比报告
│   ├── run_experiment_eval.py         # 实验 Agent 评估脚本 v1（16 题）
│   ├── run_experiment_eval_v2.py      # 实验 Agent 评估脚本 v2（实验员视角，自动化评分）
│   ├── migrate_add_summaries.py       # 新增 summary/keywords 列（首次升级运行一次）
│   └── migrate_*.py                   # 其他数据迁移工具
├── tests/                       # pytest 测试套件
├── deploy/
│   └── local-dev/               # Docker Compose（dev 环境）
└── pyproject.toml
```

---

## 当前功能状态

| 功能 | 状态 |
|------|------|
| Markdown / TXT / PDF 摄取 | ✅ |
| MinerU JSON 格式摄取 | ✅ |
| 规则 + LLM NER 抽取 | ✅ |
| 表格感知属性抽取 | ✅ |
| 单位规范化（kbar→GPa，K→°C 等） | ✅ |
| 配方/组成识别（AP/HTPB/Al=68/20/12） | ✅ |
| VLM 图像标注 | ✅ |
| 关键词检索（SQLite ILIKE） | ✅ |
| 向量检索（BGE-M3，混合 RRF） | ✅ |
| HyDE 假想文档嵌入增强 | ✅ |
| 查询扩展（词典 + LLM） | ✅ |
| LLM Reranker（listwise 重排） | ✅ |
| 章节 BFS 上下文扩展 | ✅ |
| 知识图谱扩展检索 | ✅ |
| TTL 查询结果缓存（5 分钟） | ✅ |
| 预取加速（查询扩展 + HyDE 后台并行） | ✅ |
| PostgreSQL + pgvector HNSW | ✅ |
| 规划 Agent（LangGraph StateGraph） | ✅ |
| 8 阶段 Agent 流水线 | ✅ |
| 置信度评分（high/medium/low/none） | ✅ |
| 内联引用 `[N]`（精确到 chunk 级） | ✅ |
| 多轮对话（session history） | ✅ |
| 流式 SSE 输出（start/plan/progress/token/done） | ✅ |
| Phase 2 三段式兜底（重试流式 → 非流式 + 分块推送） | ✅ |
| Phase 1 超时保护（httpx 显式分阶段 Timeout，read=45s） | ✅ |
| 响应耗时显示（总耗时 + 检索/生成分段，气泡底部标签） | ✅ |
| GraphRecursionError 部分状态恢复 | ✅ |
| 子问题并行分解 | ✅ |
| 自我反思与校验 | ✅ |
| 批量摄取（paths 列表 / 目录 glob） | ✅ |
| 强制重新摄取（force=True） | ✅ |
| 异步摄取任务（WebSocket 进度） | ✅ |
| 12 个 MCP 工具（FastMCP 协议） | ✅ |
| ToolRegistry（工具零配置注册） | ✅ |
| ExtractionPipeline（可插拔提取阶段） | ✅ |
| 章节记忆（LLM 摘要 + 关键词，摄取时生成） | ✅ |
| Agentic 三阶段导航检索（Discovery→Navigation→Assembly） | ✅ |
| 实验设计 Agent（目标→文献检索→配方→属性预测→步骤→安全检查）| ✅ |
| 安全门控（规则引擎，approved/needs_review/blocked + 具体替代建议）| ✅ |
| 实验结果分析（预测 vs 实测偏差报告）| ✅ |
| 实验数据写回知识库（标注来源=实验）| ✅ |
| 迭代改进循环（第二轮方案引用第一轮实测数据）| ✅ |
| /experiments REST API（提交→查询→上传结果→分析报告）| ✅ |
| 化学计算工具（OB%/密度/Vieille燃速/Kamlet-Jacobs爆速/RDKit/PubChem）| ✅ |
| PyroPlanner Chat UI（Next.js，流式输出 + 状态指示）| ✅ |
| 对话历史侧栏（localStorage，支持恢复并续问）| ✅ |
| 实验 Agent 评估套件 v2（19 题 239 分，化学硬指标自动核验，~96%）| ✅ |
| 知识图谱 API（节点/边/邻居/重建） | ✅ |
| 检索统计 API（零结果/高频/分布） | ✅ |
| 通用问答评估套件（22 题，faithfulness + entity recall）| ✅ |
| 评估对比报告（--compare） | ✅ |
| 离线评估模式（CI 友好） | ✅ |
| GitHub Actions CI | ✅ |
