<p align="center">
  <img src="docs/assets/awe-agentcheck-hero.svg" alt="AWE-AgentForge" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge"><img alt="GitHub" src="https://img.shields.io/badge/GitHub-awe--agentforge-0f172a?style=for-the-badge&logo=github"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=Stars&color=fbbf24"></a>&nbsp;
  <a href="#"><img alt="Version" src="https://img.shields.io/badge/version-0.1.0-f59e0b?style=for-the-badge"></a>&nbsp;
  <a href="#"><img alt="Python" src="https://img.shields.io/badge/Python-3.10+-3b82f6?style=for-the-badge&logo=python&logoColor=white"></a>&nbsp;
  <a href="#"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/cloveric/awe-agentforge/ci.yml?style=for-the-badge&label=CI"></a>&nbsp;
  <a href="#"><img alt="License" src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="#"><img alt="Multi-Agent Collaboration" src="https://img.shields.io/badge/core-multi_agent_collaboration-06b6d4?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Bugfix and Review Loops" src="https://img.shields.io/badge/core-bugfix_%2B_review_loops-22c55e?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Self Evolution" src="https://img.shields.io/badge/core-self_evolution-8b5cf6?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Policy Guardrails" src="https://img.shields.io/badge/safety-policy_guardrails-f97316?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Ruff" src="https://img.shields.io/badge/code_style-ruff-d4aa00?style=flat-square"></a>
</p>

<br/>

<p align="center">
  <b>reviewer-first 的 vibe coder 控制塔：多智能体审阅、修复与进化一体化。</b><br/>
  <sub>让 Claude、Codex、Gemini 等 CLI 智能体在可审计的共识闭环中找 bug、做修复、再进化代码库。</sub>
</p>
<p align="center">
  <sub><b>低风险改名模式：</b>展示名使用 <code>AWE-AgentForge</code>，运行/包标识仍保持 <code>awe-agentcheck</code> / <code>awe_agentcheck</code> 以兼容现有脚本。</sub>
</p>

<p align="center">
  <a href="README.md">&#127468;&#127463; English</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="docs/RUNBOOK.md">运维手册</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="docs/ARCHITECTURE_FLOW.md">架构文档</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="CONTRIBUTING.md">贡献指南</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="SECURITY.md">安全策略</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#新手面板操作指南逐按钮解释">面板操作指南</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#项目热度stars">Stars</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#快速开始">快速开始</a>
</p>

<br/>

---

<br/>

## 最新更新（每日摘要）

| 日期 | 当日总结 |
|---|---|
| 2026-02-21 | 完成 service.py 大幅拆分（proposal_helpers / risk_assessment / git_operations / event_analysis），修复 Dashboard 头像渲染运行时错误，统一 runtime 归一化实现，修正 Docker 运行时依赖，并通过完整校验（ruff/mypy/pytest/bandit，覆盖率 90.28%）。 |
| 2026-02-20 | 完成 adapter 策略/工厂化、service layer 包化拆分、prompt 模板化与 LangGraph 按轮推进、Dashboard 模块化，以及 CI/治理/安全基线加固。 |
| 2026-02-19 | 完成 reviewer-first 与手动共识流程稳定化、preflight/precompletion/resume 护栏、benchmark + analytics 闭环，以及项目历史/PR 摘要集成。 |

详细变更时间线维护在 CHANGELOG.auto.md。

## 为什么选择 AWE-AgentForge？

<table>
<tr>
<td width="33%" align="center">

**多智能体协作**

让一个智能体负责实现，其他智能体负责审阅与反驳，在多轮交叉中收敛到可落地方案。

</td>
<td width="33%" align="center">

**Bug 修复引擎**

把模糊问题变成结构化流程：复现、修复、审阅、验证、门禁，重点是稳定解决真实缺陷。

</td>
<td width="33%" align="center">

**持续自我进化**

除了修 bug，还支持引导式/主动式进化，让智能体持续提出改进并验证质量收益。

</td>
</tr>
<tr>
<td width="33%" align="center">

**人工与策略控制**

作者审批、门禁判定、强制失败等机制确保高风险场景下仍由人类掌控节奏与边界。

</td>
<td width="33%" align="center">

**实时运维控制台**

在一个页面里看项目树、角色会话和对话流，并直接执行任务控制动作。

</td>
<td width="33%" align="center">

**可靠性与可观测性**

通过看门狗、降级切换、冷却策略、指标日志追踪，保障长时间自动运行可测量、可诊断。

</td>
</tr>
</table>

<br/>

## 系统架构

<p align="center">
  <img src="docs/assets/architecture-overview.svg" alt="系统架构" width="100%" />
</p>

<br/>

## 可视化总览

### 监控面板（Terminal Pixel 主题）

<p align="center">
  <img src="docs/assets/dashboard-preview.svg" alt="terminal pixel 多角色面板预览" width="100%" />
</p>

预览重点：

1. 终端像素风界面。
2. 高密度角色/会话列表（不止 2-3 个角色）。
3. 以对话和操作面板为中心的运行视图。

### 运行流程（清晰泳道版，箭头不穿过气泡）

<p align="center">
  <img src="docs/assets/workflow-flow.svg" alt="工作流" width="100%" />
</p>

<br/>

## 项目热度（Stars）

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=GitHub%20Stars&color=fbbf24"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/network/members"><img alt="GitHub forks" src="https://img.shields.io/github/forks/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=Forks&color=60a5fa"></a>
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge/stargazers">
    <img src="https://api.star-history.com/svg?repos=cloveric/awe-agentforge&type=Date" alt="Star History Chart" width="92%" />
  </a>
</p>

<br/>

## 核心概念

在开始使用之前，先了解以下核心概念：

### 参与者（Participants）

每个任务有一个 **作者**（author，负责写代码）和一个或多个 **审阅者**（reviewer，负责评审）。参与者使用 `provider#alias` 格式标识：

| 格式 | 含义 |
|:---|:---|
| `claude#author-A` | Claude CLI 担任作者角色，别名 "author-A" |
| `codex#review-B` | Codex CLI 担任审阅者角色，别名 "review-B" |
| `gemini#review-C` | Gemini CLI 担任第二审阅者，别名 "review-C" |

`provider` 决定调用哪个 CLI 工具（`claude`、`codex` 或 `gemini`）。`alias` 是在 Web 控制台和日志中显示的人类可读标签。

### 任务生命周期

每个任务遵循以下生命周期：

```
queued → running → passed / failed_gate / failed_system / canceled
```

在手动模式（`self_loop_mode=0`）中，会多一个等待状态：

```
queued → running → waiting_manual → (approve) → queued → running → passed/failed
                                  → (reject)  → canceled
```

这里的 `running` 在手动模式下表示“提案共识阶段”（`debate_mode=1` 时先 reviewer-first 预审），达标后才进入 `waiting_manual`。

### 三大控制参数

| 参数 | 可选值 | 默认值 | 作用 |
|:---|:---:|:---:|:---|
| `sandbox_mode` | `0` / `1` | **`1`** | `1` = 在隔离的 `*-lab` 副本中运行；`0` = 直接在主工作区运行 |
| `self_loop_mode` | `0` / `1` | **`0`** | `0` = 先跑提案共识轮，再暂停等待确认；`1` = 全自动实现/审查循环 |
| `auto_merge` | `0` / `1` | **`1`** | `1` = 通过后自动合并变更 + 生成变更日志；`0` = 结果保留在沙盒中 |

> [!TIP]
> **推荐的安全默认策略**：`sandbox_mode=1` + `self_loop_mode=0` + `auto_merge=1` — 沙盒执行 + 人工签核 + 通过后自动融合。

<br/>

## 快速开始

### 前置条件

- **Python 3.10+**
- **Claude CLI** 已安装并认证（用于 Claude 参与者）
- **Codex CLI** 已安装并认证（用于 Codex 参与者）
- **Gemini CLI** 已安装并认证（用于 Gemini 参与者）
- **PostgreSQL**（可选 — 不可用时自动降级为内存数据库）

### 第 1 步：安装

```bash
git clone https://github.com/cloveric/awe-agentforge.git
cd awe-agentforge
pip install -e .[dev]
# 可选：复制环境变量模板后再按需修改
cp .env.example .env
```

### 第 2 步：配置环境

系统需要知道工具的位置和连接方式。设置以下环境变量：

```powershell
# 必需：告诉 Python 源码位置
$env:PYTHONPATH="src"

# 可选：数据库连接（省略则使用内存模式）
$env:AWE_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck?connect_timeout=2"

# 可选：任务工件（日志、报告、事件）存储位置
$env:AWE_ARTIFACT_ROOT=".agents"

# 可选：工作流编排后端（langgraph/classic）
$env:AWE_WORKFLOW_BACKEND="langgraph"
```

也可以先复制 `.env.example`，再在当前 shell 中导出对应变量。

<details>
<summary><b>所有环境变量参考</b></summary>

| 变量 | 默认值 | 说明 |
|:---|:---|:---|
| `PYTHONPATH` | _(无)_ | 必须包含 `src/` 目录 |
| `AWE_DATABASE_URL` | `postgresql+psycopg://...?...connect_timeout=2` | PostgreSQL 连接字符串。数据库不可用时更快回退并自动降级内存模式 |
| `AWE_ARTIFACT_ROOT` | `.agents` | 任务工件目录（线程、事件、报告） |
| `AWE_CLAUDE_COMMAND` | `claude -p --dangerously-skip-permissions --effort low --model claude-opus-4-6` | Claude CLI 调用命令模板 |
| `AWE_CODEX_COMMAND` | `codex exec --skip-git-repo-check ... -c model_reasoning_effort=xhigh` | Codex CLI 调用命令模板 |
| `AWE_GEMINI_COMMAND` | `gemini --yolo` | Gemini CLI 调用命令模板 |
| `AWE_PARTICIPANT_TIMEOUT_SECONDS` | `3600` | 单个参与者（Claude/Codex/Gemini）每步最大运行秒数 |
| `AWE_COMMAND_TIMEOUT_SECONDS` | `300` | 测试/lint 命令最大运行秒数 |
| `AWE_PARTICIPANT_TIMEOUT_RETRIES` | `1` | 参与者超时后的重试次数 |
| `AWE_MAX_CONCURRENT_RUNNING_TASKS` | `1` | 可同时运行的任务数量 |
| `AWE_WORKFLOW_BACKEND` | `langgraph` | 工作流后端（推荐 `langgraph`，可回退 `classic`） |
| `AWE_ARCH_AUDIT_MODE` | _(随 evolution level 自动)_ | 架构审计执行级别：`off`、`warn`、`hard` |
| `AWE_ARCH_PYTHON_FILE_LINES_MAX` | `1200` | 架构审计中 Python 文件最大行数覆盖值 |
| `AWE_ARCH_FRONTEND_FILE_LINES_MAX` | `2500` | 架构审计中前端文件最大行数覆盖值 |
| `AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX` | `10` | 大型 Python 文件职责关键词命中上限 |
| `AWE_ARCH_SERVICE_FILE_LINES_MAX` | `4500` | `src/awe_agentcheck/service.py` 行数上限 |
| `AWE_ARCH_WORKFLOW_FILE_LINES_MAX` | `2600` | `src/awe_agentcheck/workflow.py` 行数上限 |
| `AWE_ARCH_DASHBOARD_JS_LINES_MAX` | `3800` | `web/assets/dashboard.js` 行数上限 |
| `AWE_ARCH_PROMPT_BUILDER_COUNT_MAX` | `14` | prompt 构建热点阈值 |
| `AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX` | `0` | adapter 运行时路径允许的 `RuntimeError` 次数上限 |
| `AWE_PROVIDER_ADAPTERS_JSON` | _(无)_ | 额外 provider 适配器 JSON 映射，例如 `{"qwen":"qwen-cli --yolo"}` |
| `AWE_PROMOTION_GUARD_ENABLED` | `true` | 在自动融合/轮次晋升前启用 promotion guard 检查 |
| `AWE_PROMOTION_ALLOWED_BRANCHES` | _(空)_ | 可选逗号分隔分支白名单（空表示不限制分支） |
| `AWE_PROMOTION_REQUIRE_CLEAN` | `false` | guard 启用时是否要求 git 工作区干净 |
| `AWE_SANDBOX_USE_PUBLIC_BASE` | `false` | 仅在显式设置为 `1/true` 时使用共享/公共沙盒根目录 |
| `AWE_API_ALLOW_REMOTE` | `false` | 是否允许非 loopback 远程访问 API（默认仅本机） |
| `AWE_API_TOKEN` | _(无)_ | API 鉴权 token（可选） |
| `AWE_API_TOKEN_HEADER` | `Authorization` | API token 使用的请求头 |
| `AWE_API_RATE_LIMIT_PER_MINUTE` | `120` | `/api/*` 按 client/path 的每分钟配额（`0` 表示关闭限流） |
| `AWE_DRY_RUN` | `false` | 设为 `true` 时不实际调用参与者 |
| `AWE_SERVICE_NAME` | `awe-agentcheck` | 可观测性中的服务名称 |
| `AWE_OTEL_EXPORTER_OTLP_ENDPOINT` | _(无)_ | OpenTelemetry 收集器端点 |

> [!NOTE]
> 若未设置 `AWE_DATABASE_URL` 且使用项目自带启动脚本，默认会落到本地 SQLite（`.agents/runtime/awe-agentcheck.sqlite3`），重启后历史可保留。仅在自定义启动路径下才可能退回内存数据库。
</details>

### 第 3 步：启动 API 服务

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/start_api.ps1" -ForceRestart
```

```bash
bash scripts/start_api.sh --force-restart
```

健康检查：

```powershell
(Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/healthz").Content
```

期望输出：

```json
{"status":"ok"}
```

安全停止 API：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/stop_api.ps1"
```

```bash
bash scripts/stop_api.sh
```

### 第 4 步：打开 Web 控制台

在浏览器中访问：

```
http://localhost:8000/
```

你会看到监控面板，包含：
- **左侧面板**：项目文件树 + 角色/会话列表
- **右侧面板**：任务控制、对话流、任务创建表单

## 新手面板操作指南（逐按钮解释）

第一次用，建议严格按下面顺序操作：

1. 先确认右上角显示 `API: ONLINE`。
2. 点一次 `Refresh`。
3. 在 `Dialogue Scope` 中选好 `Project` 和 `Task`。
4. 先看 `Conversation`，再做 `Start/Approve/Reject`。
5. `Force Fail` 只在任务明显卡死时使用。

### 顶部工具栏

| 控件 | 含义 | 什么时候用 |
|:---|:---|:---|
| `Refresh` | 立即拉取最新任务/统计/树/事件 | 看到数据没更新时 |
| `Auto Poll: OFF/ON` | 开关自动轮询刷新 | 任务运行中建议开 ON |
| `Theme` | 切换视觉风格（`Neon Grid`/`Terminal Pixel`/`Executive Glass`） | 纯显示偏好 |
| `API: ONLINE/RETRY(n)` | 后端健康状态 | 若 `RETRY`，先查服务日志 |

### 左侧：Project Structure（项目树）

| 控件 | 含义 | 什么时候用 |
|:---|:---|:---|
| `Expand` | 展开当前项目树里所有已加载目录 | 快速看全局结构 |
| `Collapse` | 收起目录 | 项目太大时降噪 |
| 树节点（`[D]` / `[F]`） | 目录 / 文件 | 确认任务针对的代码区域 |

### 左侧：Roles / Sessions（角色会话）

| 控件 | 含义 | 什么时候用 |
|:---|:---|:---|
| `all roles` | 展示完整混合对话流 | 默认总览视角 |
| 某个 `provider#alias` 行 | 过滤到单一角色会话 | 排查某个 CLI 的行为问题 |

### 右侧：Dialogue Scope + Task Controls

| 控件 | 含义 | 什么时候用 |
|:---|:---|:---|
| `Project` | 当前操作的项目范围 | 多仓并行时切换项目 |
| `Task` | 当前操作的任务 | 在同项目多个任务间切换 |
| `Force-fail reason` | 强制失败时写入的原因 | 点 `Force Fail` 前先填好 |
| `Start` | 启动当前 `queued` 任务 | 正常启动入口 |
| `Approve + Queue` | 在 `waiting_manual` 批准方案，但先不启动 | 先批准，稍后再跑 |
| `Approve + Start` | 在 `waiting_manual` 批准并立即执行 | 快速推进 |
| `Reject` | 在 `waiting_manual` 拒绝并取消任务 | 方案风险高或质量不足 |
| `Cancel` | 取消正在运行/排队任务 | 主动终止本轮 |
| `Force Fail` | 以指定原因标记 `failed_system` | 卡死时的兜底手段 |
| `Reload Dialogue` | 强制重拉当前任务事件流 | 对话疑似不完整时 |

### Conversation（对话区）

| 区域 | 含义 | 怎么读 |
|:---|:---|:---|
| 角色标签（如 `claude#author-A`） | 事件是谁发的 | 用来追责和定位来源 |
| 事件类型（如 `discussion`、`review`） | 当前工作流阶段 | 判断卡在哪个阶段 |
| 消息正文 | 原始或结构化事件内容 | 批准前先核对事实 |

### Create Task（创建任务）每个输入项

| 字段 | 含义 | 新手推荐值 |
|:---|:---|:---|
| `Title` | 任务名（UI/日志都会显示） | 简短且可辨识 |
| `Workspace path` | 仓库根目录路径 | 你的真实项目路径 |
| `Author` | 负责实现的角色 | `claude#author-A` / `codex#author-A` / `gemini#author-A` |
| `Reviewers` | 审阅者（逗号分隔） | 至少 1 个 |
| `Claude Model / Codex Model / Gemini Model` | 按提供者模型绑定（下拉可选 + 可编辑） | 建议从默认开始（`claude-opus-4-6`、`gpt-5.3-codex`、`gemini-3-pro-preview`） |
| `Claude/Codex/Gemini Model Params` | 每个提供者的附加参数（可选） | Codex 建议 `-c model_reasoning_effort=xhigh` |
| `Policy Template` | 一组预设执行策略（会一次性应用多项控制） | 建议先用 `deep-discovery-first`；要激进探索新功能/框架/UI 用 `frontier-evolve` |
| `Claude Team Agents` | 是否启用 Claude `--agents` 模式 | `0`（关闭） |
| `Evolution Level` | `0`仅修复，`1`引导进化，`2`主动进化，`3`前沿/激进进化 | 先用 `0` |
| `Repair Mode` | `minimal` / `balanced` / `structural` | 建议先用 `balanced` |
| `Max Rounds` | `self_loop_mode=0` 时为共识轮目标；`self_loop_mode=1` 时为无截止时间的重试上限 | `1` |
| `Evolve Until` | 可选截止时间（`YYYY-MM-DD HH:MM`） | 非夜跑可留空 |
| `Max Rounds` + `Evolve Until` | 优先级规则 | 若设置了 `Evolve Until`，以截止时间为准；为空时才使用 `Max Rounds` |
| `Conversation Language` | 对话输出语言（`en` / `zh`） | 英文日志优先选 `English`，中文协作选 `中文` |
| `Plain Mode` | 小白可读输出模式（`1` 开 / `0` 关） | 建议先用 `1` |
| `Stream Mode` | 参与者 stdout/stderr 实时流输出（`1` 开 / `0` 关） | 建议先用 `1` |
| `Debate Mode` | 启用 reviewer-first 预审/辩论阶段（`1` 开 / `0` 关） | 建议先用 `1` |
| `Sandbox Mode` | `1`沙盒 / `0`主仓 | 安全起见用 `1` |
| `Sandbox Workspace Path` | 自定义沙盒路径 | 建议留空（自动每任务独立） |
| `Self Loop Mode` | `0`手动审批 / `1`全自动 | 先用 `0` |
| `Auto Merge` | `1`通过后自动融合 / `0`关闭 | 建议先用 `1` |
| `Merge Target Path` | 通过后融合到哪里 | 项目根目录 |
| `Description` | 任务详细要求 | 写清验收标准 |

UI 策略说明：当 `Sandbox Mode = 0` 时，面板会强制 `Auto Merge = 0` 并锁定该选项。

策略模板速览：

- `deep-discovery-first`（默认）：发现优先审计风格，`evolution_level=2`。
- `frontier-evolve`：激进主动进化模式，`evolution_level=3`。
- `deep-evolve`：深度结构重构姿态，默认 `auto_merge=0`。
- `safe-review`：保守风控优先/人工偏好。
- `rapid-fix`：最快小修补姿态。

### 创建按钮

| 按钮 | 行为 | 适用场景 |
|:---|:---|:---|
| `Create` | 只创建任务（保持 queued） | 想先复核配置 |
| `Create + Start` | 创建并立即启动 | 当前配置已确认无误 |

### 新手安全默认组合

建议先固定这套：

- `Sandbox Mode = 1`
- `Self Loop Mode = 0`
- `Auto Merge = 1`
- Reviewer 数量 `>= 1`

操作节奏：`Create + Start` -> 等到 `waiting_manual` -> 查看 `Conversation` -> `Approve + Start` 或 `Reject`。

<br/>

### 第 5 步：创建第一个任务

可以通过 **Web UI**（面板底部的 "Create Task" 表单）或 **CLI** 创建任务：

```powershell
py -m awe_agentcheck.cli run `
  --task "修复登录验证的bug" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --conversation-language zh `
  --workspace-path "." `
  --auto-start
```

这个命令会：
1. 创建标题为 "修复登录验证的bug" 的任务
2. 指定 Codex 为作者，Claude 为审阅者
3. 使用默认策略（`sandbox_mode=1`, `self_loop_mode=0`, `auto_merge=1`）
4. 立即启动任务（`--auto-start`）
5. 由于 `self_loop_mode=0`，系统会先跑 reviewer-first 提案共识轮，然后在 `waiting_manual` 暂停等待你的确认

### 第 6 步：审批并执行（手动模式）

系统在 `waiting_manual` 暂停后，在 Web UI 或 CLI 中查看提案，然后批准：

```powershell
# 批准提案并立即开始执行
py -m awe_agentcheck.cli decide <task-id> --approve --auto-start
```

或者拒绝：

```powershell
# 拒绝提案（任务将被取消）
py -m awe_agentcheck.cli decide <task-id>
```

> [!IMPORTANT]
> 在手动模式下，任务**不会**进入实现阶段，直到你明确批准。这是设计使然 — 确保你对即将实现的内容拥有完全控制权。

<br/>

## CLI 参考

CLI 通过 HTTP 与 API 服务通信。使用前请确保服务已启动。

```
py -m awe_agentcheck.cli [--api-base URL] <command> [options]
```

全局选项：`--api-base`（默认：`http://127.0.0.1:8000`）— API 服务地址。

### `run` — 创建新任务

创建任务并可选地立即启动。

```powershell
py -m awe_agentcheck.cli run `
  --task "任务标题" `
  --description "要做什么的详细描述" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --conversation-language zh `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --auto-merge `
  --workspace-path "C:/path/to/your/project" `
  --max-rounds 3 `
  --test-command "py -m pytest -q" `
  --lint-command "py -m ruff check ." `
  --auto-start
```

| 参数 | 必需 | 默认值 | 说明 |
|:---|:---:|:---|:---|
| `--task` | 是 | — | 任务标题（显示在 UI 和日志中） |
| `--description` | 否 | 同 `--task` | 给 AI 参与者的详细描述 |
| `--author` | 是 | — | 作者参与者，格式 `provider#alias` |
| `--reviewer` | 是 | — | 审阅者参与者（可重复添加多个） |
| `--sandbox-mode` | 否 | `1` | `1` = 沙盒执行，`0` = 主工作区 |
| `--sandbox-workspace-path` | 否 | 自动生成 | 自定义沙盒目录路径 |
| `--self-loop-mode` | 否 | `0` | `0` = 手动审批，`1` = 全自动 |
| `--auto-merge` / `--no-auto-merge` | 否 | 开启 | 通过后是否自动融合 |
| `--merge-target-path` | 否 | 项目根目录 | 变更合并回哪个目录 |
| `--workspace-path` | 否 | `.` | 目标仓库路径 |
| `--max-rounds` | 否 | `3` | 手动模式：共识轮目标；自动模式：无截止时间时的门禁重试上限 |
| `--test-command` | 否 | `py -m pytest -q` | 测试命令 |
| `--lint-command` | 否 | `py -m ruff check .` | 代码检查命令 |
| `--evolution-level` | 否 | `0` | `0` = 仅修复，`1` = 引导进化，`2` = 主动进化 |
| `--repair-mode` | 否 | `balanced` | 修复策略（`minimal` / `balanced` / `structural`） |
| `--evolve-until` | 否 | — | 进化截止时间（如 `2026-02-13 06:00`） |
| `--conversation-language` | 否 | `en` | 智能体输出语言（`en` 或 `zh`） |
| `--plain-mode` / `--no-plain-mode` | 否 | 开启 | 开关小白可读输出模式 |
| `--stream-mode` / `--no-stream-mode` | 否 | 开启 | 开关实时流事件输出 |
| `--debate-mode` / `--no-debate-mode` | 否 | 开启 | 开关 reviewer-first 预审/辩论阶段 |
| `--provider-model` | 否 | — | 按提供者指定模型，格式 `provider=model`（可重复） |
| `--provider-model-param` | 否 | — | 按提供者传递额外参数，格式 `provider=args`（可重复） |
| `--claude-team-agents` | 否 | `0` | `1` 时为 Claude 参与者启用 `--agents` 模式 |
| `--auto-start` | 否 | `false` | 创建后立即启动 |

### `decide` — 提交作者决定

在手动模式下，用于在 `waiting_manual` 状态批准或拒绝提案。

```powershell
# 批准并立即启动
py -m awe_agentcheck.cli decide <task-id> --approve --auto-start

# 仅批准不启动（任务进入 queued）
py -m awe_agentcheck.cli decide <task-id> --approve

# 拒绝（任务被取消）
py -m awe_agentcheck.cli decide <task-id>

# 批准并附加备注
py -m awe_agentcheck.cli decide <task-id> --approve --note "方案可行，继续执行" --auto-start
```

### `status` — 查看任务详情

```powershell
py -m awe_agentcheck.cli status <task-id>
```

返回完整的任务 JSON，包括状态、已完成轮次、门禁原因等。

### `tasks` — 列出所有任务

```powershell
py -m awe_agentcheck.cli tasks --limit 20
```

### `stats` — 查看聚合统计

```powershell
py -m awe_agentcheck.cli stats
```

返回通过率、失败分桶、提供者错误计数和平均任务耗时。

### `analytics` — 查看高级分析

```powershell
py -m awe_agentcheck.cli analytics --limit 300
```

返回失败分类/趋势和 reviewer 偏移指标，便于可观测性分析。

### `policy-templates` — 获取推荐策略模板

```powershell
py -m awe_agentcheck.cli policy-templates --workspace-path "."
```

返回仓库规模/风险画像及推荐控制项组合。

### `benchmark` — 运行固定 A/B 基准回归

```powershell
py -m awe_agentcheck.cli benchmark `
  --workspace-path "." `
  --variant-a-name "baseline" `
  --variant-b-name "candidate" `
  --reviewer "claude#review-B"
```

运行固定基准任务集，并将 JSON/Markdown 报告输出到 `.agents/benchmarks/`。

### `github-summary` — 生成 PR 可用摘要

```powershell
py -m awe_agentcheck.cli github-summary <task-id>
```

返回可直接粘贴到 GitHub PR 的 markdown 摘要和工件链接。

### `start` — 启动已有任务

```powershell
py -m awe_agentcheck.cli start <task-id>
py -m awe_agentcheck.cli start <task-id> --background
```

### `cancel` — 取消任务

```powershell
py -m awe_agentcheck.cli cancel <task-id>
```

### `force-fail` — 强制失败

```powershell
py -m awe_agentcheck.cli force-fail <task-id> --reason "手动中止：分支错误"
```

### `promote-round` — 提升单轮结果（多轮手动模式）

```powershell
py -m awe_agentcheck.cli promote-round <task-id> --round 2 --merge-target-path "."
```

适用于 `max_rounds>1` 且 `auto_merge=0` 的任务，将指定轮次快照融合到目标路径。

### `events` — 查看任务事件

```powershell
py -m awe_agentcheck.cli events <task-id>
```

返回任务的完整事件时间线（讨论、审查、验证、门禁结果等）。

### `tree` — 查看工作区文件树

```powershell
py -m awe_agentcheck.cli tree --workspace-path "." --max-depth 4
```

<br/>

## 使用示例

### 示例 1：安全手动审查（推荐首次使用）

最保守的方式 — 沙盒执行 + 手动审批：

```powershell
py -m awe_agentcheck.cli run `
  --task "改善 API 层的错误处理" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --workspace-path "." `
  --auto-start
```

流程说明：
1. 系统创建隔离沙盒工作区（`awe-agentcheck-lab/20260213-...`）
2. 审阅者先进行预审并挑战方案（reviewer-first 阶段）
3. 作者修订方案后，审阅者再次评估并确认共识
4. 任务在 `waiting_manual` 暂停 — 你在 Web UI 中查看
5. 你批准 → 系统运行实现 → 审阅者审查代码 → 测试 + lint → 门禁决定
6. 如果通过：变更自动合并回主工作区，附带变更日志

### 示例 2：全自动夜间运行

适合无人值守运行（请确保你信任安全控制）：

```powershell
py -m awe_agentcheck.cli run `
  --task "夜间连续改进" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --sandbox-mode 1 `
  --self-loop-mode 1 `
  --max-rounds 5 `
  --workspace-path "." `
  --auto-start
```

流程说明：
1. Codex（作者）直接进入工作流循环 — 无手动检查点
2. 每轮：讨论 → 实现 → 审查 → 验证 → 门禁
3. 门禁通过：完成。失败：重试最多 5 轮
4. 通过后结果自动合并回来

### 示例 3：不自动合并（结果保留在沙盒）

当你想手动审查变更再决定是否合并：

```powershell
py -m awe_agentcheck.cli run `
  --task "实验性重构" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --workspace-path "." `
  --no-auto-merge `
  --auto-start
```

流程说明：
1. 一切照常运行，但通过后变更保留在沙盒中
2. 你可以手动查看沙盒目录，自行决定如何合并

### 示例 4：直接在主工作区运行（无沙盒）

当你希望变更直接应用到主工作区：

```powershell
py -m awe_agentcheck.cli run `
  --task "快速修复：README 拼写错误" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --sandbox-mode 0 `
  --self-loop-mode 1 `
  --workspace-path "." `
  --auto-start
```

> [!WARNING]
> 使用 `sandbox_mode=0` 时，变更直接写入你的工作区。仅用于低风险任务或有 git 可回退的场景。

<br/>

## API 参考

所有端点在 `http://localhost:8000` 提供服务。请求/响应体为 JSON 格式。

### 创建任务

```
POST /api/tasks
```

<details>
<summary>请求体</summary>

```json
{
  "title": "修复登录验证 bug",
  "description": "邮箱验证器接受了无效格式",
  "author_participant": "claude#author-A",
  "reviewer_participants": ["codex#review-B"],
  "conversation_language": "zh",
  "provider_models": {
    "claude": "claude-opus-4-6",
    "codex": "gpt-5.3-codex"
  },
  "provider_model_params": {
    "codex": "-c model_reasoning_effort=xhigh"
  },
  "claude_team_agents": false,
  "sandbox_mode": true,
  "self_loop_mode": 0,
  "auto_merge": true,
  "workspace_path": ".",
  "max_rounds": 3,
  "test_command": "py -m pytest -q",
  "lint_command": "py -m ruff check .",
  "auto_start": true
}
```
</details>

<details>
<summary>响应 (201)</summary>

```json
{
  "task_id": "task-abc123",
  "title": "修复登录验证 bug",
  "status": "queued",
  "sandbox_mode": true,
  "self_loop_mode": 0,
  "auto_merge": true,
  "rounds_completed": 0,
  ...
}
```
</details>

### 所有端点

| 方法 | 端点 | 说明 |
|:---:|:---|:---|
| `POST` | `/api/tasks` | 创建新任务 |
| `GET` | `/api/tasks` | 列出所有任务（`?limit=100`） |
| `GET` | `/api/tasks/{id}` | 获取任务详情 |
| `POST` | `/api/tasks/{id}/start` | 启动任务（`{"background": true}` 异步执行） |
| `POST` | `/api/tasks/{id}/cancel` | 请求取消任务 |
| `POST` | `/api/tasks/{id}/force-fail` | 强制失败 `{"reason": "..."}` |
| `POST` | `/api/tasks/{id}/promote-round` | 将指定轮次融合到目标路径（要求 `max_rounds>1` 且 `auto_merge=0`） |
| `POST` | `/api/tasks/{id}/author-decision` | 手动模式下批准/拒绝：`{"approve": true, "auto_start": true}` |
| `GET` | `/api/tasks/{id}/events` | 获取完整事件时间线 |
| `POST` | `/api/tasks/{id}/gate` | 提交手动门禁结果 |
| `GET` | `/api/provider-models` | 获取提供者模型目录（供 UI 下拉使用） |
| `GET` | `/api/policy-templates` | 获取仓库画像与推荐控制策略模板 |
| `GET` | `/api/analytics` | 获取失败分类/趋势与 reviewer 偏移分析 |
| `GET` | `/api/tasks/{id}/github-summary` | 生成 GitHub/PR 可用摘要 |
| `GET` | `/api/project-history` | 项目级历史记录（`core_findings` / `revisions` / `disputes` / `next_steps`） |
| `POST` | `/api/project-history/clear` | 清理指定范围历史（可选同时清理匹配的 live task） |
| `GET` | `/api/workspace-tree` | 文件树（`?workspace_path=.&max_depth=4`） |
| `GET` | `/api/stats` | 聚合统计（通过率、耗时、失败分桶） |
| `GET` | `/healthz` | 健康检查 |

<br/>

## 能力矩阵

| 能力 | 说明 | 状态 |
|:---|:---|:---:|
| **沙盒优先执行** | 默认 `sandbox_mode=1`，运行在 `*-lab` 工作区，自动生成每任务隔离沙盒 | `GA` |
| **作者确认门** | 默认 `self_loop_mode=0`，在 reviewer-first 提案共识轮后进入 `waiting_manual` | `GA` |
| **全自动自循环** | `self_loop_mode=1`，适合无人值守运行 | `GA` |
| **自动融合** | 通过后：合并 + `CHANGELOG.auto.md` + 快照 | `GA` |
| **提供者模型绑定** | 每任务按 `claude` / `codex` / `gemini` 指定模型 | `GA` |
| **Claude Team Agents 模式** | 每任务开关 Claude `--agents` 行为 | `GA` |
| **多角色模型** | `provider#alias` 参与者（跨模型或同模型多会话） | `GA` |
| **Web 监控控制台** | 项目树、角色区、头像化对话、任务控制、拖放 | `GA` |
| **项目历史账本** | 按项目沉淀跨任务时间线（发现/修订/争议/下一步） | `GA` |
| **多主题 UI** | Neon Grid、Terminal Pixel、Executive Glass | `GA` |
| **可观测性链路** | OpenTelemetry、Prometheus、Loki、Tempo、Grafana | `GA` |
| **夜间监督脚本** | 看门狗超时、提供者降级、冷却控制、单实例锁 | `GA` |

<br/>

## 工作流程详解

### 手动模式（`self_loop_mode=0` — 默认）

推荐大多数场景使用：

1. **创建任务** → 状态变为 `queued`
2. **启动任务** → 系统运行提案共识轮：
   - 若 `debate_mode=1`，先由审阅者做预审（`proposal_precheck_review`）
   - 作者基于反馈修订提案
   - 审阅者进行提案评审（`proposal_review`）
3. **共识规则**：
   - 仅当所有必需审阅者都给出通过级结论时，才计为一轮共识完成
   - 同一轮内会持续重试直到对齐，但现在有 10 次重试停滞保护（`proposal_consensus_stalled_in_round`）
   - 若跨轮反复围绕同一问题，则有 4 轮重复停滞保护（`proposal_consensus_stalled_across_rounds`）
4. **等待人工** → 达到目标共识轮后，状态变为 `waiting_manual`
5. **作者决定**：
   - **批准** → 状态变为 `queued`（原因为 `author_approved`），然后立即重新启动进入完整工作流
   - **拒绝** → 状态变为 `canceled`
6. **完整工作流** 运行：reviewer-first 辩论（可选）→ 作者讨论 → 作者实现 → 审阅者审查 → 验证（测试 + lint）→ 门禁决定
7. **门禁结果**：
   - **通过** → `passed` → 自动融合（合并 + 变更日志 + 快照 + 沙盒清理）
   - **失败** → 重试下一轮；若设置 `Evolve Until` 则由截止时间控制，否则由 `max_rounds` 控制，最终 `failed_gate`

### 自动模式（`self_loop_mode=1`）

适合无人值守运行：

1. **创建任务** → `queued`
2. **启动任务** → 直接进入完整工作流（无手动检查点）
3. **第 1..N 轮**：reviewer-first 辩论（可选）→ 作者讨论 → 作者实现 → 审查 → 验证 → 门禁
4. **门禁结果**：
   - **通过** → `passed` → 自动融合
   - **失败** → 持续重试，直到达到 `Evolve Until`（若设置）或 `max_rounds`（未设置截止时间）→ `failed_gate`

### 自动融合细节

当任务通过且 `auto_merge=1` 时：

1. 变更文件从沙盒复制到主工作区
2. 追加 `CHANGELOG.auto.md` 变更摘要
3. 快照保存到 `.agents/snapshots/`
4. 系统自动生成的沙盒被清理
5. 写入 `auto_merge_summary.json` 工件

<details>
<summary><b>沙盒生命周期细节</b></summary>

1. 不手动指定 `sandbox_workspace_path` 时，系统为每个任务创建唯一沙盒：`<project>-lab/<时间戳>-<id>/`
2. 沙盒是项目的过滤副本（排除 `.git`、`.venv`、`node_modules`、`__pycache__` 等）
3. 任务通过且自动融合完成后，系统生成的沙盒自动清理
4. 如果你手动指定了 `sandbox_workspace_path`，默认保留不删除
</details>

<br/>

## 路线图

### 2026 Q1 &nbsp; <img src="https://img.shields.io/badge/状态-已完成-22c55e?style=flat-square" alt="已完成"/>

- [x] 沙盒优先默认策略
- [x] 作者确认门
- [x] 自动融合 + 变更日志 + 快照
- [x] 监控页多主题与角色视角

### 2026 Q2 &nbsp; <img src="https://img.shields.io/badge/状态-已完成-22c55e?style=flat-square" alt="已完成"/>

- [x] GitHub / PR 深度联动（任务工件回链）
- [x] 按仓库体量和风险级别的策略模板
- [x] 扩展 Claude/Codex/Gemini 之外的更多参与者适配器

### 2026 Q3 &nbsp; <img src="https://img.shields.io/badge/状态-已完成-22c55e?style=flat-square" alt="已完成"/>

- [x] 沙盒到主仓的策略化自动晋升流水线
- [x] 更高级的质量趋势分析和评审偏移检测

<br/>

## 文档

| 文档 | 说明 |
|:---|:---|
| [`README.md`](README.md) | 英文文档 |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 运维手册 & 命令参考 |
| [`docs/ARCHITECTURE_FLOW.md`](docs/ARCHITECTURE_FLOW.md) | 系统架构深度解析 |
| [`docs/API_EXPOSURE_AUDIT.md`](docs/API_EXPOSURE_AUDIT.md) | 本地/API 暴露审计与防护建议 |
| [`docs/TESTING_TARGET_POLICY.md`](docs/TESTING_TARGET_POLICY.md) | 测试策略 & 方针 |
| [`docs/GITHUB_ABOUT.md`](docs/GITHUB_ABOUT.md) | GitHub About/描述建议文案（中英） |
| [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) | 会话交接记录 |

<br/>

## 开发

```bash
# 代码检查
py -m ruff check .

# 运行测试
py -m pytest -q
```

<br/>

## 贡献

欢迎贡献！请确保：

1. 代码通过 `ruff check .` 无告警
2. 所有测试通过 `pytest -q`
3. 新功能包含适当的测试覆盖

<br/>

## 许可证

MIT

<br/>

---

<p align="center">
  <sub>为需要结构化、可观测、安全的多模型代码审查工作流的团队而构建。</sub>
</p>

