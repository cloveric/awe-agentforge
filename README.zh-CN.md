<p align="center">
  <img src="docs/assets/awe-agentcheck-hero.svg" alt="awe-agentcheck hero" width="100%" />
</p>

<p align="center">
  <a href="README.md"><b>English README</b></a>
</p>

---

# awe-agentcheck（中文）

面向多 CLI 代理协作（作者/审阅者）的专业级编排平台。  
默认强调安全与可控：先沙盒、先讨论、再由作者确认是否执行。

## 目录

- [项目定位](#项目定位)
- [可视化总览](#可视化总览)
- [能力矩阵](#能力矩阵)
- [策略模式](#策略模式)
- [快速开始](#快速开始)
- [CLI 用法](#cli-用法)
- [API](#api)
- [路线图](#路线图)

## 项目定位

核心流程：

`讨论 -> 实现 -> 审阅 -> 验证 -> 门禁`

典型用法：

- Claude 写、Codex 审
- Codex 写、Claude 审
- 同一 CLI 的不同 session 互审
- 夜间无人值守连续进化（含超时看门狗、降级切换、可观测性）

## 可视化总览

### 1) 监控面板预览

<p align="center">
  <img src="docs/assets/dashboard-preview.svg" alt="dashboard preview" width="100%" />
</p>

### 2) 运行流程图

<p align="center">
  <img src="docs/assets/workflow-flow.svg" alt="workflow flow" width="100%" />
</p>

## 能力矩阵

| 能力 | 说明 | 状态 |
|---|---|---|
| 沙盒优先执行 | 默认 `sandbox_mode=1`，任务跑 `*-lab` | `GA` |
| 作者确认门 | 默认 `self_loop_mode=0`，先进入 `waiting_manual` | `GA` |
| 全自动自循环 | `self_loop_mode=1`，适合无人值守 | `GA` |
| 自动融合 | 通过后可自动合并、写 changelog、打快照 | `GA` |
| 多角色模型 | `provider#alias`，支持跨模型/同模型多会话 | `GA` |
| Web 运维控制台 | 项目树（可展开/收起）、角色区、头像化对话流、任务控制 | `GA` |
| 多主题风格 | Neon / Pixel / Executive | `GA` |
| 可观测性链路 | OTel + Prom + Loki + Tempo + Grafana | `GA` |
| 夜间监督脚本 | watchdog、fallback、cooldown | `GA` |

## 策略模式

| 参数 | 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `sandbox_mode` | `0` / `1` | `1` | 主仓执行或沙盒执行 |
| `self_loop_mode` | `0` / `1` | `0` | 作者确认模式或全自动模式 |
| `auto_merge` | `0` / `1` | `1` | 通过后是否自动融合 |

自动融合默认与开关：

1. 新任务默认 `auto_merge=1`（开启）。
2. 按任务关闭方式：CLI 用 `--no-auto-merge`，API 传 `auto_merge=false`，或 Web 创建任务时设 `Auto Merge=0`。
3. 开启时任务 `passed` 会产生 `auto_merge_completed` 事件，并写入 `.agents/threads/<task_id>/artifacts/auto_merge_summary.json`。

默认安全策略（推荐）：

1. `sandbox_mode=1`
2. `self_loop_mode=0`
3. `auto_merge=1`

沙盒默认行为（已内置）：

1. 如果不手动提供 `sandbox_workspace_path`，系统会在 `<project>-lab/<时间戳>-<id>` 下为每个任务创建独立沙盒。
2. 当任务通过且自动融合完成后，会自动清理系统生成的沙盒，避免历史残留混入下一轮。
3. 如果你手工指定了 `sandbox_workspace_path`，默认保留，不自动删除。

## 快速开始

```powershell
cd C:/Users/hangw/awe-agentcheck
py -m pip install -e .[dev]
```

启动 API：

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

打开控制台：

- `http://localhost:8000/`

## CLI 用法

创建任务（默认策略）：

```powershell
py -m awe_agentcheck.cli run `
  --task "修复监控信号噪声" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

单任务关闭自动融合：

```powershell
py -m awe_agentcheck.cli run `
  --task "关闭自动融合示例" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --no-auto-merge `
  --auto-start
```

作者批准并启动：

```powershell
py -m awe_agentcheck.cli decide task-1 --approve --auto-start
```

夜间全自动：

```powershell
py -m awe_agentcheck.cli run `
  --task "夜间连续进化" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --sandbox-mode 1 `
  --self-loop-mode 1 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

## API

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

## 路线图

### 2026 Q1

- [x] 沙盒优先默认策略
- [x] 作者确认门
- [x] 自动融合 + 变更日志 + 快照
- [x] 监控页多主题与角色视角

### 2026 Q2

- [ ] GitHub / PR 深度联动（任务工件回链）
- [ ] 按仓库体量和风险级别的策略模板
- [ ] 扩展更多参与者适配器

### 2026 Q3

- [ ] 沙盒到主仓的策略化自动晋升流水线
- [ ] 更高级的质量趋势分析和评审偏移检测

## 验证

```powershell
py -m ruff check .
py -m pytest -q
```
