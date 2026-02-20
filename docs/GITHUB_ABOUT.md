# GitHub About / Description (2026-02-20, post-refactor sync)

## English About (short, paste into repository About)

Reviewer-first multi-CLI control tower for vibe coding. Run Codex, Claude, Gemini in auditable loops for bug finding, repair, and continuous codebase evolution.

## English Description (long, for project page)

AWE-AgentForge orchestrates multiple coding CLIs in one observable workflow:
- reviewer-first consensus loops
- true per-round LangGraph orchestration (graph loops round-by-round, not single-node full-loop wrapper)
- package-split adapter runtime architecture (`src/awe_agentcheck/adapters/`) with strategy/factory dispatch
- structured reviewer output (JSON + fallback controls)
- structured adapter runtime-error results (no silent empty runs)
- architecture audit (`off|warn|hard`)
- expanded architecture hard checks (service/workflow/dashboard size, prompt hotspot, adapter runtime-raise policy)
- externalized prompt templates (`src/awe_agentcheck/prompt_templates/*.txt`) for safer prompt evolution
- preflight policy guards and evidence-gated completion (`No evidence, no merge`)
- auto-merge and sandbox promotion controls
- modular dashboard client (`api/store/utils/ui/create_task_help/avatar/tree/history/dialogue`) for maintainable Web evolution
- typed event taxonomy (`EventType`) + normalized event writes across repository/SQL storage
- API rate limiting for `/api/*` and stricter artifact filename sanitization
- CI hard gates: ruff + mypy + bandit + pytest-cov (80% threshold)
- integration workflow tests and container baseline (`Dockerfile`)
- post-refactor stability fix verified by browser console + API smoke checks
- analytics + benchmark feedback loop for self-improving runs
- cross-platform operation scripts for Windows + Linux/macOS

Built for teams and solo vibe coders who do not trust single-agent confidence.

## 中文 About（简短，可粘贴到仓库 About）

面向 vibe coding 的 reviewer-first 多 CLI 控制塔：让 Codex、Claude、Gemini 在可观测、可追踪的循环中互审、修复并持续进化代码库。

## 中文描述（长版）

AWE-AgentForge 把多智能体协作工程化为一条可观测流水线：
- reviewer-first 共识闭环
- 真实按轮推进的 LangGraph 编排（不是单节点包一层完整循环）
- 适配器运行时架构已拆包（`src/awe_agentcheck/adapters/`），通过策略/工厂分发 provider 行为
- 结构化审阅输出（JSON + 兼容兜底）
- 结构化适配层运行时错误返回（避免“空跑不清楚”）
- 架构审计（`off|warn|hard`）
- 扩展架构硬规则（service/workflow/dashboard 体量、prompt 热点、adapter 运行时 raise 策略）
- Prompt 模板外置（`src/awe_agentcheck/prompt_templates/*.txt`），降低拼接脆弱性
- 预检策略门禁 + 证据硬门禁（`No evidence, no merge`）
- 自动融合与沙盒晋升控制
- Dashboard 客户端按模块拆分（`api/store/utils/ui/create_task_help/avatar/tree/history/dialogue`），便于持续迭代
- 事件类型治理（`EventType`）+ repository/SQL 统一事件类型归一化
- `/api/*` 内置限流 + artifact 文件名严格净化，降低运行期安全风险
- CI 硬门禁升级：ruff + mypy + bandit + pytest-cov（80%）
- 新增集成测试链路与容器化基础（`Dockerfile`）
- 已补齐重构后自查修复，并通过浏览器控制台 + API 冒烟验证
- 基于 analytics + benchmark 的自我进化回路
- 同时覆盖 Windows 与 Linux/macOS 的运维脚本

适合不再相信“单智能体说没问题就真没问题”的开发者。

## Suggested Topics

- multi-agent
- vibe-coding
- cli
- codex
- claude
- gemini
- orchestration
- code-review
- observability
- automation
- fastapi
- langgraph
