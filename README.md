<p align="center">
  <img src="docs/assets/awe-agentcheck-hero.svg" alt="awe-agentcheck hero" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentcheck"><img alt="repo" src="https://img.shields.io/badge/repo-awe--agentcheck-0f172a?style=for-the-badge"></a>
  <a href="#"><img alt="python" src="https://img.shields.io/badge/python-3.11+-2563eb?style=for-the-badge&logo=python&logoColor=white"></a>
  <a href="#"><img alt="fastapi" src="https://img.shields.io/badge/FastAPI-control--plane-0ea5a4?style=for-the-badge&logo=fastapi&logoColor=white"></a>
  <a href="#"><img alt="mode" src="https://img.shields.io/badge/default-sandbox--first-1d4ed8?style=for-the-badge"></a>
  <a href="#"><img alt="mode" src="https://img.shields.io/badge/default-author--approval-f97316?style=for-the-badge"></a>
  <a href="#"><img alt="obs" src="https://img.shields.io/badge/observability-otel%20%7C%20prom%20%7C%20loki%20%7C%20tempo-16a34a?style=for-the-badge"></a>
</p>

<p align="center">
  Professional multi-CLI orchestration platform for author/reviewer agent collaboration.<br/>
  面向多 CLI 代理协作（作者/审阅者）的专业级编排平台。
</p>

<p align="center">
  <a href="#english">English</a> | <a href="#中文">中文</a>
</p>

---

## English

### What It Is

`awe-agentcheck` is a control plane for running structured multi-agent engineering loops:

`discussion -> implementation -> review -> verification -> gate`.

It is built for scenarios like:

- Codex reviews Claude output
- Claude reviews Codex output
- same CLI, different sessions cross-check each other
- unattended overnight evolution with observability and fail-safe controls

### Why It Feels Production-Grade

- Sandbox-first by default: tasks run in `*-lab` workspace (`sandbox_mode=1`)
- Author-confirmed by default: proposal/review first, then wait approval (`self_loop_mode=0`)
- Optional autonomous mode: full unattended execution (`self_loop_mode=1`)
- Auto-fusion pipeline (optional, default on):
  - merge changed files to target
  - generate `CHANGELOG.auto.md`
  - generate snapshot archive
- Web operator console with:
  - project tree
  - role/session panel
  - dialogue stream
  - manual controls (start/cancel/force-fail/approve/reject)
  - themes (`Neon Grid`, `Terminal Pixel`, `Executive Glass`)
- End-to-end observability stack: OTel + Prometheus + Loki + Tempo + Grafana

### Architecture

```mermaid
flowchart TD
    A[Task Create Request] --> B[FastAPI Control Plane]
    B --> C[OrchestratorService]
    C --> D[WorkflowEngine]
    D --> E[Author CLI]
    D --> F[Reviewer CLI(s)]
    D --> G[Test + Lint Verification]
    G --> H[Medium Gate]
    H -->|passed + auto_merge=1| I[Auto Fusion]
    I --> J[CHANGELOG.auto.md + Snapshot]
    H -->|self_loop_mode=0| K[WAITING_MANUAL]
    K --> L[Author Decision]
    L -->|approve| D
    L -->|reject| M[Canceled]
    C --> N[Artifacts + Events]
    C --> O[Stats API]
```

### Quick Start (Windows / PowerShell)

```powershell
cd C:/Users/hangw/awe-agentcheck
py -m pip install -e .[dev]
```

Start API:

```powershell
$env:AWE_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck"
$env:AWE_ARTIFACT_ROOT="C:/Users/hangw/awe-agentcheck/.agents"
$env:AWE_CLAUDE_COMMAND="claude -p --dangerously-skip-permissions --effort low"
$env:AWE_CODEX_COMMAND="codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low"
$env:AWE_PARTICIPANT_TIMEOUT_SECONDS="240"
$env:AWE_COMMAND_TIMEOUT_SECONDS="300"
$env:AWE_PARTICIPANT_TIMEOUT_RETRIES="1"
$env:AWE_MAX_CONCURRENT_RUNNING_TASKS="1"
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py -m uvicorn awe_agentcheck.main:app --reload --port 8000
```

Open monitor:

- `http://localhost:8000/`

### Core Task Modes

1. `sandbox_mode`
   - `1` default, run in lab workspace
   - `0` run directly in project workspace
2. `self_loop_mode`
   - `0` default, wait author decision before implementation
   - `1` autonomous loop
3. `auto_merge`
   - `1` default, auto fusion + changelog + snapshot on pass
   - `0` keep outputs isolated

### CLI Examples

Default policy (sandbox + author approval):

```powershell
py -m awe_agentcheck.cli run `
  --task "Improve monitor signal quality" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

Author approves and starts:

```powershell
py -m awe_agentcheck.cli decide task-1 --approve --auto-start
```

Autonomous direct-main run:

```powershell
py -m awe_agentcheck.cli run `
  --task "Autonomous pass" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --sandbox-mode 0 `
  --self-loop-mode 1 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

### API Surface

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/start`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/force-fail`
- `POST /api/tasks/{task_id}/author-decision`
- `GET /api/tasks/{task_id}/events`
- `GET /api/workspace-tree`
- `GET /api/stats`

### Project Docs

- `docs/RUNBOOK.md`
- `docs/ARCHITECTURE_FLOW.md`
- `docs/TESTING_TARGET_POLICY.md`
- `docs/SESSION_HANDOFF.md`
- `docs/plans/2026-02-13-sandbox-and-author-gate.md`

---

## 中文

### 项目定位

`awe-agentcheck` 是一个多 CLI 代理协作的编排控制台，核心工作流为：

`讨论 -> 实现 -> 审阅 -> 验证 -> 门禁`

典型场景：

- Claude 写，Codex 审
- Codex 写，Claude 审
- 同一 CLI 的不同 session 互相交叉检查
- 夜间无人值守连续进化（带可观测性与故障保护）

### 为什么它更“专业”

- 默认沙盒优先：`sandbox_mode=1`，任务默认跑在 `*-lab`
- 默认作者确认：`self_loop_mode=0`，先产出方案，再由作者决定是否执行
- 可切全自动：`self_loop_mode=1`，适合夜间连续运行
- 自动融合机制（可选，默认开）：
  - 自动合并变更
  - 自动写 `CHANGELOG.auto.md`
  - 自动打快照
- Web 端具备运维视角：
  - 项目树 / 角色会话 / 对话流 / 手动控制 / 主题切换
- 本地可观测性链路完整：OTel + Prometheus + Loki + Tempo + Grafana

### 快速开始

```powershell
cd C:/Users/hangw/awe-agentcheck
py -m pip install -e .[dev]
```

启动 API 后打开：

- `http://localhost:8000/`

### 常用模式建议

1. 日常开发（推荐）
   - `sandbox_mode=1`
   - `self_loop_mode=0`
   - `auto_merge=1`
2. 夜间无人值守
   - `sandbox_mode=1`
   - `self_loop_mode=1`
3. 直接改主仓（谨慎）
   - `sandbox_mode=0`

### 常用命令

创建任务（默认安全策略）：

```powershell
py -m awe_agentcheck.cli run `
  --task "修复监控信号噪声" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

作者同意并启动：

```powershell
py -m awe_agentcheck.cli decide task-1 --approve --auto-start
```

### 质量验证

```powershell
py -m ruff check .
py -m pytest -q
```

