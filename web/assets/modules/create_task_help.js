export const CREATE_TASK_HELP_ITEMS = [

  {
  field: 'Title',
  en: 'Task name shown in task list, history, and conversation stream.',
  zh: '任务名称，会显示在任务列表、历史记录和对话流里。',
  },
  {
  field: 'Workspace path',
  en: 'Root path of the project to operate on. Use your real repository path.',
  zh: '要操作的项目根目录。建议填写真实仓库路径。',
  },
  {
  field: 'Author',
  en: 'Configured inside Bot Capability Matrix (single author row). Format: provider#alias (example: codex#author-A).',
  zh: '在 Bot Capability Matrix 内配置（单个 author 行）。格式：provider#alias（例如 codex#author-A）。',
  },
  {
  field: 'Reviewers',
  en: 'Configured inside Bot Capability Matrix. Use "+ Add Reviewer" there to append reviewer rows.',
  zh: '在 Bot Capability Matrix 内配置。通过其中的“+ Add Reviewer”增加 reviewer 行。',
  },
  {
  field: 'Policy Template',
  en: 'Preset control policy by repo size/risk profile. Click "Apply Policy" to fill recommended controls. Use "custom" to keep your manual settings unchanged.',
  zh: '按仓库规模/风险给出策略模板。点击“Apply Policy”可自动填入推荐控制项。选择“custom”可保留你当前手动配置，不覆盖。',
  },
  {
  field: 'Claude/Codex/Gemini Model',
  en: 'Pin model per provider for this task. You can select from list or type custom model id.',
  zh: '按提供者绑定模型。可从列表选择，也可手动输入模型 ID。',
  },
  {
  field: 'Model Params',
  en: 'Extra provider-specific CLI args (advanced). Keep empty unless you know what you need.',
  zh: '提供者额外参数（进阶）。不确定时建议留空。',
  },
  {
  field: 'Bot Capability Matrix',
  en: 'Single place to manage author + reviewers. Configure model/params and per-bot multi-agent toggles (0/1). Participant values override provider defaults.',
  zh: '统一管理 author + reviewers。可按 bot 配置 model/params 与 multi-agent 开关（0/1），参与者配置优先于提供者默认值。',
  },
  {
  field: 'Evolution Level',
  en: '0=fix-only, 1=guided evolution, 2=proactive evolution.',
  zh: '0=仅修复，1=引导进化，2=主动进化。',
  },
  {
  field: 'Repair Mode',
  en: 'minimal=smallest patch, balanced=root-cause focused, structural=allow deeper refactor.',
  zh: 'minimal=最小修补，balanced=聚焦根因，structural=允许更深重构。',
  },
  {
  field: 'Max Rounds',
  en: 'Fallback round cap when no deadline is set.',
  zh: '未设置截止时间时的轮次上限。',
  },
  {
  field: 'Evolve Until',
  en: 'Deadline in local time. When set, deadline takes priority over Max Rounds.',
  zh: '本地时间截止点。设置后优先于 Max Rounds。',
  },
  {
  field: 'Conversation Language',
  en: 'Controls preferred output language for participant responses.',
  zh: '控制参与者输出语言偏好。',
  },
  {
  field: 'Plain Mode',
  en: 'Beginner-readable style. Recommended ON for easier conversation text.',
  zh: '小白可读模式。建议开启，便于看懂对话内容。',
  },
  {
  field: 'Stream Mode',
  en: 'Realtime streaming chunks from participants. OFF means stage summary only.',
  zh: '实时流式输出。关闭后仅显示阶段总结。',
  },
  {
  field: 'Debate Mode',
  en: 'Enable reviewer-first debate/precheck stage before implementation.',
  zh: '启用 reviewer-first 预审/辩论阶段。',
  },
  {
  field: 'Sandbox Mode',
  en: '1=run in isolated lab workspace, 0=run directly in main workspace.',
  zh: '1=在隔离沙盒运行，0=直接在主工作区运行。',
  },
  {
  field: 'Sandbox Workspace Path',
  en: 'Optional custom sandbox folder. Leave blank for auto unique per-task sandbox.',
  zh: '可选自定义沙盒路径。留空则自动生成每任务独立沙盒。',
  },
  {
  field: 'Self Loop Mode',
  en: '0=manual checkpoint, 1=autonomous loop.',
  zh: '0=人工确认流程，1=全自动循环流程。',
  },
  {
  field: 'Auto Merge',
  en: 'On pass, merge sandbox changes to target path automatically.',
  zh: '任务通过后，自动将沙盒改动融合到目标路径。',
  },
  {
  field: 'Merge Target Path',
  en: 'Destination path for auto-merge. Leave blank to merge in-place.',
  zh: '自动融合目标路径。留空表示原地融合。',
  },
  {
  field: 'Description',
  en: 'Main instruction body. Include goal, constraints, acceptance criteria.',
  zh: '主要任务说明。建议写清目标、约束和验收标准。',
  },
  {
  field: 'Create',
  en: 'Create task only (queued). Start manually later.',
  zh: '仅创建任务（queued），稍后手动启动。',
  },
  {
  field: 'Create + Start',
  en: 'Create task and start immediately.',
  zh: '创建并立即启动任务。',
  },
  {
  field: 'Policy Note',
  en: 'When Sandbox Mode = 0, Auto Merge is forced to 0. When Evolve Until is set, Max Rounds is disabled.',
  zh: '策略说明：Sandbox Mode=0 时 Auto Merge 会被强制为 0；设置 Evolve Until 后 Max Rounds 会禁用。',
  },
];

