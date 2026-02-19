
import { createApiClient } from './modules/api.js';
import {
  readCreateHelpCollapsedPreference,
  readCreateHelpLanguagePreference,
  readHistoryCollapsePreference,
  readPollPreference,
  readSelectionPreference,
  readStreamDetailPreference,
  persistSelectionPreference as persistSelectionPreferenceStore,
} from './modules/store.js';
import {
  escapeHtml,
  hashText,
  normalizeProjectPath,
  projectName,
  seededRandom,
  sleep,
} from './modules/utils.js';
import { renderModelSelect } from './modules/ui.js';

    const DEFAULT_PROVIDER_MODEL_CATALOG = Object.freeze({
      claude: [
        'claude-opus-4-6',
        'claude-sonnet-4-6',
        'claude-opus-4-1',
        'claude-sonnet-4-5',
        'claude-3-7-sonnet',
        'claude-3-5-sonnet-latest',
      ],
      codex: [
        'gpt-5.3-codex',
        'gpt-5.3-codex-spark',
        'gpt-5-codex',
        'gpt-5',
        'gpt-5-mini',
        'gpt-4.1',
      ],
      gemini: [
        'gemini-3-flash-preview',
        'gemini-3-pro-preview',
        'gemini-3-flash',
        'gemini-3-pro',
        'gemini-flash-latest',
        'gemini-pro-latest',
      ],
    });

    const state = {
      tasks: [],
      historyItems: [],
      stats: null,
      analytics: null,
      policyTemplates: null,
      historyLoadedOnce: false,
      providerModelCatalog: {
        claude: [...DEFAULT_PROVIDER_MODEL_CATALOG.claude],
        codex: [...DEFAULT_PROVIDER_MODEL_CATALOG.codex],
        gemini: [...DEFAULT_PROVIDER_MODEL_CATALOG.gemini],
      },
      selectedProject: null,
      selectedRole: 'all',
      selectedTaskId: null,
      theme: 'neon',
      eventsByTask: new Map(),
      githubSummaryByTask: new Map(),
      treeByProject: new Map(),
      treeOpenByProject: new Map(),
      historyCollapsed: false,
      createHelpCollapsed: true,
      createHelpLanguage: 'zh',
      polling: true,
      showStreamDetails: false,
      timer: null,
      pollTickInFlight: false,
      apiHealthy: false,
      apiFailureCount: 0,
      lastDialogueSignature: '',
      avatarVariantCache: new Map(),
      avatarSessionSalt: `${Date.now()}-${Math.floor(Math.random() * 1e9)}`,
      selectionNeedsValidation: false,
      participantCapabilityDraft: {},
      participantRoleRows: [],
    };

    const SELECTION_PREF_KEY = 'awe-agentcheck-selection';

    const el = {
      projectSelect: document.getElementById('projectSelect'),
      projectTree: document.getElementById('projectTree'),
      projectTreeMeta: document.getElementById('projectTreeMeta'),
      roleList: document.getElementById('roleList'),
      statsLine: document.getElementById('statsLine'),
      kpiStrip: document.getElementById('kpiStrip'),
      analyticsSummary: document.getElementById('analyticsSummary'),
      taskSelect: document.getElementById('taskSelect'),
      dialogue: document.getElementById('dialogue'),
      githubSummaryMeta: document.getElementById('githubSummaryMeta'),
      githubSummaryText: document.getElementById('githubSummaryText'),
      reloadGithubSummaryBtn: document.getElementById('reloadGithubSummaryBtn'),
      actionStatus: document.getElementById('actionStatus'),
      taskSnapshot: document.getElementById('taskSnapshot'),
      projectHistory: document.getElementById('projectHistory'),
      historySummary: document.getElementById('historySummary'),
      projectHistoryBody: document.getElementById('projectHistoryBody'),
      clearHistoryBtn: document.getElementById('clearHistoryBtn'),
      toggleHistoryBtn: document.getElementById('toggleHistoryBtn'),
      openCreateHelpBtn: document.getElementById('openCreateHelpBtn'),
      closeCreateHelpBtn: document.getElementById('closeCreateHelpBtn'),
      createHelpPanel: document.getElementById('createHelpPanel'),
      createHelpHint: document.getElementById('createHelpHint'),
      createHelpList: document.getElementById('createHelpList'),
      createHelpLangEnBtn: document.getElementById('createHelpLangEnBtn'),
      createHelpLangZhBtn: document.getElementById('createHelpLangZhBtn'),
      createStatus: document.getElementById('createStatus'),
      pollBtn: document.getElementById('pollBtn'),
      streamDetailBtn: document.getElementById('streamDetailBtn'),
      startBtn: document.getElementById('startBtn'),
      cancelBtn: document.getElementById('cancelBtn'),
      forceFailBtn: document.getElementById('forceFailBtn'),
      customReplyBtn: document.getElementById('customReplyBtn'),
      promoteRoundBtn: document.getElementById('promoteRoundBtn'),
      promoteRound: document.getElementById('promoteRound'),
      forceReason: document.getElementById('forceReason'),
      manualReplyNote: document.getElementById('manualReplyNote'),
      connBadge: document.getElementById('connBadge'),
      themeSelect: document.getElementById('themeSelect'),
      expandTreeBtn: document.getElementById('expandTreeBtn'),
      collapseTreeBtn: document.getElementById('collapseTreeBtn'),
      approveQueueBtn: document.getElementById('approveQueueBtn'),
      approveStartBtn: document.getElementById('approveStartBtn'),
      rejectBtn: document.getElementById('rejectBtn'),
      policyTemplate: document.getElementById('policyTemplate'),
      applyPolicyTemplateBtn: document.getElementById('applyPolicyTemplateBtn'),
      policyProfileHint: document.getElementById('policyProfileHint'),
      workspacePath: document.getElementById('workspacePath'),
      author: document.getElementById('author'),
      reviewers: document.getElementById('reviewers'),
      matrixAddReviewerBtn: document.getElementById('matrixAddReviewerBtn'),
      selfLoopMode: document.getElementById('selfLoopMode'),
      claudeModel: document.getElementById('claudeModel'),
      codexModel: document.getElementById('codexModel'),
      geminiModel: document.getElementById('geminiModel'),
      claudeModelCustom: document.getElementById('claudeModelCustom'),
      codexModelCustom: document.getElementById('codexModelCustom'),
      geminiModelCustom: document.getElementById('geminiModelCustom'),
      claudeModelParams: document.getElementById('claudeModelParams'),
      codexModelParams: document.getElementById('codexModelParams'),
      geminiModelParams: document.getElementById('geminiModelParams'),
      participantCapabilityMatrix: document.getElementById('participantCapabilityMatrix'),
      sandboxMode: document.getElementById('sandboxMode'),
      autoMerge: document.getElementById('autoMerge'),
      mergeTargetPath: document.getElementById('mergeTargetPath'),
      evolveUntil: document.getElementById('evolveUntil'),
      maxRounds: document.getElementById('maxRounds'),
      repairMode: document.getElementById('repairMode'),
      plainMode: document.getElementById('plainMode'),
      streamMode: document.getElementById('streamMode'),
      debateMode: document.getElementById('debateMode'),
    };

    const savedSelection = readSelectionPreference();
    if (savedSelection) {
      state.selectedProject = savedSelection.selectedProject;
      state.selectedTaskId = savedSelection.selectedTaskId;
      state.selectedRole = savedSelection.selectedRole;
      state.selectionNeedsValidation = true;
    }

    const THEME_OPTIONS = [
      { id: 'neon', label: 'Neon Grid' },
      { id: 'pixel', label: 'Terminal Pixel' },
      { id: 'pixel-sw', label: 'Terminal Pixel: Star Wars' },
      { id: 'pixel-sg', label: 'Terminal Pixel: Three Kingdoms' },
      { id: 'executive', label: 'Executive Glass' },
    ];

    const CREATE_TASK_HELP_ITEMS = [
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

    function normalizeTheme(themeId) {
      const value = String(themeId || '').trim().toLowerCase();
      return THEME_OPTIONS.some((theme) => theme.id === value) ? value : 'neon';
    }

    function readThemePreference() {
      try {
        return normalizeTheme(localStorage.getItem('awe-agentcheck-theme'));
      } catch {
        return 'neon';
      }
    }

    function persistSelectionPreference() {
      persistSelectionPreferenceStore(
        {
          selectedProject: state.selectedProject,
          selectedTaskId: state.selectedTaskId,
          selectedRole: state.selectedRole,
        },
        SELECTION_PREF_KEY,
      );
    }

    function applyTheme(themeId, { persist = true } = {}) {
      const theme = normalizeTheme(themeId);
      state.theme = theme;
      document.body.dataset.theme = theme;
      if (el.themeSelect) {
        el.themeSelect.value = theme;
      }
      if (persist) {
        try {
          localStorage.setItem('awe-agentcheck-theme', theme);
        } catch {
        }
      }
    }

    function initThemeSelector() {
      if (!el.themeSelect) {
        applyTheme('neon', { persist: false });
        return;
      }
      el.themeSelect.innerHTML = '';
      for (const theme of THEME_OPTIONS) {
        const option = document.createElement('option');
        option.value = theme.id;
        option.textContent = theme.label;
        el.themeSelect.appendChild(option);
      }
      applyTheme(readThemePreference(), { persist: false });
      el.themeSelect.addEventListener('change', () => applyTheme(el.themeSelect.value));
    }

    function setApiHealth(ok, detail = '', options = {}) {
      const increment = options.increment !== undefined ? !!options.increment : true;
      state.apiHealthy = !!ok;
      if (ok) {
        state.apiFailureCount = 0;
        el.connBadge.className = 'pill ok';
        el.connBadge.textContent = 'API: ONLINE';
        return;
      }
      if (increment) {
        state.apiFailureCount += 1;
      } else if (state.apiFailureCount <= 0) {
        state.apiFailureCount = 1;
      }
      el.connBadge.className = 'pill warn';
      el.connBadge.textContent = `API: RETRY(${state.apiFailureCount})`;
      if (detail) {
        el.actionStatus.textContent = `API unstable: ${detail}`;
      }
    }

    const api = createApiClient({ setApiHealth, fetchImpl: fetch, sleepFn: sleep });

    function treeNodeLabel(path) {
      const raw = String(path || '').replace(/\\/g, '/');
      const trimmed = raw.replace(/\/+$/, '');
      if (!trimmed) return '.';
      const parts = trimmed.split('/').filter(Boolean);
      return parts.length ? parts[parts.length - 1] : trimmed;
    }

    function parseProvider(participantId) {
      const text = String(participantId || '');
      if (!text.includes('#')) return text || 'unknown';
      return text.split('#')[0];
    }

    function readProviderModelsFromForm() {
      const out = {};
      const fields = [
        ['claude', el.claudeModel, el.claudeModelCustom],
        ['codex', el.codexModel, el.codexModelCustom],
        ['gemini', el.geminiModel, el.geminiModelCustom],
      ];
      for (const [provider, selectElm, customElm] of fields) {
        const custom = String(customElm && customElm.value || '').trim();
        if (custom) {
          out[provider] = custom;
          continue;
        }
        const model = String(selectElm && selectElm.value || '').trim();
        if (model) out[provider] = model;
      }
      return out;
    }

    function readProviderModelParamsFromForm() {
      const out = {};
      const fields = [
        ['claude', el.claudeModelParams],
        ['codex', el.codexModelParams],
        ['gemini', el.geminiModelParams],
      ];
      for (const [provider, input] of fields) {
        const params = String(input && input.value || '').trim();
        if (params) out[provider] = params;
      }
      return out;
    }

    function normalizeParticipantIdList(values) {
      const out = [];
      const seen = new Set();
      for (const raw of values || []) {
        const value = String(raw || '').trim();
        if (!value) continue;
        const key = value.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(value);
      }
      return out;
    }

    function defaultAuthorParticipantId() {
      return 'codex#author-A';
    }

    function normalizeParticipantRoleRows(rows) {
      const list = Array.isArray(rows) ? rows : [];
      const out = [];
      let authorId = '';
      const reviewers = [];
      const reviewerSeen = new Set();

      for (const item of list) {
        const role = String((item && item.role) || '').trim().toLowerCase();
        const participantId = String((item && item.participantId) || '').trim();
        if (role === 'author') {
          if (!authorId && participantId) {
            authorId = participantId;
          }
          continue;
        }
        if (role === 'reviewer') {
          const key = participantId.toLowerCase();
          if (!participantId || reviewerSeen.has(key)) {
            continue;
          }
          reviewerSeen.add(key);
          reviewers.push(participantId);
        }
      }

      authorId = authorId || String((el.author && el.author.value) || '').trim() || defaultAuthorParticipantId();
      out.push({ role: 'author', participantId: authorId });
      for (const reviewerId of reviewers) {
        if (reviewerId.toLowerCase() === authorId.toLowerCase()) {
          continue;
        }
        out.push({ role: 'reviewer', participantId: reviewerId });
      }
      return out;
    }

    function suggestReviewerParticipantId(existingRows) {
      const rows = Array.isArray(existingRows) ? existingRows : [];
      const existing = new Set(
        rows
          .map((item) => String((item && item.participantId) || '').trim().toLowerCase())
          .filter(Boolean)
      );
      const providers = ['claude', 'codex', 'gemini'];
      const aliases = ['B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K'];
      for (let idx = 0; idx < 256; idx += 1) {
        const provider = providers[idx % providers.length];
        const alias = aliases[idx] || String(idx + 2);
        const candidate = `${provider}#review-${alias}`;
        if (!existing.has(candidate.toLowerCase())) {
          return candidate;
        }
      }
      return `codex#review-${Date.now()}`;
    }

    function roleRowsFromHiddenFields() {
      const author = String((el.author && el.author.value) || '').trim() || defaultAuthorParticipantId();
      const reviewersText = String((el.reviewers && el.reviewers.value) || '');
      const reviewers = normalizeParticipantIdList(
        reviewersText.split(',').map((v) => String(v || '').trim())
      );
      const rows = [{ role: 'author', participantId: author }];
      for (const reviewerId of reviewers) {
        if (reviewerId.toLowerCase() === author.toLowerCase()) continue;
        rows.push({ role: 'reviewer', participantId: reviewerId });
      }
      return rows;
    }

    function syncRoleRowsToHiddenFields(rows) {
      const normalized = normalizeParticipantRoleRows(rows);
      const author = normalized[0] ? String(normalized[0].participantId || '').trim() : defaultAuthorParticipantId();
      const reviewers = normalized
        .slice(1)
        .map((item) => String((item && item.participantId) || '').trim())
        .filter(Boolean);
      if (el.author) {
        el.author.value = author;
      }
      if (el.reviewers) {
        el.reviewers.value = reviewers.join(',');
      }
      return normalized;
    }

    function roleRowsFromState() {
      if (!Array.isArray(state.participantRoleRows) || !state.participantRoleRows.length) {
        state.participantRoleRows = roleRowsFromHiddenFields();
      }
      state.participantRoleRows = syncRoleRowsToHiddenFields(state.participantRoleRows);
      return [...state.participantRoleRows];
    }

    function readAuthorParticipantFromForm() {
      const rows = roleRowsFromState();
      if (!rows.length) return defaultAuthorParticipantId();
      return String(rows[0].participantId || '').trim() || defaultAuthorParticipantId();
    }

    function readReviewerParticipantsFromForm() {
      const rows = roleRowsFromState();
      return rows
        .slice(1)
        .map((item) => String((item && item.participantId) || '').trim())
        .filter(Boolean);
    }

    function parseTaskParticipantsFromForm() {
      const out = [];
      const seen = new Set();
      const author = readAuthorParticipantFromForm();
      if (author && !seen.has(author)) {
        seen.add(author);
        out.push(author);
      }
      const reviewers = readReviewerParticipantsFromForm();
      for (const reviewer of reviewers) {
        if (seen.has(reviewer)) continue;
        seen.add(reviewer);
        out.push(reviewer);
      }
      return out;
    }

    function providerDefaultsFromForm(provider) {
      const key = String(provider || '').trim().toLowerCase();
      const map = {
        claude: [el.claudeModel, el.claudeModelCustom, el.claudeModelParams],
        codex: [el.codexModel, el.codexModelCustom, el.codexModelParams],
        gemini: [el.geminiModel, el.geminiModelCustom, el.geminiModelParams],
      };
      const tuple = map[key];
      if (!tuple) return { model: '', params: '' };
      const [selectElm, customElm, paramsElm] = tuple;
      const custom = String((customElm && customElm.value) || '').trim();
      const model = custom || String((selectElm && selectElm.value) || '').trim();
      const params = String((paramsElm && paramsElm.value) || '').trim();
      return { model, params };
    }

    function participantModelOptions(provider, selectedModel) {
      const key = String(provider || '').trim().toLowerCase();
      const list = Array.isArray((state.providerModelCatalog || {})[key]) ? (state.providerModelCatalog || {})[key] : [];
      const seen = new Set();
      const out = [];
      const selected = String(selectedModel || '').trim();
      if (selected) {
        seen.add(selected.toLowerCase());
        out.push(selected);
      }
      for (const raw of list) {
        const value = String(raw || '').trim();
        const norm = value.toLowerCase();
        if (!value || seen.has(norm)) continue;
        seen.add(norm);
        out.push(value);
      }
      return out;
    }

    function pruneParticipantCapabilityDraft(activeParticipants) {
      const active = new Set((activeParticipants || []).map((v) => String(v || '').trim()).filter(Boolean));
      const next = {};
      for (const [participant, payload] of Object.entries(state.participantCapabilityDraft || {})) {
        const key = String(participant || '').trim();
        if (!key || !active.has(key)) continue;
        next[key] = {
          model: String((payload && payload.model) || '').trim(),
          customModel: String((payload && payload.customModel) || '').trim(),
          params: String((payload && payload.params) || '').trim(),
          claudeAgentsMode: String((payload && payload.claudeAgentsMode) || '0').trim().toLowerCase() || '0',
          codexMultiAgentsMode: String((payload && payload.codexMultiAgentsMode) || '0').trim().toLowerCase() || '0',
        };
      }
      state.participantCapabilityDraft = next;
    }

    function renderParticipantCapabilityMatrix() {
      const host = el.participantCapabilityMatrix;
      if (!host) return;
      const roleRows = roleRowsFromState();
      const participants = roleRows.map((row) => String((row && row.participantId) || '').trim()).filter(Boolean);
      pruneParticipantCapabilityDraft(participants);
      if (!roleRows.length) {
        host.innerHTML = '<div class="empty">No participants configured.</div>';
        return;
      }

      const rows = roleRows.map((roleRow, rowIndex) => {
        const role = String((roleRow && roleRow.role) || 'reviewer').trim().toLowerCase() === 'author'
          ? 'author'
          : 'reviewer';
        const participantId = String((roleRow && roleRow.participantId) || '').trim();
        const provider = participantId ? parseProvider(participantId) : '';
        const defaults = providerDefaultsFromForm(provider);
        const draft = participantId ? (state.participantCapabilityDraft[participantId] || {}) : {};
        const selectedModel = String(draft.model || defaults.model || '').trim();
        const customModel = String(draft.customModel || '').trim();
        const params = String(
          draft.params !== undefined && draft.params !== null
            ? draft.params
            : defaults.params
        ).trim();
        const rowDisabled = !participantId;
        const claudeToggleDisabled = provider !== 'claude';
        const codexToggleDisabled = provider !== 'codex';
        const claudeAgentsModeRaw = String(draft.claudeAgentsMode || '0').trim().toLowerCase();
        const codexMultiAgentsModeRaw = String(draft.codexMultiAgentsMode || '0').trim().toLowerCase();
        const claudeAgentsMode = claudeToggleDisabled
          ? '0'
          : (['1', '0'].includes(claudeAgentsModeRaw) ? claudeAgentsModeRaw : '0');
        const codexMultiAgentsMode = codexToggleDisabled
          ? '0'
          : (['1', '0'].includes(codexMultiAgentsModeRaw) ? codexMultiAgentsModeRaw : '0');
        const options = participantModelOptions(provider, selectedModel);
        const optionHtml = options.length
          ? options
            .map((model) => `<option value="${escapeHtml(model)}"${model === selectedModel ? ' selected' : ''}>${escapeHtml(model)}</option>`)
            .join('')
          : `<option value="">${escapeHtml(participantId ? '(no model candidates)' : '(set Bot ID first)')}</option>`;
        const participantAttr = participantId ? `data-participant="${escapeHtml(participantId)}"` : '';

        return `
          <div class="participant-matrix-row">
            <div class="participant-role-line">
              <div>
                <label>Bot ID</label>
                <input
                  data-row-index="${rowIndex}"
                  data-field="participantId"
                  value="${escapeHtml(participantId)}"
                  placeholder="${role === 'author' ? 'provider#author-A' : 'provider#review-B'}"
                />
              </div>
              <span class="participant-role-pill">${role}</span>
              ${role === 'reviewer'
                ? `<button class="participant-remove-btn" data-remove-row="${rowIndex}" type="button">Remove</button>`
                : '<span></span>'}
            </div>
            <div class="participant-matrix-head">
              <span>${escapeHtml(participantId || '(empty)')}</span>
              <span class="participant-matrix-meta">provider=${escapeHtml(provider || 'n/a')}</span>
            </div>
            <div class="participant-matrix-fields">
              <div>
                <label>Model</label>
                <select data-row-index="${rowIndex}" data-field="model" ${participantAttr} ${rowDisabled ? 'disabled' : ''}>${optionHtml}</select>
              </div>
              <div>
                <label>Custom Model (override)</label>
                <input
                  data-row-index="${rowIndex}"
                  data-field="customModel"
                  ${participantAttr}
                  value="${escapeHtml(customModel)}"
                  placeholder="optional custom model id"
                  ${rowDisabled ? 'disabled' : ''}
                />
              </div>
              <div>
                <label>Model Params</label>
                <input
                  data-row-index="${rowIndex}"
                  data-field="params"
                  ${participantAttr}
                  value="${escapeHtml(params)}"
                  placeholder="optional CLI params"
                  ${rowDisabled ? 'disabled' : ''}
                />
              </div>
              <div>
                <label>Claude Team Agents</label>
                <select
                  data-row-index="${rowIndex}"
                  data-field="claudeAgentsMode"
                  ${participantAttr}
                  ${rowDisabled || claudeToggleDisabled ? 'disabled' : ''}
                >
                  <option value="1"${claudeAgentsMode === '1' ? ' selected' : ''}>1 | on</option>
                  <option value="0"${claudeAgentsMode === '0' ? ' selected' : ''}>0 | off</option>
                </select>
              </div>
              <div>
                <label>Codex Multi Agents</label>
                <select
                  data-row-index="${rowIndex}"
                  data-field="codexMultiAgentsMode"
                  ${participantAttr}
                  ${rowDisabled || codexToggleDisabled ? 'disabled' : ''}
                >
                  <option value="1"${codexMultiAgentsMode === '1' ? ' selected' : ''}>1 | on</option>
                  <option value="0"${codexMultiAgentsMode === '0' ? ' selected' : ''}>0 | off</option>
                </select>
              </div>
            </div>
          </div>
        `;
      }).join('');
      host.innerHTML = rows;
    }

    function readParticipantModelsFromForm() {
      const out = {};
      if (!el.participantCapabilityMatrix) return out;
      const rows = el.participantCapabilityMatrix.querySelectorAll('[data-participant][data-field]');
      const buckets = {};
      for (const node of rows) {
        const participant = String(node.getAttribute('data-participant') || '').trim();
        const field = String(node.getAttribute('data-field') || '').trim();
        if (!participant || !field) continue;
        if (!buckets[participant]) buckets[participant] = {};
        buckets[participant][field] = String(node.value || '').trim();
      }
      for (const [participant, payload] of Object.entries(buckets)) {
        const custom = String((payload && payload.customModel) || '').trim();
        const model = custom || String((payload && payload.model) || '').trim();
        if (model) out[participant] = model;
      }
      return out;
    }

    function readParticipantModelParamsFromForm() {
      const out = {};
      if (!el.participantCapabilityMatrix) return out;
      const rows = el.participantCapabilityMatrix.querySelectorAll('[data-participant][data-field="params"]');
      for (const node of rows) {
        const participant = String(node.getAttribute('data-participant') || '').trim();
        const params = String(node.value || '').trim();
        if (participant && params) {
          out[participant] = params;
        }
      }
      return out;
    }

    function readParticipantAgentOverridesFromForm(field) {
      const out = {};
      if (!el.participantCapabilityMatrix) return out;
      const rows = el.participantCapabilityMatrix.querySelectorAll(`[data-participant][data-field="${field}"]`);
      for (const node of rows) {
        const participant = String(node.getAttribute('data-participant') || '').trim();
        const mode = String(node.value || '').trim().toLowerCase();
        if (!participant) continue;
        if (mode === '1') {
          out[participant] = true;
          continue;
        }
        if (mode === '0') {
          out[participant] = false;
        }
      }
      return out;
    }

    function renderProviderModelOptions() {
      const catalog = state.providerModelCatalog || {};
      renderModelSelect(el.claudeModel, catalog.claude || []);
      renderModelSelect(el.codexModel, catalog.codex || []);
      renderModelSelect(el.geminiModel, catalog.gemini || []);
      renderParticipantCapabilityMatrix();
    }

    function normalizeProviderModelCatalog(rawCatalog) {
      const out = {};
      for (const provider of ['claude', 'codex', 'gemini']) {
        const seen = new Set();
        const merged = [
          ...(DEFAULT_PROVIDER_MODEL_CATALOG[provider] || []),
          ...(Array.isArray(rawCatalog && rawCatalog[provider]) ? rawCatalog[provider] : []),
        ];
        out[provider] = [];
        for (const raw of merged) {
          const model = String(raw || '').trim();
          const key = model.toLowerCase();
          if (!model || seen.has(key)) continue;
          seen.add(key);
          out[provider].push(model);
        }
      }
      return out;
    }

    function formatProviderModels(value) {
      const obj = value && typeof value === 'object' ? value : {};
      const entries = Object.entries(obj)
        .map(([provider, model]) => [String(provider || '').trim(), String(model || '').trim()])
        .filter(([provider, model]) => provider && model);
      if (!entries.length) return 'n/a';
      return entries.map(([provider, model]) => `${provider}=${model}`).join(', ');
    }

    function formatProviderModelParams(value) {
      const obj = value && typeof value === 'object' ? value : {};
      const entries = Object.entries(obj)
        .map(([provider, params]) => [String(provider || '').trim(), String(params || '').trim()])
        .filter(([provider, params]) => provider && params);
      if (!entries.length) return 'n/a';
      return entries.map(([provider, params]) => `${provider}=${params}`).join(' | ');
    }

    function formatParticipantModels(value) {
      const obj = value && typeof value === 'object' ? value : {};
      const entries = Object.entries(obj)
        .map(([participant, model]) => [String(participant || '').trim(), String(model || '').trim()])
        .filter(([participant, model]) => participant && model);
      if (!entries.length) return 'n/a';
      return entries.map(([participant, model]) => `${participant}=${model}`).join(' | ');
    }

    function formatParticipantModelParams(value) {
      const obj = value && typeof value === 'object' ? value : {};
      const entries = Object.entries(obj)
        .map(([participant, params]) => [String(participant || '').trim(), String(params || '').trim()])
        .filter(([participant, params]) => participant && params);
      if (!entries.length) return 'n/a';
      return entries.map(([participant, params]) => `${participant}=${params}`).join(' | ');
    }

    function formatParticipantBoolOverrides(value) {
      const obj = value && typeof value === 'object' ? value : {};
      const entries = Object.entries(obj)
        .map(([participant, enabled]) => [String(participant || '').trim(), !!enabled])
        .filter(([participant]) => participant);
      if (!entries.length) return 'n/a';
      return entries.map(([participant, enabled]) => `${participant}=${enabled ? 1 : 0}`).join(' | ');
    }

    function normalizeAvatarProvider(provider) {
      const raw = String(provider || '').trim().toLowerCase();
      return ['claude', 'codex', 'gemini'].includes(raw) ? raw : 'system';
    }

    function avatarVariantInfo(roleId, provider, scope, variantCount) {
      const key = normalizeAvatarProvider(provider);
      const role = String(roleId || 'system').trim() || 'system';
      const scopeKey = String(scope || state.theme || 'pixel').trim().toLowerCase();
      const count = Math.max(1, Number(variantCount || 1));
      const cacheKey = `${scopeKey}|${key}|${role}`;
      const cached = state.avatarVariantCache.get(cacheKey);
      if (cached && Number(cached.variantCount) === count) {
        return cached;
      }
      const seeded = seededRandom(hashText(`${cacheKey}|${state.avatarSessionSalt}`));
      const info = {
        variantCount: count,
        variant: Math.floor(seeded() * count),
        noise: Math.floor(seeded() * 1000000000),
      };
      state.avatarVariantCache.set(cacheKey, info);
      return info;
    }

    function avatarPalette(provider, rng) {
      const key = String(provider || '').toLowerCase();
      const isPixelTheme = state.theme === 'pixel';
      const skinPairs = [
        ['#f6d2b7', '#d9a88b'],
        ['#deb38c', '#bf8c68'],
        ['#c98b66', '#a76c49'],
        ['#9b6546', '#7f4f36'],
      ];
      const hairByProvider = isPixelTheme
        ? (key === 'codex'
          ? ['#d8e7ff', '#b8c8df', '#95a8c9', '#4f6076']
          : key === 'gemini'
            ? ['#ffe59a', '#ffd166', '#e6b63f', '#8f6a1e']
            : ['#f1f1f1', '#d7d7d7', '#aaaaaa', '#5f5f5f'])
        : key === 'claude'
          ? ['#f1f1f1', '#d7d7d7', '#aaaaaa', '#5f5f5f']
          : key === 'codex'
            ? ['#d8e7ff', '#b8c8df', '#8fa0bc', '#4f6076']
            : key === 'gemini'
              ? ['#ffe59a', '#ffd166', '#e6b63f', '#8f6a1e']
              : ['#e6d8b5', '#bfbfbf', '#8a8a8a', '#5a5a5a'];
      const shirtByProvider = isPixelTheme
        ? (key === 'codex'
          ? [['#345587', '#263f67'], ['#2e4064', '#253453'], ['#5f5f75', '#48485d']]
          : key === 'gemini'
            ? [['#8a5a20', '#684214'], ['#5d4f2e', '#463c22'], ['#4f4b68', '#3d3a53']]
            : [['#5a5a5a', '#454545'], ['#61677a', '#4a5060'], ['#6a4444', '#533434']])
        : key === 'claude'
          ? [['#2f6f49', '#24533a'], ['#315f8f', '#264970'], ['#6d5a2e', '#584821']]
          : key === 'codex'
            ? [['#345587', '#263f67'], ['#2d6a71', '#235258'], ['#63558b', '#4d416d']]
            : key === 'gemini'
              ? [['#8a5a20', '#684214'], ['#2d5d68', '#23474f'], ['#57456f', '#423455']]
              : [['#5a5a5a', '#454545'], ['#3e5d3f', '#304932'], ['#6a4444', '#533434']];
      const bgByProvider = isPixelTheme
        ? (key === 'codex'
          ? [['#0d1624', '#142032'], ['#11182a', '#182944']]
          : key === 'gemini'
            ? [['#1f160a', '#2a1d0e'], ['#1a1410', '#262018']]
            : [['#141414', '#1c1c1c'], ['#101317', '#181d22']])
        : key === 'claude'
          ? [['#0f1a13', '#112319'], ['#11161f', '#15233a']]
          : key === 'codex'
            ? [['#0d1624', '#142032'], ['#11182a', '#182944']]
            : key === 'gemini'
              ? [['#1f160a', '#2a1d0e'], ['#1a1410', '#262018']]
              : [['#141414', '#1c1c1c'], ['#101317', '#181d22']];
      const skinChoice = skinPairs[Math.floor(rng() * skinPairs.length)];
      const shirtChoice = shirtByProvider[Math.floor(rng() * shirtByProvider.length)];
      const bgChoice = bgByProvider[Math.floor(rng() * bgByProvider.length)];
      return {
        skin: skinChoice[0],
        skinShade: skinChoice[1],
        hair: hairByProvider[Math.floor(rng() * hairByProvider.length)],
        shirt: shirtChoice[0],
        shirtShade: shirtChoice[1],
        eyeWhite: '#f5f5f5',
        pupil: '#121212',
        lip: '#8a4b4b',
        outline: '#050505',
        bg: bgChoice[0],
        bgShade: bgChoice[1],
        accent: isPixelTheme
          ? (key === 'codex' ? '#9ec9ff' : key === 'gemini' ? '#ffd166' : '#d7dce6')
          : (key === 'claude' ? '#9fffd0' : key === 'codex' ? '#9ec9ff' : key === 'gemini' ? '#ffd166' : '#f0d79a'),
      };
    }

    function generateAvatarSvg(roleId, provider) {
      if (state.theme === 'pixel-sw') {
        return generateAvatarSvgStarWars(roleId, provider);
      }
      if (state.theme === 'pixel-sg') {
        return generateAvatarSvgThreeKingdoms(roleId, provider);
      }
      if (state.theme === 'pixel') {
        return generateAvatarSvgPixel(roleId, provider);
      }
      const seed = `${provider}|${roleId}`;
      const rng = seededRandom(hashText(seed));
      const palette = avatarPalette(provider, rng);
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      fillRect(0, 0, 23, 23, palette.bg);
      fillRect(0, 16, 23, 23, palette.bgShade);
      for (let y = 0; y < size; y += 2) {
        for (let x = 0; x < size; x += 2) {
          if (((x + y) / 2 + Math.floor(rng() * 5)) % 5 === 0) {
            px(x, y, palette.bgShade);
          }
        }
      }

      fillRect(3, 17, 20, 23, palette.shirt);
      fillRect(5, 19, 18, 23, palette.shirtShade);
      fillRect(10, 16, 13, 18, palette.skin);
      fillRect(10, 18, 13, 18, palette.skinShade);
      fillRect(8, 17, 9, 18, palette.shirtShade);
      fillRect(14, 17, 15, 18, palette.shirtShade);

      fillRect(6, 5, 17, 16, palette.skin);
      fillRect(5, 7, 5, 12, palette.skin);
      fillRect(18, 7, 18, 12, palette.skin);
      fillRect(7, 14, 16, 16, palette.skinShade);
      strokeRect(6, 5, 17, 16, palette.outline);
      px(6, 16, palette.skinShade);
      px(17, 16, palette.skinShade);
      fillRect(5, 8, 5, 11, palette.skinShade);
      fillRect(18, 8, 18, 11, palette.skinShade);

      const hairStyle = Math.floor(rng() * 4);
      if (hairStyle === 0) {
        fillRect(5, 2, 18, 6, palette.hair);
        fillRect(5, 7, 6, 9, palette.hair);
        fillRect(17, 7, 18, 9, palette.hair);
      } else if (hairStyle === 1) {
        fillRect(4, 2, 19, 5, palette.hair);
        fillRect(4, 6, 4, 12, palette.hair);
        fillRect(19, 6, 19, 9, palette.hair);
        fillRect(8, 6, 14, 6, palette.hair);
      } else if (hairStyle === 2) {
        fillRect(5, 2, 18, 4, palette.hair);
        fillRect(5, 5, 7, 9, palette.hair);
        fillRect(16, 5, 18, 9, palette.hair);
        fillRect(8, 5, 15, 5, palette.hair);
      } else {
        fillRect(6, 2, 17, 4, palette.hair);
        fillRect(6, 5, 6, 10, palette.hair);
        fillRect(17, 5, 17, 10, palette.hair);
        fillRect(8, 5, 15, 5, palette.hair);
        fillRect(10, 1, 13, 1, palette.hair);
      }

      fillRect(8, 8, 10, 8, palette.outline);
      fillRect(13, 8, 15, 8, palette.outline);
      fillRect(8, 9, 10, 10, palette.eyeWhite);
      fillRect(13, 9, 15, 10, palette.eyeWhite);

      const eyeShift = rng() > 0.5 ? 0 : 1;
      px(9 + eyeShift, 9, palette.pupil);
      px(14 + eyeShift, 9, palette.pupil);
      if (rng() > 0.6) {
        px(8, 10, palette.pupil);
        px(15, 10, palette.pupil);
      }

      fillRect(11, 10, 12, 12, palette.skinShade);
      px(11, 13, palette.skinShade);
      px(12, 13, palette.skinShade);

      const mouthStyle = Math.floor(rng() * 4);
      if (mouthStyle === 0) {
        fillRect(9, 14, 14, 14, palette.lip);
      } else if (mouthStyle === 1) {
        fillRect(9, 14, 10, 14, palette.lip);
        fillRect(11, 15, 12, 15, palette.lip);
        fillRect(13, 14, 14, 14, palette.lip);
      } else if (mouthStyle === 2) {
        fillRect(9, 15, 14, 15, palette.lip);
      } else {
        fillRect(10, 14, 13, 14, palette.lip);
      }

      if (rng() > 0.62) {
        fillRect(7, 9, 10, 10, palette.outline);
        fillRect(13, 9, 16, 10, palette.outline);
        fillRect(11, 9, 12, 9, palette.outline);
      }
      if (rng() > 0.76) {
        fillRect(8, 15, 15, 16, palette.skinShade);
        px(10, 16, palette.outline);
        px(13, 16, palette.outline);
      }
      if (rng() > 0.7) {
        fillRect(2, 10, 4, 11, palette.accent);
        fillRect(19, 10, 21, 11, palette.accent);
        fillRect(3, 12, 3, 13, palette.outline);
        fillRect(20, 12, 20, 13, palette.outline);
      }

      strokeRect(3, 17, 20, 23, palette.outline);
      fillRect(9, 19, 14, 19, palette.outline);

      const rects = [`<rect width="${size}" height="${size}" fill="${palette.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function generateAvatarSvgPixel(roleId, provider) {
      const key = normalizeAvatarProvider(provider);
      const style = avatarVariantInfo(roleId, key, 'pixel', 5);
      const variant = Number(style.variant || 0);
      const seed = `px-modern-human|${key}|${roleId}|${style.noise}`;
      const rng = seededRandom(hashText(seed));
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      const palettes = {
        system: {
          bg: '#0a0f16', bgShade: '#111a27', line: '#1f3147',
          skin: '#d6dcee', skinShade: '#b4bfd8',
          hair: '#b9c7e4', hairShade: '#8da0c5',
          eyeWhite: '#edf3ff', pupil: '#111b2c',
          cloth: '#3f526f', clothShade: '#2f4058',
          accent: '#9ec4ff', lip: '#8e9ab1',
        },
        claude: {
          bg: '#14100e', bgShade: '#1e1714', line: '#34261f',
          skin: '#efcaab', skinShade: '#d9aa85',
          hair: '#ebe7de', hairShade: '#ccc6bd',
          eyeWhite: '#faf6ef', pupil: '#1b140f',
          cloth: '#625a70', clothShade: '#4b4458',
          accent: '#d8c3a1', lip: '#9b6f62',
        },
        codex: {
          bg: '#0b121f', bgShade: '#121d32', line: '#26426a',
          skin: '#dcc2a3', skinShade: '#c19c78',
          hair: '#93abd6', hairShade: '#6d85b2',
          eyeWhite: '#ecf3ff', pupil: '#11233f',
          cloth: '#345383', clothShade: '#274064',
          accent: '#9fd2ff', lip: '#8f6f58',
        },
        gemini: {
          bg: '#171208', bgShade: '#231b0d', line: '#3d2f12',
          skin: '#e8c8a1', skinShade: '#cba073',
          hair: '#e7bc67', hairShade: '#b68d41',
          eyeWhite: '#fff4db', pupil: '#2b1f10',
          cloth: '#675133', clothShade: '#4c3b26',
          accent: '#ffd978', lip: '#9f744f',
        },
      };
      const p = palettes[key];

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      function drawEyes() {
        fillRect(9, 9, 10, 10, p.eyeWhite);
        fillRect(13, 9, 14, 10, p.eyeWhite);
        px(10, 10, p.pupil);
        px(14, 10, p.pupil);
      }

      fillRect(0, 0, 23, 23, p.bg);
      fillRect(0, 16, 23, 23, p.bgShade);
      for (let y = 0; y < size; y += 2) {
        for (let x = 0; x < size; x += 2) {
          if (rng() > 0.8) px(x, y, p.bgShade);
        }
      }

      for (let y = 0; y < size; y += 1) {
        px(0, y, p.line);
        px(size - 1, y, p.line);
      }
      for (let x = 0; x < size; x += 1) {
        px(x, 0, p.line);
        px(x, size - 1, p.line);
      }

      // Modern human portrait base.
      fillRect(6, 16, 17, 23, p.cloth);
      fillRect(8, 18, 15, 23, p.clothShade);
      fillRect(10, 14, 13, 16, p.skin);
      fillRect(10, 15, 13, 16, p.skinShade);

      fillRect(7, 6, 16, 15, p.skin);
      fillRect(8, 11, 15, 15, p.skinShade);
      fillRect(6, 8, 6, 12, p.skin);
      fillRect(17, 8, 17, 12, p.skin);
      fillRect(6, 9, 6, 12, p.skinShade);
      fillRect(17, 9, 17, 12, p.skinShade);

      drawEyes();
      fillRect(11, 10, 12, 12, p.skinShade);
      fillRect(10, 13, 13, 13, p.lip);
      strokeRect(7, 6, 16, 15, p.line);

      if (key === 'system') {
        // Modern ops headset.
        fillRect(6, 4, 17, 7, p.hair);
        fillRect(7, 8, 8, 11, p.hairShade);
        fillRect(15, 8, 16, 10, p.hairShade);
        fillRect(18, 8, 19, 12, p.accent);
        fillRect(18, 12, 20, 12, p.accent);
        fillRect(10, 18, 13, 18, p.accent);
      } else if (key === 'claude') {
        // Side-part + scarf.
        fillRect(6, 4, 17, 7, p.hair);
        fillRect(6, 8, 7, 13, p.hairShade);
        fillRect(14, 8, 17, 9, p.hairShade);
        fillRect(9, 18, 14, 18, p.accent);
        fillRect(11, 19, 12, 21, p.accent);
      } else if (key === 'codex') {
        // Hoodie + sleek visor.
        fillRect(5, 5, 18, 9, p.hairShade);
        fillRect(5, 10, 6, 14, p.hairShade);
        fillRect(17, 10, 18, 14, p.hairShade);
        fillRect(8, 8, 15, 9, p.accent);
        fillRect(10, 19, 13, 20, p.accent);
      } else {
        // Glasses + small forehead clip.
        fillRect(6, 4, 17, 7, p.hair);
        fillRect(7, 8, 16, 8, p.hairShade);
        strokeRect(8, 8, 10, 11, p.accent);
        strokeRect(13, 8, 15, 11, p.accent);
        fillRect(11, 9, 12, 9, p.accent);
        fillRect(11, 4, 12, 4, p.accent);
      }

      if (variant === 1) {
        // Variant: cleaner glasses profile.
        strokeRect(8, 9, 10, 10, p.accent);
        strokeRect(13, 9, 15, 10, p.accent);
        fillRect(11, 9, 12, 9, p.accent);
      } else if (variant === 2) {
        // Variant: stronger brow and jaw.
        fillRect(8, 7, 10, 7, p.line);
        fillRect(13, 7, 15, 7, p.line);
        fillRect(9, 14, 14, 15, p.skinShade);
      } else if (variant === 3) {
        // Variant: side comms headset.
        fillRect(5, 9, 6, 12, p.accent);
        fillRect(17, 9, 18, 12, p.accent);
        fillRect(18, 12, 20, 12, p.accent);
      } else if (variant === 4) {
        // Variant: chest badge + collar.
        fillRect(9, 18, 14, 18, p.accent);
        fillRect(10, 19, 13, 20, p.accent);
      }

      strokeRect(6, 16, 17, 23, p.line);

      const rects = [`<rect width="${size}" height="${size}" fill="${p.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function generateAvatarSvgStarWars(roleId, provider) {
      const key = normalizeAvatarProvider(provider);
      const style = avatarVariantInfo(roleId, key, 'pixel-sw', 5);
      const variant = Number(style.variant || 0);
      const seed = `sw|${key}|${roleId}|${style.noise}`;
      const rng = seededRandom(hashText(seed));
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      const palettes = {
        system: {
          bg: '#050812', bgShade: '#0b1230', outline: '#010102',
          skin: '#d2dcff', skinShade: '#a6b3d8', cloth: '#3a4e7f', clothShade: '#2b3b62',
          armor: '#7a8ebc', armorShade: '#5d6f99', visor: '#0a0f1e',
          saber: '#7cc7ff', saberCore: '#e0f5ff', accent: '#ffe081',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
        claude: {
          bg: '#060912', bgShade: '#0f1530', outline: '#010102',
          skin: '#f1c9a3', skinShade: '#d8a77f', cloth: '#7b5d38', clothShade: '#5b4329',
          armor: '#3a4f66', armorShade: '#2a3b4f', visor: '#0a0d14',
          saber: '#59b8ff', saberCore: '#e5f5ff', accent: '#ffe081',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
        codex: {
          bg: '#05070f', bgShade: '#1a1022', outline: '#010102',
          skin: '#d8dfe8', skinShade: '#b5c0cf', cloth: '#2f3448', clothShade: '#222637',
          armor: '#e6ebf2', armorShade: '#bfc9d8', visor: '#131822',
          saber: '#ff6262', saberCore: '#ffdada', accent: '#9fb4ff',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
        gemini: {
          bg: '#09070f', bgShade: '#161327', outline: '#010102',
          skin: '#d2b26a', skinShade: '#a1844c', cloth: '#5f4b2a', clothShade: '#45361d',
          armor: '#b3bcc9', armorShade: '#8893a3', visor: '#13141b',
          saber: '#ffc857', saberCore: '#fff2cf', accent: '#72b9ff',
          starA: '#dfe7ff', starB: '#ffe8a8', starC: '#95c7ff',
        },
      };
      const p = palettes[key];

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      function drawVerticalSaber(x, y1, y2, glowColor, coreColor) {
        for (let y = y1; y <= y2; y += 1) {
          px(x, y, coreColor);
          px(x - 1, y, glowColor);
          px(x + 1, y, glowColor);
        }
        fillRect(x - 1, y2 + 1, x + 1, y2 + 2, p.outline);
      }

      function drawEyes(x1, x2, y) {
        fillRect(x1, y, x1 + 1, y, p.visor);
        fillRect(x2, y, x2 + 1, y, p.visor);
      }

      fillRect(0, 0, 23, 23, p.bg);
      fillRect(0, 16, 23, 23, p.bgShade);
      for (let i = 0; i < 34; i += 1) {
        const sx = Math.floor(rng() * 24);
        const sy = Math.floor(rng() * 24);
        const starColor = (i % 3 === 0) ? p.starA : (i % 3 === 1 ? p.starB : p.starC);
        if (rng() > 0.55) {
          px(sx, sy, starColor);
        }
      }

      if (key === 'claude') {
        // Hooded Jedi-like role (blue saber)
        fillRect(6, 15, 17, 23, p.cloth);
        fillRect(8, 17, 15, 23, p.clothShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(6, 5, 17, 8, p.cloth);
        fillRect(7, 7, 8, 12, p.cloth);
        fillRect(15, 7, 16, 12, p.cloth);
        fillRect(9, 4, 14, 5, p.clothShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.visor);
        strokeRect(6, 5, 17, 23, p.outline);
        drawVerticalSaber(20, 8, 20, p.saber, p.saberCore);
      } else if (key === 'codex') {
        // Trooper-like role (red saber)
        fillRect(7, 15, 16, 23, p.armor);
        fillRect(8, 17, 15, 23, p.armorShade);
        fillRect(7, 6, 16, 14, p.armor);
        fillRect(8, 7, 15, 13, p.skin);
        fillRect(8, 8, 15, 10, p.visor);
        fillRect(9, 11, 14, 13, p.skinShade);
        fillRect(9, 6, 14, 6, p.armorShade);
        fillRect(7, 11, 7, 12, p.armorShade);
        fillRect(16, 11, 16, 12, p.armorShade);
        fillRect(10, 18, 13, 19, p.visor);
        strokeRect(7, 6, 16, 23, p.outline);
        drawVerticalSaber(3, 8, 20, p.saber, p.saberCore);
      } else if (key === 'gemini') {
        // Droid-like role with protocol colors
        fillRect(8, 15, 15, 23, p.cloth);
        fillRect(9, 17, 14, 23, p.clothShade);
        fillRect(8, 6, 15, 14, p.armor);
        fillRect(9, 7, 14, 12, p.skin);
        fillRect(10, 8, 11, 8, p.visor);
        fillRect(12, 8, 13, 8, p.visor);
        fillRect(10, 10, 13, 10, p.accent);
        fillRect(9, 13, 14, 14, p.armorShade);
        px(11, 4, p.accent);
        px(12, 4, p.accent);
        fillRect(11, 5, 12, 5, p.armorShade);
        strokeRect(8, 6, 15, 23, p.outline);
      } else {
        // System command/hologram role
        fillRect(7, 15, 16, 23, p.cloth);
        fillRect(8, 17, 15, 23, p.clothShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 7, 14, 13, p.skinShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.visor);
        fillRect(10, 18, 13, 19, p.accent);
        strokeRect(7, 6, 16, 23, p.outline);
        drawVerticalSaber(19, 9, 20, p.saber, p.saberCore);
      }

      if (variant === 1) {
        // Variant: shoulder pauldron.
        fillRect(5, 13, 8, 15, p.armor);
        fillRect(15, 13, 18, 15, p.armorShade);
      } else if (variant === 2) {
        // Variant: tactical chest panel.
        fillRect(9, 17, 14, 19, p.accent);
        fillRect(10, 18, 13, 18, p.visor);
      } else if (variant === 3) {
        // Variant: comm antenna.
        fillRect(4, 5, 4, 13, p.armorShade);
        fillRect(3, 5, 5, 6, p.accent);
      } else if (variant === 4) {
        // Variant: visor stripe and belt nodes.
        fillRect(8, 8, 15, 8, p.accent);
        px(9, 20, p.accent);
        px(12, 20, p.accent);
        px(15, 20, p.accent);
      }

      const rects = [`<rect width="${size}" height="${size}" fill="${p.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function generateAvatarSvgThreeKingdoms(roleId, provider) {
      const key = normalizeAvatarProvider(provider);
      const style = avatarVariantInfo(roleId, key, 'pixel-sg', 5);
      const variant = Number(style.variant || 0);
      const seed = `sg|${key}|${roleId}|${style.noise}`;
      const rng = seededRandom(hashText(seed));
      const size = 24;
      const grid = Array.from({ length: size }, () => Array(size).fill(null));

      const palettes = {
        system: {
          bg: '#1b0f08', bgShade: '#2a130c', outline: '#050302',
          skin: '#efcdaa', skinShade: '#d7ac86',
          robe: '#6e1f1f', robeShade: '#511414',
          hat: '#d4a85f', hatShade: '#9a7335',
          eye: '#170d09', prop: '#b0533b', propShade: '#7f2f22',
          fan: '#efdec0', fanShade: '#d8c3a0',
          scroll: '#ead7af', scrollBand: '#ae7f43',
          plume: '#c74a3d', crown: '#d4a85f', crownShade: '#9a7335',
          seal: '#b94732', sealShade: '#7a291f',
          sparkA: '#f2cc92', sparkB: '#9ab4df',
        },
        claude: {
          bg: '#120f12', bgShade: '#1d1520', outline: '#050505',
          skin: '#efcfad', skinShade: '#d8ad87',
          robe: '#365f92', robeShade: '#28496f',
          hat: '#1c1c28', hatShade: '#2b2b39',
          eye: '#0f1015', prop: '#8c6c3c', propShade: '#664f2c',
          fan: '#efe7d4', fanShade: '#c9baa2',
          scroll: '#e6d2a5', scrollBand: '#a9793f',
          plume: '#d16d54', crown: '#c89c58', crownShade: '#906f38',
          seal: '#ab4b39', sealShade: '#742a21',
          sparkA: '#dfc28f', sparkB: '#9ab4df',
        },
        codex: {
          bg: '#140c08', bgShade: '#23120c', outline: '#040302',
          skin: '#e5c19f', skinShade: '#c99a75',
          robe: '#5d2f23', robeShade: '#452217',
          hat: '#6a727f', hatShade: '#4e5560',
          eye: '#130b08', prop: '#cb9d5c', propShade: '#7d5d2e',
          fan: '#ecd7b2', fanShade: '#ccb28b',
          scroll: '#e5d09f', scrollBand: '#a67332',
          plume: '#c83f3a', crown: '#c29b5c', crownShade: '#8f6f3a',
          seal: '#ad4430', sealShade: '#73261d',
          sparkA: '#e5bf83', sparkB: '#a4b8d8',
        },
        gemini: {
          bg: '#0e1116', bgShade: '#151a23', outline: '#040507',
          skin: '#e4c5a2', skinShade: '#c7a077',
          robe: '#2f4c72', robeShade: '#223954',
          hat: '#3f4f68', hatShade: '#2f3b4e',
          eye: '#0b1016', prop: '#84a7d8', propShade: '#5e79a4',
          fan: '#e8d7b6', fanShade: '#c9b28f',
          scroll: '#e8d3a2', scrollBand: '#b47d3a',
          plume: '#c66955', crown: '#c7a061', crownShade: '#8f6d3a',
          seal: '#a64b3d', sealShade: '#6d2c24',
          sparkA: '#d8bf8d', sparkB: '#9cb7e7',
        },
      };
      const p = palettes[key];

      function px(x, y, color) {
        if (x < 0 || y < 0 || x >= size || y >= size) return;
        grid[y][x] = color;
      }

      function fillRect(x1, y1, x2, y2, color) {
        for (let y = y1; y <= y2; y += 1) {
          for (let x = x1; x <= x2; x += 1) {
            px(x, y, color);
          }
        }
      }

      function strokeRect(x1, y1, x2, y2, color) {
        for (let x = x1; x <= x2; x += 1) {
          px(x, y1, color);
          px(x, y2, color);
        }
        for (let y = y1; y <= y2; y += 1) {
          px(x1, y, color);
          px(x2, y, color);
        }
      }

      function drawEyes(x1, x2, y) {
        fillRect(x1, y, x1 + 1, y, p.eye);
        fillRect(x2, y, x2 + 1, y, p.eye);
      }

      fillRect(0, 0, 23, 23, p.bg);
      fillRect(0, 16, 23, 23, p.bgShade);
      for (let i = 0; i < 30; i += 1) {
        const sx = Math.floor(rng() * 24);
        const sy = Math.floor(rng() * 24);
        const sparkle = i % 2 === 0 ? p.sparkA : p.sparkB;
        if (rng() > 0.58) {
          px(sx, sy, sparkle);
        }
      }

      if (key === 'claude') {
        // Strategist: scholar robe + feather fan.
        fillRect(6, 15, 17, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(7, 4, 16, 6, p.hat);
        fillRect(9, 3, 14, 3, p.hatShade);
        fillRect(6, 5, 6, 8, p.hat);
        fillRect(17, 5, 17, 8, p.hat);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        strokeRect(6, 6, 17, 23, p.outline);

        fillRect(1, 10, 6, 14, p.fan);
        fillRect(2, 11, 5, 13, p.fanShade);
        fillRect(3, 11, 3, 13, p.outline);
        fillRect(4, 11, 4, 13, p.outline);
        fillRect(6, 12, 7, 12, p.prop);
        fillRect(7, 11, 7, 13, p.prop);
      } else if (key === 'codex') {
        // General: helmet + plume + halberd.
        fillRect(7, 15, 16, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 13, p.skin);
        fillRect(9, 11, 14, 13, p.skinShade);
        fillRect(7, 5, 16, 7, p.hat);
        fillRect(8, 8, 15, 9, p.hatShade);
        fillRect(7, 8, 8, 11, p.hat);
        fillRect(15, 8, 16, 11, p.hat);
        fillRect(10, 2, 13, 4, p.plume);
        fillRect(11, 1, 12, 1, p.plume);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        strokeRect(7, 6, 16, 23, p.outline);

        fillRect(20, 7, 20, 21, p.propShade);
        fillRect(19, 7, 21, 8, p.prop);
        fillRect(19, 6, 19, 7, p.prop);
        fillRect(21, 6, 21, 7, p.prop);
        fillRect(18, 9, 19, 10, p.plume);
      } else if (key === 'gemini') {
        // Diplomat: robe + scroll + brush.
        fillRect(7, 15, 16, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(7, 4, 16, 6, p.hat);
        fillRect(9, 3, 14, 3, p.hatShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        strokeRect(7, 6, 16, 23, p.outline);

        fillRect(18, 10, 21, 15, p.scroll);
        fillRect(19, 12, 20, 13, p.scrollBand);
        fillRect(18, 9, 18, 10, p.scrollBand);
        fillRect(21, 15, 21, 16, p.scrollBand);
        fillRect(2, 11, 4, 11, p.propShade);
        fillRect(4, 10, 4, 12, p.propShade);
        fillRect(0, 10, 1, 12, p.prop);
      } else {
        // Commander: imperial crown + seal token.
        fillRect(7, 15, 16, 23, p.robe);
        fillRect(8, 17, 15, 23, p.robeShade);
        fillRect(8, 6, 15, 14, p.skin);
        fillRect(9, 11, 14, 14, p.skinShade);
        fillRect(8, 4, 15, 5, p.crown);
        px(9, 3, p.crown);
        px(11, 2, p.crown);
        px(13, 3, p.crown);
        px(15, 3, p.crown);
        fillRect(10, 6, 13, 6, p.crownShade);
        drawEyes(9, 13, 9);
        fillRect(10, 12, 13, 12, p.eye);
        fillRect(10, 18, 13, 20, p.seal);
        fillRect(11, 19, 12, 19, p.sealShade);
        strokeRect(7, 6, 16, 23, p.outline);

        fillRect(3, 7, 3, 20, p.propShade);
        fillRect(1, 7, 2, 9, p.prop);
        fillRect(1, 10, 2, 12, p.propShade);
      }

      if (variant === 1) {
        // Variant: shoulder cape layers.
        fillRect(6, 14, 8, 17, p.robeShade);
        fillRect(15, 14, 17, 17, p.robeShade);
      } else if (variant === 2) {
        // Variant: formal chest knot.
        fillRect(10, 17, 13, 18, p.prop);
        fillRect(11, 19, 12, 20, p.propShade);
      } else if (variant === 3) {
        // Variant: hairpin/crown trim.
        fillRect(9, 3, 14, 3, p.crownShade);
        px(11, 2, p.crown);
        px(12, 2, p.crown);
      } else if (variant === 4) {
        // Variant: side ornament and belt seal.
        fillRect(19, 10, 21, 12, p.seal);
        fillRect(20, 11, 20, 11, p.sealShade);
        fillRect(9, 20, 14, 20, p.scrollBand);
      }

      const rects = [`<rect width="${size}" height="${size}" fill="${p.bg}"></rect>`];
      for (let y = 0; y < size; y += 1) {
        for (let x = 0; x < size; x += 1) {
          const color = grid[y][x];
          if (!color) continue;
          rects.push(`<rect x="${x}" y="${y}" width="1" height="1" fill="${color}"></rect>`);
        }
      }
      return `<svg viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${rects.join('')}</svg>`;
    }

    function avatarHtml(roleId, provider, className = 'role-avatar') {
      return `<span class="${className}" aria-hidden="true">${generateAvatarSvg(roleId, provider)}</span>`;
    }

    function roleAvatarHtml(roleId, provider) {
      return avatarHtml(roleId, provider, 'role-avatar');
    }

    function eventActorProvider(event, actor) {
      const payload = event.payload || {};
      if (payload.provider) return parseProvider(String(payload.provider));
      const participant = payload.participant ? String(payload.participant) : '';
      if (participant.includes('#')) return parseProvider(participant);
      const actorText = String(actor || '');
      if (actorText.includes('#')) return parseProvider(actorText);
      if (actorText && actorText !== 'system') return actorText;
      return 'system';
    }

    function statusPill(status) {
      const text = String(status || 'unknown');
      if (text === 'passed') return `<span class="pill ok">${text}</span>`;
      if (['failed_gate', 'failed_system', 'canceled'].includes(text)) return `<span class="pill warn">${text}</span>`;
      return `<span class="pill">${text}</span>`;
    }

    function isActiveStatus(status) {
      return ['running', 'queued', 'waiting_manual'].includes(String(status || ''));
    }

    function projectGroups() {
      const map = new Map();
      for (const task of state.tasks) {
        const key = normalizeProjectPath(task.project_path || task.workspace_path);
        let g = map.get(key);
        if (!g) {
          g = { key, name: projectName(key), tasks: [], active: 0, passed: 0, failed: 0, history: 0 };
          map.set(key, g);
        }
        g.tasks.push(task);
        g.history += 1;
        if (isActiveStatus(task.status)) g.active += 1;
        if (task.status === 'passed') g.passed += 1;
        if (['failed_gate', 'failed_system'].includes(task.status)) g.failed += 1;
      }
      for (const item of state.historyItems || []) {
        const key = normalizeProjectPath(item.project_path || '');
        if (!key) continue;
        let g = map.get(key);
        if (!g) {
          g = { key, name: projectName(key), tasks: [], active: 0, passed: 0, failed: 0, history: 0 };
          map.set(key, g);
        }
        g.history += 1;
        if (item.status === 'passed') g.passed += 1;
        if (['failed_gate', 'failed_system'].includes(item.status)) g.failed += 1;
      }
      return Array.from(map.values()).sort((a, b) => {
        if (b.active !== a.active) return b.active - a.active;
        if (b.history !== a.history) return b.history - a.history;
        if (b.tasks.length !== a.tasks.length) return b.tasks.length - a.tasks.length;
        return a.name.localeCompare(b.name);
      });
    }

    function participantInTask(task, roleId) {
      const id = String(roleId || '').trim();
      if (!id) return false;
      if (String(task.author_participant || '') === id) return true;
      const reviewers = Array.isArray(task.reviewer_participants) ? task.reviewer_participants : [];
      return reviewers.some((v) => String(v || '').trim() === id);
    }

    function participantOverrideLookup(map, roleId) {
      const source = map && typeof map === 'object' ? map : {};
      const key = String(roleId || '').trim();
      if (!key) return '';
      const exact = String(source[key] || '').trim();
      if (exact) return exact;
      const lowered = String(source[key.toLowerCase()] || '').trim();
      if (lowered) return lowered;
      return '';
    }

    function resolveRoleModelInfo(roleId, provider) {
      const providerKey = String(provider || '').trim().toLowerCase();
      const selected = selectedTask();
      if (selected && participantInTask(selected, roleId)) {
        const selectedParticipantModels = selected.participant_models || {};
        const selectedParticipantParams = selected.participant_model_params || {};
        const selectedModels = selected.provider_models || {};
        const selectedParams = selected.provider_model_params || {};
        const model = participantOverrideLookup(selectedParticipantModels, roleId) || String(selectedModels[providerKey] || '').trim();
        const params = participantOverrideLookup(selectedParticipantParams, roleId) || String(selectedParams[providerKey] || '').trim();
        if (model || params) {
          return { model, params };
        }
      }

      for (const task of state.tasks) {
        if (!participantInTask(task, roleId)) continue;
        const participantModels = task.participant_models || {};
        const participantParams = task.participant_model_params || {};
        const models = task.provider_models || {};
        const paramsMap = task.provider_model_params || {};
        const model = participantOverrideLookup(participantModels, roleId) || String(models[providerKey] || '').trim();
        const params = participantOverrideLookup(participantParams, roleId) || String(paramsMap[providerKey] || '').trim();
        if (model || params) {
          return { model, params };
        }
      }

      const catalog = state.providerModelCatalog || {};
      const fallback = Array.isArray(catalog[providerKey]) ? catalog[providerKey] : [];
      return { model: fallback.length ? String(fallback[0]) : '', params: '' };
    }

    function roleGroups() {
      const scopedTasks = tasksInSelectedProject();
      const selected = selectedTask();
      const visibleParticipants = new Set();
      if (selected) {
        const seed = [selected.author_participant, ...(selected.reviewer_participants || [])];
        for (const id of seed) {
          const pid = String(id || '').trim();
          if (pid) visibleParticipants.add(pid);
        }
      } else {
        for (const task of scopedTasks) {
          const seed = [task.author_participant, ...(task.reviewer_participants || [])];
          for (const id of seed) {
            const pid = String(id || '').trim();
            if (pid) visibleParticipants.add(pid);
          }
        }
      }

      const sourceTasks = (selected && selected._history_only)
        ? []
        : (scopedTasks.length ? scopedTasks : state.tasks);
      const map = new Map();
      for (const task of sourceTasks) {
        const participants = [task.author_participant, ...(task.reviewer_participants || [])];
        for (const id of participants) {
          const pid = String(id || '').trim();
          if (!pid) continue;
          if (visibleParticipants.size && !visibleParticipants.has(pid)) continue;
          let role = map.get(pid);
          if (!role) {
            role = { id: pid, provider: parseProvider(pid), tasks: 0, active: 0 };
            map.set(pid, role);
          }
          role.tasks += 1;
          if (isActiveStatus(task.status)) role.active += 1;
        }
      }
      for (const role of map.values()) {
        const info = resolveRoleModelInfo(role.id, role.provider);
        role.model = info.model || '';
        role.modelParams = info.params || '';
      }
      return Array.from(map.values()).sort((a, b) => {
        if (b.active !== a.active) return b.active - a.active;
        if (b.tasks !== a.tasks) return b.tasks - a.tasks;
        return a.id.localeCompare(b.id);
      });
    }

    function tasksInSelectedProject() {
      const path = state.selectedProject;
      if (!path) return [...state.tasks];
      return state.tasks.filter((task) => normalizeProjectPath(task.project_path || task.workspace_path) === path);
    }

    function makeHistoryOnlyTask(item) {
      const taskId = String(item.task_id || '').trim();
      const projectPath = String(item.project_path || '').trim();
      const status = String(item.status || 'unknown').trim() || 'unknown';
      const historyStamp = String(item.updated_at || item.created_at || '').trim();
      return {
        task_id: taskId,
        title: String(item.title || `Task ${taskId}`).trim() || `Task ${taskId}`,
        description: '',
        author_participant: '',
        reviewer_participants: [],
        evolution_level: 0,
        evolve_until: null,
        conversation_language: 'en',
        provider_models: {},
        provider_model_params: {},
        participant_models: {},
        participant_model_params: {},
        claude_team_agents: false,
        codex_multi_agents: false,
        claude_team_agents_overrides: {},
        codex_multi_agents_overrides: {},
        repair_mode: 'balanced',
        plain_mode: true,
        stream_mode: true,
        debate_mode: true,
        sandbox_mode: false,
        sandbox_workspace_path: null,
        sandbox_generated: false,
        sandbox_cleanup_on_pass: false,
        self_loop_mode: 0,
        project_path: projectPath,
        auto_merge: !!(item.revisions && item.revisions.auto_merge),
        merge_target_path: null,
        workspace_path: projectPath,
        status,
        last_gate_reason: item.last_gate_reason || null,
        max_rounds: 1,
        test_command: '',
        lint_command: '',
        rounds_completed: 0,
        cancel_requested: false,
        created_at: String(item.created_at || '').trim(),
        updated_at: String(item.updated_at || '').trim(),
        _history_only: true,
        _history_stamp: historyStamp,
      };
    }

    function historyOnlyTasksInSelectedProject() {
      const liveIds = new Set(state.tasks.map((task) => String(task.task_id || '').trim()).filter(Boolean));
      const path = state.selectedProject;
      const out = [];
      for (const item of state.historyItems || []) {
        const taskId = String(item.task_id || '').trim();
        if (!taskId || liveIds.has(taskId)) continue;
        const itemProject = normalizeProjectPath(item.project_path || '');
        if (path && itemProject !== path) continue;
        out.push(makeHistoryOnlyTask(item));
      }
      return out;
    }

    function taskSortPriority(status) {
      const text = String(status || '').trim().toLowerCase();
      if (text === 'running') return 30;
      if (text === 'waiting_manual') return 20;
      if (text === 'queued') return 10;
      return 0;
    }

    function taskSortStamp(task) {
      const raw = String(
        task.updated_at
        || task.created_at
        || task._history_stamp
        || ''
      ).trim();
      if (!raw) return 0;
      const dt = parseEventDate(raw);
      if (Number.isNaN(dt.getTime())) return 0;
      return dt.getTime();
    }

    function taskChoicesInSelectedProject() {
      const combined = [...tasksInSelectedProject(), ...historyOnlyTasksInSelectedProject()];
      combined.sort((a, b) => {
        const sourceDiff = Number(!!a._history_only) - Number(!!b._history_only);
        if (sourceDiff !== 0) return sourceDiff;
        const prioDiff = taskSortPriority(b.status) - taskSortPriority(a.status);
        if (prioDiff !== 0) return prioDiff;
        const stampDiff = taskSortStamp(b) - taskSortStamp(a);
        if (stampDiff !== 0) return stampDiff;
        return String(a.task_id || '').localeCompare(String(b.task_id || ''));
      });
      return combined;
    }

    function ensureSelections() {
      const projects = projectGroups();
      if (!projects.length) {
        state.selectedProject = null;
        state.selectedTaskId = null;
        state.selectedRole = 'all';
        persistSelectionPreference();
        return;
      }

      if (!state.selectedProject || !projects.some((p) => p.key === state.selectedProject)) {
        state.selectedProject = projects[0].key;
      }

      const scoped = taskChoicesInSelectedProject();
      if (!scoped.length) {
        state.selectedTaskId = null;
        state.selectedRole = 'all';
        persistSelectionPreference();
        return;
      }

      if (!state.selectedTaskId || !scoped.some((t) => t.task_id === state.selectedTaskId)) {
        const liveScoped = scoped.filter((t) => !t._history_only);
        const runningLive = liveScoped.find((t) => String(t.status || '') === 'running');
        const runningAny = scoped.find((t) => String(t.status || '') === 'running');
        state.selectedTaskId = (runningLive || liveScoped[0] || runningAny || scoped[0]).task_id;
      }

      if (state.selectionNeedsValidation) {
        const current = scoped.find((t) => t.task_id === state.selectedTaskId) || null;
        const liveScoped = scoped.filter((t) => !t._history_only);
        if (current && current._history_only && liveScoped.length) {
          const runningLive = liveScoped.find((t) => String(t.status || '') === 'running');
          state.selectedTaskId = (runningLive || liveScoped[0]).task_id;
        }
        state.selectionNeedsValidation = false;
      }

      const current = scoped.find((t) => t.task_id === state.selectedTaskId) || null;
      if (state.selectedRole !== 'all' && current && !participantInTask(current, state.selectedRole)) {
        state.selectedRole = 'all';
      }
      persistSelectionPreference();
    }

    function selectedTask() {
      if (!state.selectedTaskId) return null;
      const key = String(state.selectedTaskId || '').trim();
      if (!key) return null;
      const live = state.tasks.find((task) => String(task.task_id || '').trim() === key);
      if (live) return live;

      const history = (state.historyItems || []).find((item) => String(item.task_id || '').trim() === key);
      if (!history) return null;
      return makeHistoryOnlyTask(history);
    }

    function renderProjectSelector() {
      const projects = projectGroups();
      el.projectSelect.innerHTML = '';
      if (!projects.length) {
        const option = document.createElement('option');
        option.textContent = 'No project';
        option.value = '';
        el.projectSelect.appendChild(option);
        el.projectSelect.disabled = true;
        return;
      }

      el.projectSelect.disabled = false;
      for (const project of projects) {
        const option = document.createElement('option');
        option.value = project.key;
        option.textContent = `${project.name} | tasks=${project.tasks.length} history=${project.history} active=${project.active}`;
        if (project.key === state.selectedProject) {
          option.selected = true;
        }
        el.projectSelect.appendChild(option);
      }
    }

    async function loadProjectTree(projectPath, { force = false } = {}) {
      if (!projectPath) {
        return null;
      }
      if (!force && state.treeByProject.has(projectPath)) {
        return state.treeByProject.get(projectPath) || null;
      }
      const pathParam = encodeURIComponent(projectPath);
      const data = await api(
        `/api/workspace-tree?workspace_path=${pathParam}&max_depth=4&max_entries=800`,
        { healthImpact: false },
      );
      state.treeByProject.set(projectPath, data);
      return data;
    }

    function treeOpenStateFor(projectPath) {
      const key = normalizeProjectPath(projectPath);
      if (!state.treeOpenByProject.has(key)) {
        state.treeOpenByProject.set(key, new Map());
      }
      return state.treeOpenByProject.get(key);
    }

    function buildTreeHierarchy(tree) {
      const workspace = normalizeProjectPath(tree.workspace_path).replace(/\\/g, '/');
      const root = {
        path: workspace,
        kind: 'dir',
        depth: -1,
        name: projectName(workspace),
        children: [],
      };
      const stack = [root];
      for (const raw of tree.nodes || []) {
        const path = normalizeProjectPath(raw.path).replace(/\\/g, '/');
        if (!path) continue;
        const depth = Math.max(0, Number(raw.depth || 0));
        const kind = raw.kind === 'dir' ? 'dir' : 'file';
        if (depth === 0 && path === workspace) {
          continue;
        }
        const node = {
          path,
          kind,
          depth,
          name: treeNodeLabel(path),
          children: [],
        };
        while (stack.length > depth + 1) {
          stack.pop();
        }
        const parent = stack[stack.length - 1] || root;
        parent.children.push(node);
        if (kind === 'dir') {
          stack.push(node);
        }
      }
      return root.children;
    }

    function renderTreeBranch(nodes, depth, dirState) {
      const branch = document.createElement('ul');
      branch.className = depth > 0 ? 'tree-branch nested' : 'tree-branch';
      for (const node of nodes) {
        const item = document.createElement('li');
        item.className = `tree-item ${node.kind}`;
        if (node.kind === 'dir') {
          const details = document.createElement('details');
          details.className = 'tree-folder';
          details.dataset.path = node.path;
          details.open = dirState.has(node.path) ? !!dirState.get(node.path) : depth < 1;
          details.addEventListener('toggle', () => {
            dirState.set(node.path, details.open);
          });

          const summary = document.createElement('summary');
          summary.className = 'tree-entry dir';
          summary.title = node.path;

          const caret = document.createElement('span');
          caret.className = 'tree-caret';
          caret.textContent = '>';

          const icon = document.createElement('span');
          icon.className = 'tree-icon';
          icon.textContent = 'D';

          const name = document.createElement('span');
          name.className = 'tree-name';
          name.textContent = node.name;

          summary.appendChild(caret);
          summary.appendChild(icon);
          summary.appendChild(name);
          details.appendChild(summary);

          if (node.children.length) {
            details.appendChild(renderTreeBranch(node.children, depth + 1, dirState));
          } else {
            const empty = document.createElement('div');
            empty.className = 'tree-leaf-empty';
            empty.textContent = '(empty)';
            details.appendChild(empty);
          }
          item.appendChild(details);
        } else {
          const entry = document.createElement('div');
          entry.className = 'tree-entry file';
          entry.title = node.path;

          const pad = document.createElement('span');
          pad.className = 'tree-pad';
          pad.textContent = ' ';

          const icon = document.createElement('span');
          icon.className = 'tree-icon';
          icon.textContent = 'F';

          const name = document.createElement('span');
          name.className = 'tree-name';
          name.textContent = node.name;

          entry.appendChild(pad);
          entry.appendChild(icon);
          entry.appendChild(name);
          item.appendChild(entry);
        }
        branch.appendChild(item);
      }
      return branch;
    }

    function setTreeExpansion(open) {
      const project = normalizeProjectPath(state.selectedProject);
      const dirState = treeOpenStateFor(project);
      const folders = el.projectTree.querySelectorAll('details.tree-folder');
      folders.forEach((folder) => {
        folder.open = open;
        const path = folder.dataset.path;
        if (path) {
          dirState.set(path, open);
        }
      });
    }

    function renderProjectTree(tree) {
      el.projectTree.innerHTML = '';
      if (!tree || !Array.isArray(tree.nodes)) {
        el.projectTreeMeta.textContent = 'No project tree available.';
        el.projectTree.innerHTML = '<div class="tree-empty">Select a project to load structure.</div>';
        if (el.expandTreeBtn) el.expandTreeBtn.disabled = true;
        if (el.collapseTreeBtn) el.collapseTreeBtn.disabled = true;
        return;
      }

      const extra = tree.truncated ? ' (truncated)' : '';
      el.projectTreeMeta.textContent = `root=${tree.workspace_path} | entries=${tree.total_entries}${extra}`;
      const hierarchy = buildTreeHierarchy(tree);
      if (!hierarchy.length) {
        el.projectTree.innerHTML = '<div class="tree-empty">Project is empty.</div>';
        if (el.expandTreeBtn) el.expandTreeBtn.disabled = true;
        if (el.collapseTreeBtn) el.collapseTreeBtn.disabled = true;
        return;
      }
      const dirState = treeOpenStateFor(tree.workspace_path);
      el.projectTree.appendChild(renderTreeBranch(hierarchy, 0, dirState));
      if (el.expandTreeBtn) el.expandTreeBtn.disabled = false;
      if (el.collapseTreeBtn) el.collapseTreeBtn.disabled = false;
    }

    function renderRoles() {
      const roles = roleGroups();
      if (state.selectedRole !== 'all' && !roles.some((r) => r.id === state.selectedRole)) {
        state.selectedRole = 'all';
      }
      el.roleList.innerHTML = '';

      const allBtn = document.createElement('button');
      if (state.selectedRole === 'all') allBtn.classList.add('active');
      allBtn.dataset.role = 'all';
      allBtn.innerHTML = `
        <div class="item-top">
          <span class="role-main">
            ${roleAvatarHtml('all-roles', 'system')}
            <span class="item-name">all roles</span>
          </span>
          <span class="pill">${roles.length}</span>
        </div>
        <div class="item-meta">Show full conversation stream.</div>
      `;
      el.roleList.appendChild(allBtn);

      for (const role of roles) {
        const button = document.createElement('button');
        if (state.selectedRole === role.id) button.classList.add('active');
        button.dataset.role = role.id;
        button.innerHTML = `
          <div class="item-top">
            <span class="role-main">
              ${roleAvatarHtml(role.id, role.provider)}
              <span class="item-name">${escapeHtml(role.id)}</span>
            </span>
            <span class="pill">${escapeHtml(role.provider)}</span>
          </div>
          <div class="item-meta">
            <span>tasks: ${role.tasks}</span>
            <span>active: ${role.active}</span>
            <span>model: ${escapeHtml(role.model || 'n/a')}</span>
          </div>
        `;
        el.roleList.appendChild(button);
      }
    }

    function renderTaskSelect() {
      const scoped = taskChoicesInSelectedProject();
      el.taskSelect.innerHTML = '';
      if (!scoped.length) {
        const option = document.createElement('option');
        option.textContent = 'No task in selected project';
        option.value = '';
        el.taskSelect.appendChild(option);
        el.taskSelect.disabled = true;
        return;
      }

      el.taskSelect.disabled = false;
      for (const task of scoped) {
        const option = document.createElement('option');
        option.value = task.task_id;
        const sourceTag = task._history_only ? ' | history' : '';
        option.textContent = `${task.task_id} | ${task.title}${sourceTag}`;
        if (task.task_id === state.selectedTaskId) {
          option.selected = true;
        }
        el.taskSelect.appendChild(option);
      }
    }

    function renderTaskSnapshot() {
      const task = selectedTask();
      if (!task) {
        el.taskSnapshot.innerHTML = '<div class="empty">No task selected.</div>';
        if (el.startBtn) el.startBtn.disabled = true;
        if (el.cancelBtn) el.cancelBtn.disabled = true;
        if (el.forceFailBtn) el.forceFailBtn.disabled = true;
        if (el.promoteRoundBtn) el.promoteRoundBtn.disabled = true;
        if (el.promoteRound) el.promoteRound.disabled = true;
        if (el.approveQueueBtn) el.approveQueueBtn.disabled = true;
        if (el.approveStartBtn) el.approveStartBtn.disabled = true;
        if (el.rejectBtn) el.rejectBtn.disabled = true;
        return;
      }

      const historyOnly = !!task._history_only;
      const project = normalizeProjectPath(task.workspace_path);
      const reviewerCount = (task.reviewer_participants || []).length;
      el.taskSnapshot.innerHTML = '';

      const boxes = [
        { label: 'TaskSource', value: historyOnly ? 'history (read-only)' : 'live' },
        { label: 'TaskID', value: task.task_id },
        { label: 'RoundProgress', value: `${task.rounds_completed}/${task.max_rounds}` },
        { label: 'Status', value: task.status, html: statusPill(task.status) },
        { label: 'ProjectPath', value: task.project_path || project },
        { label: 'Workspace', value: project },
        { label: 'ConversationLang', value: String(task.conversation_language || 'en') },
        { label: 'Sandbox', value: String(task.sandbox_mode ? 1 : 0) },
        { label: 'SandboxPath', value: task.sandbox_workspace_path || 'n/a' },
        { label: 'SelfLoop', value: String(task.self_loop_mode ?? 1) },
        { label: 'Evolution', value: String(task.evolution_level ?? 0) },
        { label: 'RepairMode', value: String(task.repair_mode || 'balanced') },
        { label: 'PlainMode', value: String(task.plain_mode !== false ? 1 : 0) },
        { label: 'StreamMode', value: String(task.stream_mode !== false ? 1 : 0) },
        { label: 'DebateMode', value: String(task.debate_mode !== false ? 1 : 0) },
        { label: 'EvolveUntil', value: task.evolve_until || 'n/a' },
        { label: 'ProviderModels', value: formatProviderModels(task.provider_models) },
        { label: 'ProviderModelParams', value: formatProviderModelParams(task.provider_model_params) },
        { label: 'ParticipantModels', value: formatParticipantModels(task.participant_models) },
        { label: 'ParticipantModelParams', value: formatParticipantModelParams(task.participant_model_params) },
        { label: 'ClaudeAgents', value: String(task.claude_team_agents ? 1 : 0) },
        { label: 'CodexMultiAgents', value: String(task.codex_multi_agents ? 1 : 0) },
        { label: 'ClaudeAgentsOverrides', value: formatParticipantBoolOverrides(task.claude_team_agents_overrides) },
        { label: 'CodexMultiOverrides', value: formatParticipantBoolOverrides(task.codex_multi_agents_overrides) },
        { label: 'AutoMerge', value: String(task.auto_merge !== false ? 1 : 0) },
        { label: 'MergeTarget', value: task.merge_target_path || 'in-place' },
        { label: 'Author', value: task.author_participant },
        { label: 'Reviewers', value: String(reviewerCount) },
        { label: 'Last Reason', value: task.last_gate_reason || 'n/a' },
      ];

      for (const item of boxes) {
        const row = document.createElement('div');
        row.className = 'scope-row';

        const key = document.createElement('span');
        key.className = 'scope-key';
        key.textContent = String(item.label || '');

        const value = document.createElement('span');
        value.className = 'scope-value';
        if (item.html) {
          value.innerHTML = item.html;
        } else {
          value.textContent = String(item.value ?? '');
        }

        row.appendChild(key);
        row.appendChild(value);
        el.taskSnapshot.appendChild(row);
      }

      const status = String(task.status || '').trim().toLowerCase();
      const waitingManual = !historyOnly && status === 'waiting_manual';
      const canStart = !historyOnly && status === 'queued';
      const canOperate = !historyOnly && isActiveStatus(status);
      const terminalStatuses = new Set(['passed', 'failed_gate', 'failed_system', 'canceled']);
      const canPromote = !historyOnly
        && terminalStatuses.has(status)
        && Number(task.auto_merge === false ? 0 : 1) === 0
        && Number(task.max_rounds || 1) > 1;
      if (el.startBtn) {
        el.startBtn.disabled = !canStart;
        if (historyOnly) {
          el.startBtn.title = 'History task is read-only. Start is unavailable.';
        } else {
          el.startBtn.title = canStart ? '' : `Start is available only when status=queued (current=${task.status}).`;
        }
      }
      if (el.cancelBtn) el.cancelBtn.disabled = !canOperate;
      if (el.forceFailBtn) el.forceFailBtn.disabled = !canOperate;
      if (el.promoteRoundBtn) {
        el.promoteRoundBtn.disabled = !canPromote;
        el.promoteRoundBtn.title = canPromote
          ? ''
          : 'Promote is available only for terminal tasks with max_rounds>1 and auto_merge=0.';
      }
      if (el.promoteRound) {
        el.promoteRound.disabled = !canPromote;
        if (canPromote && (!String(el.promoteRound.value || '').trim() || Number(el.promoteRound.value || 0) < 1)) {
          const fallbackRound = Math.max(1, Number(task.rounds_completed || task.max_rounds || 1));
          el.promoteRound.value = String(fallbackRound);
        }
      }
      if (el.approveQueueBtn) el.approveQueueBtn.disabled = !waitingManual;
      if (el.approveStartBtn) el.approveStartBtn.disabled = !waitingManual;
      if (el.rejectBtn) el.rejectBtn.disabled = !waitingManual;
      if (el.customReplyBtn) el.customReplyBtn.disabled = !waitingManual;
      if (el.manualReplyNote) {
        el.manualReplyNote.disabled = !waitingManual;
        el.manualReplyNote.placeholder = waitingManual
          ? 'Type special constraints, business intent, scope changes, or acceptance criteria...'
          : 'Manual reply is available when task status is waiting_manual.';
      }
    }

    function historyItemsInSelectedProject() {
      const key = normalizeProjectPath(state.selectedProject || '');
      const all = Array.isArray(state.historyItems) ? state.historyItems : [];
      if (!key) return all;
      return all.filter((item) => normalizeProjectPath(item.project_path || '') === key);
    }

    function formatHistoryTime(value) {
      const text = String(value || '').trim();
      if (!text) return 'n/a';
      const dt = parseEventDate(text);
      if (Number.isNaN(dt.getTime())) return text;
      return dt.toLocaleString();
    }

    function parseEventDate(raw) {
      const text = String(raw || '').trim();
      if (!text) return new Date(NaN);
      const hasOffset = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text);
      const normalized = hasOffset ? text : `${text}Z`;
      return new Date(normalized);
    }

    function formatRevisionSummary(revisions) {
      const rev = revisions && typeof revisions === 'object' ? revisions : {};
      if (!rev.auto_merge) return 'auto-merge: off or not reached';
      const changed = Number(rev.changed_files || 0);
      const copied = Number(rev.copied_files || 0);
      const deleted = Number(rev.deleted_files || 0);
      const mode = String(rev.mode || 'n/a');
      return `mode=${mode} | changed=${changed} copied=${copied} deleted=${deleted}`;
    }

    function renderHistoryCollapseState() {
      const collapsed = !!state.historyCollapsed;
      if (el.projectHistoryBody) {
        el.projectHistoryBody.classList.toggle('is-collapsed', collapsed);
      }
      if (el.toggleHistoryBtn) {
        el.toggleHistoryBtn.textContent = collapsed ? 'Expand' : 'Collapse';
        el.toggleHistoryBtn.title = collapsed ? 'Expand project history' : 'Collapse project history';
        el.toggleHistoryBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      }
    }

    function setHistoryCollapsed(collapsed, opts = {}) {
      state.historyCollapsed = !!collapsed;
      renderHistoryCollapseState();
      if (opts.persist === false) return;
      try {
        localStorage.setItem('awe-agentcheck-history-collapsed', state.historyCollapsed ? '1' : '0');
      } catch {
      }
    }

    function renderCreateHelp() {
      const lang = state.createHelpLanguage === 'en' ? 'en' : 'zh';
      const collapsed = !!state.createHelpCollapsed;

      if (el.createHelpPanel) {
        el.createHelpPanel.classList.toggle('is-collapsed', collapsed);
      }
      if (el.openCreateHelpBtn) {
        el.openCreateHelpBtn.textContent = collapsed ? 'Help' : 'Hide Help';
        el.openCreateHelpBtn.title = collapsed ? 'Open Create Task help' : 'Hide Create Task help';
      }
      if (el.createHelpLangEnBtn) {
        el.createHelpLangEnBtn.classList.toggle('active', lang === 'en');
      }
      if (el.createHelpLangZhBtn) {
        el.createHelpLangZhBtn.classList.toggle('active', lang === 'zh');
      }
      if (el.createHelpHint) {
        el.createHelpHint.textContent = lang === 'en'
          ? 'Create Task field guide (EN)'
          : 'Create Task 字段说明（中文）';
      }
      if (!el.createHelpList) return;

      el.createHelpList.innerHTML = '';
      if (collapsed) return;

      for (const item of CREATE_TASK_HELP_ITEMS) {
        const card = document.createElement('article');
        card.className = 'create-help-item';

        const head = document.createElement('div');
        head.className = 'create-help-item-title';
        head.textContent = String(item.field || '');

        const desc = document.createElement('div');
        desc.className = 'create-help-item-desc';
        desc.textContent = lang === 'en' ? String(item.en || '') : String(item.zh || '');

        card.appendChild(head);
        card.appendChild(desc);
        el.createHelpList.appendChild(card);
      }
    }

    function setCreateHelpCollapsed(collapsed, opts = {}) {
      state.createHelpCollapsed = !!collapsed;
      renderCreateHelp();
      if (opts.persist === false) return;
      try {
        localStorage.setItem('awe-agentcheck-create-help-collapsed', state.createHelpCollapsed ? '1' : '0');
      } catch {
      }
    }

    function setCreateHelpLanguage(language, opts = {}) {
      const normalized = String(language || '').trim().toLowerCase() === 'en' ? 'en' : 'zh';
      state.createHelpLanguage = normalized;
      renderCreateHelp();
      if (opts.persist === false) return;
      try {
        localStorage.setItem('awe-agentcheck-create-help-lang', normalized);
      } catch {
      }
    }

    function renderProjectHistory() {
      const scoped = historyItemsInSelectedProject();
      const selected = normalizeProjectPath(state.selectedProject || '');
      const projectLabel = selected || 'all projects';
      if (el.historySummary) {
        el.historySummary.textContent = `${projectLabel} | history records=${scoped.length}`;
      }
      if (!el.projectHistory) return;
      el.projectHistory.innerHTML = '';
      if (state.historyCollapsed) return;
      if (!scoped.length) {
        el.projectHistory.innerHTML = '<div class="empty">No history records for current scope yet.</div>';
        return;
      }

      for (const item of scoped) {
        const card = document.createElement('article');
        card.className = 'history-item';
        const findings = Array.isArray(item.core_findings) ? item.core_findings : [];
        const disputes = Array.isArray(item.disputes) ? item.disputes : [];
        const nextSteps = Array.isArray(item.next_steps) ? item.next_steps : [];
        const findingsText = findings.length ? findings.map((v) => `- ${v}`).join('\n') : '- n/a';
        const disputesText = disputes.length
          ? disputes.map((d) => `- ${d.participant || 'reviewer'} [${d.verdict || 'unknown'}]: ${d.note || ''}`).join('\n')
          : '- none';
        const nextText = nextSteps.length ? nextSteps.map((v) => `- ${v}`).join('\n') : '- n/a';

        card.innerHTML = `
          <div class="history-head">
            <span>${escapeHtml(String(item.task_id || 'task'))} | ${statusPill(String(item.status || 'unknown'))}</span>
            <span>${escapeHtml(formatHistoryTime(item.updated_at || item.created_at))}</span>
          </div>
          <div class="history-meta"><strong>Core Findings</strong>\n${escapeHtml(findingsText)}</div>
          <div class="history-meta"><strong>Revisions</strong>\n${escapeHtml(formatRevisionSummary(item.revisions))}</div>
          <div class="history-meta"><strong>Disputes</strong>\n${escapeHtml(disputesText)}</div>
          <div class="history-meta"><strong>Next Steps</strong>\n${escapeHtml(nextText)}</div>
        `;

        if (state.tasks.some((task) => task.task_id === item.task_id)) {
          const jump = document.createElement('button');
          jump.className = 'history-link';
          jump.textContent = 'Open task in Dialogue';
          jump.addEventListener('click', async () => {
            state.selectedTaskId = item.task_id;
            persistSelectionPreference();
            renderTaskSelect();
            renderTaskSnapshot();
            await refreshConversation({ force: true });
          });
          card.appendChild(jump);
        }

        el.projectHistory.appendChild(card);
      }
    }

    function currentWorkspacePathForPolicy() {
      const task = selectedTask();
      if (task && task.workspace_path) {
        return String(task.workspace_path);
      }
      if (state.selectedProject) {
        return String(state.selectedProject);
      }
      if (el.workspacePath && String(el.workspacePath.value || '').trim()) {
        return String(el.workspacePath.value || '').trim();
      }
      return '.';
    }

    function renderPolicyTemplates() {
      const payload = state.policyTemplates;
      if (!el.policyTemplate || !el.policyProfileHint) return;
      if (!payload || !Array.isArray(payload.templates)) {
        el.policyTemplate.innerHTML = '<option value="">n/a</option>';
        el.policyProfileHint.value = 'Policy templates unavailable';
        return;
      }

      const current = String(el.policyTemplate.value || '').trim();
      el.policyTemplate.innerHTML = '';
      for (const item of payload.templates) {
        const option = document.createElement('option');
        option.value = String(item.id || '');
        option.textContent = `${item.id} | ${item.label}`;
        el.policyTemplate.appendChild(option);
      }
      const customOption = document.createElement('option');
      customOption.value = 'custom';
      customOption.textContent = 'custom | Keep manual settings';
      el.policyTemplate.appendChild(customOption);

      const validCurrent = current === 'custom' || payload.templates.some((item) => String(item.id || '') === current);
      if (validCurrent) {
        el.policyTemplate.value = current;
      } else {
        el.policyTemplate.value = String(payload.recommended_template || '');
      }

      const profile = payload.profile || {};
      const exists = profile.exists ? 'yes' : 'no';
      const size = String(profile.repo_size || 'unknown');
      const risk = String(profile.risk_level || 'unknown');
      const files = Number(profile.file_count || 0);
      const markers = Number(profile.risk_markers || 0);
      el.policyProfileHint.value = `exists=${exists} | size=${size} | risk=${risk} | files=${files} | riskMarkers=${markers}`;
    }

    function applySelectedPolicyTemplate() {
      const payload = state.policyTemplates;
      if (!payload || !Array.isArray(payload.templates) || !el.policyTemplate) return;
      const selectedId = String(el.policyTemplate.value || '').trim();
      if (selectedId === 'custom') {
        if (el.createStatus) {
          el.createStatus.textContent = 'Custom policy selected: keeping current manual values.';
        }
        return;
      }
      const selected = payload.templates.find((item) => String(item.id || '').trim() === selectedId);
      if (!selected || !selected.defaults) return;
      const defaults = selected.defaults;
      const mapNum = (value, fallback) => String(Number.isFinite(Number(value)) ? Number(value) : fallback);

      if (el.sandboxMode) el.sandboxMode.value = mapNum(defaults.sandbox_mode, 1);
      if (el.selfLoopMode) el.selfLoopMode.value = mapNum(defaults.self_loop_mode, 0);
      if (el.autoMerge) el.autoMerge.value = mapNum(defaults.auto_merge, 1);
      if (el.maxRounds) el.maxRounds.value = String(Math.max(1, Number(defaults.max_rounds || 1)));
      if (el.debateMode) el.debateMode.value = mapNum(defaults.debate_mode, 1);
      if (el.plainMode) el.plainMode.value = mapNum(defaults.plain_mode, 1);
      if (el.streamMode) el.streamMode.value = mapNum(defaults.stream_mode, 1);
      if (el.repairMode && defaults.repair_mode) el.repairMode.value = String(defaults.repair_mode);
      syncCreateTaskPolicyControls('policyTemplate');
      if (el.createStatus) {
        el.createStatus.textContent = `Applied policy template: ${selectedId}`;
      }
    }

    async function refreshPolicyTemplates() {
      const workspacePath = currentWorkspacePathForPolicy();
      try {
        const payload = await api(
          '/api/policy-templates?workspace_path=' + encodeURIComponent(workspacePath),
          { healthImpact: false },
        );
        state.policyTemplates = payload || null;
      } catch {
        state.policyTemplates = null;
      }
      renderPolicyTemplates();
    }

    function renderAnalytics() {
      if (!el.analyticsSummary) return;
      const analytics = state.analytics;
      el.analyticsSummary.innerHTML = '';
      if (!analytics) {
        el.analyticsSummary.innerHTML = '<div class="empty">Analytics unavailable.</div>';
        return;
      }

      const taxonomy = Array.isArray(analytics.failure_taxonomy) ? analytics.failure_taxonomy : [];
      const trend = Array.isArray(analytics.failure_taxonomy_trend) ? analytics.failure_taxonomy_trend : [];
      const drift = Array.isArray(analytics.reviewer_drift) ? analytics.reviewer_drift : [];

      const topTaxonomy = taxonomy.slice(0, 6).map((item) =>
        `${item.bucket}: ${item.count} (${(Number(item.share || 0) * 100).toFixed(1)}%)`
      );
      const recentTrend = trend.slice(-5).map((item) => {
        const buckets = item.buckets || {};
        const top = Object.entries(buckets)
          .sort((a, b) => Number(b[1]) - Number(a[1]))
          .slice(0, 2)
          .map(([k, v]) => `${k}=${v}`)
          .join(', ');
        return `${item.day}: total=${item.total}${top ? ` | ${top}` : ''}`;
      });
      const topDrift = drift.slice(0, 8).map((item) =>
        `${item.participant}: drift=${item.drift_score}, adverse=${(Number(item.adverse_rate || 0) * 100).toFixed(1)}%, reviews=${item.reviews}`
      );

      const blocks = [
        {
          title: 'Failure Taxonomy',
          lines: topTaxonomy.length ? topTaxonomy : ['n/a'],
        },
        {
          title: 'Trend (Recent Days)',
          lines: recentTrend.length ? recentTrend : ['n/a'],
        },
        {
          title: 'Reviewer Drift',
          lines: topDrift.length ? topDrift : ['n/a'],
        },
      ];

      for (const block of blocks) {
        const card = document.createElement('article');
        card.className = 'history-item';
        card.innerHTML = `
          <div class="history-head"><span>${escapeHtml(block.title)}</span></div>
          <div class="history-meta">${escapeHtml(block.lines.join('\n'))}</div>
        `;
        el.analyticsSummary.appendChild(card);
      }
    }

    async function refreshAnalytics() {
      try {
        state.analytics = await api('/api/analytics?limit=300', { healthImpact: false });
      } catch {
        state.analytics = null;
      }
      renderAnalytics();
    }

    function renderGithubSummary() {
      if (!el.githubSummaryText || !el.githubSummaryMeta) return;
      const task = selectedTask();
      if (!task) {
        el.githubSummaryMeta.textContent = 'Select a task to generate PR-ready summary.';
        el.githubSummaryText.value = '';
        return;
      }
      const payload = state.githubSummaryByTask.get(task.task_id);
      if (!payload) {
        el.githubSummaryMeta.textContent = `Task ${task.task_id} | summary not loaded yet`;
        el.githubSummaryText.value = '';
        return;
      }
      const git = payload.git || {};
      const branch = git.branch || 'n/a';
      const guard = String(git.guard_reason || 'n/a');
      el.githubSummaryMeta.textContent = `Task ${task.task_id} | status=${payload.status || 'unknown'} | branch=${branch} | guard=${guard}`;
      el.githubSummaryText.value = String(payload.summary_markdown || '');
    }

    async function refreshGithubSummary({ force = false } = {}) {
      const task = selectedTask();
      if (!task || task._history_only) {
        renderGithubSummary();
        return;
      }
      if (!force && state.githubSummaryByTask.has(task.task_id)) {
        renderGithubSummary();
        return;
      }
      try {
        const payload = await api(`/api/tasks/${task.task_id}/github-summary`, { healthImpact: false });
        state.githubSummaryByTask.set(task.task_id, payload);
      } catch {
        state.githubSummaryByTask.delete(task.task_id);
      }
      renderGithubSummary();
    }

    function eventActor(event, task) {
      const payload = event.payload || {};
      const type = String(event.type || '');
      if (
        type === 'review'
        || type === 'proposal_review'
        || type === 'proposal_precheck_review'
        || type === 'debate_review'
        || type === 'debate_reply'
        || type === 'participant_stream'
      ) {
        return String(payload.participant || 'reviewer');
      }
      if (
        type === 'review_started'
        || type === 'proposal_precheck_review_started'
        || type === 'debate_review_started'
        || type === 'debate_reply_started'
        || type === 'discussion_started'
        || type === 'implementation_started'
      ) {
        return String(payload.participant || payload.provider || 'system');
      }
      if (type === 'discussion' || type === 'implementation') {
        if (task && task.author_participant) return task.author_participant;
        if (payload.provider) return String(payload.provider);
        return 'author';
      }
      return 'system';
    }

    function stripControlLines(text) {
      const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
      const out = [];
      for (const raw of lines) {
        const line = String(raw || '');
        if (/^\s*(VERDICT|NEXT_ACTION)\s*:/i.test(line)) continue;
        out.push(line);
      }
      return out.join('\n').trim();
    }

    function stripInternalNoise(text) {
      const ansiStripped = String(text || '').replace(/\x1b\[[0-9;]*m/g, '');
      const lines = ansiStripped.replace(/\r\n/g, '\n').split('\n');
      const out = [];
      const dropPatterns = [
        /^\s*OpenAI Codex v/i,
        /^\s*Reading prompt from stdin/i,
        /^\s*[-]{4,}\s*$/,
        /^\s*workdir\s*:/i,
        /^\s*model\s*:/i,
        /^\s*provider\s*:/i,
        /^\s*approval\s*:/i,
        /^\s*sandbox\s*:/i,
        /^\s*reasoning effort\s*:/i,
        /^\s*reasoning summaries\s*:/i,
        /^\s*session id\s*:/i,
        /^\s*tokens used\s*$/i,
        /^\s*\d[\d,]*\s*$/,
        /^\s*(user|codex|thinking)\s*$/i,
        /^\s*exec\s*$/i,
        /^\s*\*\*.*\*\*\s*$/,
        /^\s*mcp\s*:/i,
        /^\s*mcp startup\s*:/i,
        /^\s*\".*\" in .*(succeeded|failed) in \d+ms.*$/i,
        /^\s*\".*pwsh\.exe.*-Command.*\"$/i,
        /using-superpowers/i,
        /writing-plans/i,
        /skills?\s*:/i,
      ];
      for (const raw of lines) {
        const line = String(raw || '');
        if (dropPatterns.some((pattern) => pattern.test(line))) {
          continue;
        }
        out.push(line);
      }
      return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
    }

    function isKeyDialogueEvent(type) {
      const t = String(type || '');
      return (
        t === 'task_started'
        || t === 'round_started'
        || t === 'discussion_started'
        || t === 'implementation_started'
        || t === 'review_started'
        || t === 'proposal_precheck_review_started'
        || t === 'proposal_review_started'
        || t === 'verification_started'
        || t === 'gate_passed'
        || t === 'gate_failed'
        || t === 'author_confirmation_required'
        || t === 'author_decision'
        || t === 'canceled'
        || t === 'force_failed'
        || t === 'system_failure'
      );
    }

    function verdictLabel(verdict, lang) {
      const v = String(verdict || '').trim().toLowerCase();
      if (lang === 'zh') {
        if (v === 'no_blocker') return '通过（可继续）';
        if (v === 'blocker') return '不通过（需先修复）';
        return '不确定（信息不足）';
      }
      if (v === 'no_blocker') return 'Pass (can continue)';
      if (v === 'blocker') return 'Needs fixes (blocking)';
      return 'Unclear (insufficient info)';
    }

    function eventText(event, task) {
      const payload = event.payload || {};
      const plainMode = !!(task && task.plain_mode !== false);
      const lang = String((task && task.conversation_language) || 'en').toLowerCase();

      if (payload.output) {
        const raw = String(payload.output || '');
        if (plainMode) {
          const cleaned = stripControlLines(stripInternalNoise(raw));
          if (event.type === 'proposal_review' || event.type === 'proposal_precheck_review' || event.type === 'review') {
            const verdict = verdictLabel(payload.verdict, lang);
            if (lang === 'zh') {
              return `判定: ${verdict}\n说明: ${cleaned || '无'}`;
            }
            return `Verdict: ${verdict}\nReason: ${cleaned || 'n/a'}`;
          }
          return cleaned || raw || (lang === 'zh' ? '暂无可读内容' : 'No readable content yet');
        }
        return raw;
      }
      if (payload.chunk) {
        const streamName = String(payload.stream || 'stdout').toLowerCase();
        if (!state.showStreamDetails) return '';
        const chunk = String(payload.chunk);
        const cleaned = stripInternalNoise(chunk);
        if (!cleaned) return '';
        return streamName === 'stderr' ? `[stderr] ${cleaned}` : cleaned;
      }
      if (event.type === 'debate_started') {
        const count = Number(payload.reviewer_count || 0);
        if (plainMode && lang === 'zh') return `进入评审优先讨论，评审人数: ${count}`;
        return `debate_started: reviewers=${count}`;
      }
      if (event.type === 'debate_completed') {
        if (plainMode && lang === 'zh') return '评审优先讨论阶段完成';
        return 'debate_completed';
      }
      if (event.type === 'debate_review_started' || event.type === 'debate_reply_started') {
        const participant = String(payload.participant || 'agent');
        const timeout = Number(payload.timeout_seconds || 0);
        if (plainMode && lang === 'zh') return `正在处理评审意见: ${participant} (超时 ${timeout}s)`;
        return `${event.type}: participant=${participant} timeout=${timeout}s`;
      }
      if (event.type === 'proposal_precheck_review_started') {
        const participant = String(payload.participant || 'reviewer');
        const timeout = Number(payload.timeout_seconds || 0);
        if (plainMode && lang === 'zh') return `预检查评审启动: ${participant} (超时 ${timeout}s)`;
        return `proposal_precheck_review_started: participant=${participant} timeout=${timeout}s`;
      }
      if (event.type === 'discussion_started' || event.type === 'implementation_started') {
        const provider = String(payload.provider || 'unknown');
        const timeout = Number(payload.timeout_seconds || 0);
        if (plainMode && lang === 'zh') {
          const phase = event.type === 'discussion_started' ? '方案讨论' : '代码实现';
          return `${phase}启动: ${provider} (超时 ${timeout}s)`;
        }
        return `${event.type}: provider=${provider} timeout=${timeout}s`;
      }
      if (event.type === 'review_started') {
        const participant = String(payload.participant || 'reviewer');
        const timeout = Number(payload.timeout_seconds || 0);
        if (plainMode && lang === 'zh') return `代码评审启动: ${participant} (超时 ${timeout}s)`;
        return `review_started: participant=${participant} timeout=${timeout}s`;
      }
      if (event.type === 'verification_started') {
        const timeout = Number(payload.timeout_seconds || 0);
        if (plainMode && lang === 'zh') return `测试与静态检查启动 (超时 ${timeout}s)`;
        return `verification_started: timeout=${timeout}s`;
      }
      if (event.type === 'author_confirmation_required' && payload.summary) {
        if (!plainMode) return String(payload.summary);
        const cleanedSummary = stripInternalNoise(String(payload.summary || ''));
        return cleanedSummary || String(payload.summary || '');
      }
      if (event.type === 'author_decision' && payload.decision) {
        if (plainMode && lang === 'zh') {
          const d = String(payload.decision || '');
          return d === 'approved' ? '作者已批准，继续执行。' : '作者已拒绝，任务停止。';
        }
        return `decision=${payload.decision}${payload.note ? ` | note=${payload.note}` : ''}`;
      }
      if (event.type === 'verification') {
        if (plainMode && lang === 'zh') {
          return `验证结果: 测试=${payload.tests_ok ? '通过' : '失败'}，Lint=${payload.lint_ok ? '通过' : '失败'}`;
        }
        return `tests_ok=${payload.tests_ok} lint_ok=${payload.lint_ok}`;
      }
      if (payload.reason) return String(payload.reason);
      if (payload.status) return `status=${payload.status}`;
      const keys = Object.keys(payload);
      if (!keys.length) return '';
      try {
        return JSON.stringify(payload, null, 2);
      } catch {
        return String(payload);
      }
    }

    function eventClass(event) {
      const t = String(event.type || '');
      if (t === 'discussion' || t === 'implementation') return 'agent';
      if (t === 'debate_reply') return 'agent';
      if (t === 'discussion_started' || t === 'implementation_started' || t === 'debate_reply_started') return 'agent';
      if (t === 'review' || t === 'proposal_review' || t === 'proposal_precheck_review') return 'review';
      if (t === 'debate_review') return 'review';
      if (t === 'review_started' || t === 'debate_review_started' || t === 'proposal_review_started' || t === 'proposal_precheck_review_started') return 'review';
      if (t === 'participant_stream') {
        const stage = String((event.payload || {}).stage || '');
        if (stage.includes('review')) return 'review';
        return 'agent';
      }
      if (t.includes('failed') || t === 'system_failure' || t === 'force_failed') return 'system bad';
      return 'system';
    }

    function dialogueSignature(task, events, role) {
      const taskId = task ? String(task.task_id || '') : 'none';
      const list = Array.isArray(events) ? events : [];
      const last = list.length ? list[list.length - 1] : null;
      const lastSeq = last ? String(last.seq ?? '') : '';
      const lastType = last ? String(last.type || '') : '';
      const lastCreatedAt = last ? String(last.created_at || '') : '';
      const streamMode = state.showStreamDetails ? 'stream-on' : 'stream-off';
      return `${taskId}|${String(role || 'all')}|${streamMode}|${list.length}|${lastSeq}|${lastType}|${lastCreatedAt}`;
    }

    function renderDialogue(events) {
      const task = selectedTask();
      const stream = Array.isArray(events) ? events : [];
      const filtered = stream.filter((event) => {
        if (String(event.type || '') === 'participant_stream' && !state.showStreamDetails) return false;
        if (state.selectedRole === 'all') return true;
        return eventActor(event, task) === state.selectedRole;
      });
      const displayItems = [];
      for (const event of filtered) {
        const text = String(eventText(event, task) || '').trim();
        if (!text && !isKeyDialogueEvent(event.type)) {
          continue;
        }
        displayItems.push({ event, text });
      }
      const displayEvents = displayItems.map((item) => item.event);
      const signature = dialogueSignature(task, displayEvents, state.selectedRole);
      if (state.lastDialogueSignature === signature) {
        return;
      }
      state.lastDialogueSignature = signature;

      el.dialogue.innerHTML = '';
      if (!task) {
        el.dialogue.innerHTML = '<div class="empty">Select a project/task to view dialogue.</div>';
        return;
      }
      if (!displayItems.length) {
        el.dialogue.innerHTML = '<div class="empty">No dialogue for this role scope yet.</div>';
        return;
      }

      for (const itemDef of displayItems) {
        const event = itemDef.event;
        const item = document.createElement('article');
        item.className = `message ${eventClass(event)}`;

        const actorName = eventActor(event, task);
        const actorProvider = eventActorProvider(event, actorName);

        const shell = document.createElement('div');
        shell.className = 'msg-shell';

        const avatar = document.createElement('div');
        avatar.innerHTML = avatarHtml(actorName, actorProvider, 'msg-avatar');

        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble';

        const head = document.createElement('div');
        head.className = 'msg-head';

        const actor = document.createElement('div');
        actor.className = 'msg-actor';
        actor.textContent = actorName;

        const meta = document.createElement('div');
        meta.className = 'msg-kind';
        const roundText = event.round ? `round ${event.round}` : 'task';
        const timeObj = event.created_at ? parseEventDate(event.created_at) : null;
        const timeStr = (timeObj && !Number.isNaN(timeObj.getTime()))
          ? timeObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
          : '';
        meta.textContent = `${event.type} | ${roundText}${timeStr ? ` | ${timeStr}` : ''}`;

        const body = document.createElement('pre');
        body.className = 'msg-text';
        body.textContent = itemDef.text || '...';

        head.appendChild(actor);
        head.appendChild(meta);
        bubble.appendChild(head);
        bubble.appendChild(body);

        shell.appendChild(avatar.firstElementChild || avatar);
        shell.appendChild(bubble);
        item.appendChild(shell);
        el.dialogue.appendChild(item);
      }
      requestAnimationFrame(() => { el.dialogue.scrollTop = el.dialogue.scrollHeight; });
    }

    function renderKpiStrip(stats) {
      if (!el.kpiStrip) return;
      if (!stats) {
        el.kpiStrip.innerHTML = '';
        return;
      }

      const kpis = [
        { label: 'Total Tasks', value: String(Number(stats.total_tasks || 0)) },
        { label: 'Active', value: String(Number(stats.active_tasks || 0)) },
        { label: 'Pass 50', value: `${(Number(stats.pass_rate_50 || 0) * 100).toFixed(1)}%` },
        { label: 'Gate Fail 50', value: `${(Number(stats.failed_gate_rate_50 || 0) * 100).toFixed(1)}%` },
        { label: 'System Fail 50', value: `${(Number(stats.failed_system_rate_50 || 0) * 100).toFixed(1)}%` },
        { label: 'Avg Sec 50', value: Number(stats.mean_task_duration_seconds_50 || 0).toFixed(1) },
      ];

      el.kpiStrip.innerHTML = '';
      for (const item of kpis) {
        const card = document.createElement('article');
        card.className = 'kpi-card';
        card.innerHTML = `
          <div class="kpi-label">${escapeHtml(item.label)}</div>
          <div class="kpi-value">${escapeHtml(item.value)}</div>
        `;
        el.kpiStrip.appendChild(card);
      }
    }

    function renderStats() {
      const stats = state.stats;
      if (!stats) {
        el.statsLine.textContent = 'Stats unavailable.';
        renderKpiStrip(null);
        return;
      }
      el.statsLine.textContent =
        `Total=${stats.total_tasks} | Active=${stats.active_tasks} | ` +
        `Status=${JSON.stringify(stats.status_counts)} | ` +
        `Reasons=${JSON.stringify(stats.reason_bucket_counts)} | ` +
        `Providers=${JSON.stringify(stats.provider_error_counts)} | ` +
        `Pass50=${(Number(stats.pass_rate_50 || 0) * 100).toFixed(1)}% | ` +
        `FG50=${(Number(stats.failed_gate_rate_50 || 0) * 100).toFixed(1)}% | ` +
        `FS50=${(Number(stats.failed_system_rate_50 || 0) * 100).toFixed(1)}% | ` +
        `AvgSec50=${Number(stats.mean_task_duration_seconds_50 || 0).toFixed(1)}`;
      renderKpiStrip(stats);
    }

    async function loadEvents(taskId, { force = false } = {}) {
      if (!taskId) return [];
      if (!force && state.eventsByTask.has(taskId)) {
        return state.eventsByTask.get(taskId) || [];
      }
      const events = await api(`/api/tasks/${taskId}/events`, { healthImpact: false });
      state.eventsByTask.set(taskId, events);
      return events;
    }

    async function refreshConversation({ force = false } = {}) {
      const task = selectedTask();
      if (!task) {
        renderDialogue([]);
        return;
      }
      try {
        const liveTask = isActiveStatus(task.status);
        const events = await loadEvents(task.task_id, { force: force || liveTask });
        renderDialogue(events);
      } catch (err) {
        renderDialogue([]);
        el.actionStatus.textContent = `Conversation load failed: ${String(err)}`;
      }
    }

    async function loadData({ forceEvents = false } = {}) {
      const shouldFetchHistory = forceEvents || !state.historyLoadedOnce;
      const historyRequest = !shouldFetchHistory
        ? Promise.resolve({ items: state.historyItems, _cached: true })
        : api('/api/project-history?limit=120', { healthImpact: false, retryAttempts: 1 });
      const [tasksResult, statsResult, modelsResult, historyResult] = await Promise.allSettled([
        api('/api/tasks?limit=200'),
        api('/api/stats'),
        api('/api/provider-models'),
        historyRequest,
      ]);
      if (shouldFetchHistory) {
        state.historyLoadedOnce = true;
      }

      let updated = false;
      if (tasksResult.status === 'fulfilled') {
        state.tasks = Array.isArray(tasksResult.value) ? tasksResult.value : [];
        updated = true;
      }
      if (statsResult.status === 'fulfilled') {
        state.stats = statsResult.value || null;
        updated = true;
      }
      if (modelsResult.status === 'fulfilled') {
        const providers = (modelsResult.value && modelsResult.value.providers) || {};
        state.providerModelCatalog = normalizeProviderModelCatalog(providers);
        updated = true;
      }
      if (historyResult.status === 'fulfilled') {
        const items = historyResult.value && Array.isArray(historyResult.value.items)
          ? historyResult.value.items
          : [];
        state.historyItems = items;
        updated = true;
      }
      if (!updated) {
        const reason = tasksResult.status === 'rejected'
          ? String(tasksResult.reason || '')
          : (statsResult.status === 'rejected'
            ? String(statsResult.reason || '')
            : (modelsResult.status === 'rejected'
              ? String(modelsResult.reason || '')
              : String(historyResult.reason || '')));
        throw new Error(`all data requests failed: ${reason}`);
      }

      ensureSelections();
      renderProviderModelOptions();
      renderStats();
      renderProjectSelector();
      renderRoles();
      renderTaskSelect();
      renderTaskSnapshot();
      renderProjectHistory();
      try {
        const tree = await loadProjectTree(state.selectedProject, { force: forceEvents });
        renderProjectTree(tree);
      } catch (err) {
        renderProjectTree(null);
        el.actionStatus.textContent = `Project tree load failed: ${String(err)}`;
      }
      await refreshConversation({ force: forceEvents });
      await refreshGithubSummary({ force: forceEvents });
      await refreshPolicyTemplates();
      await refreshAnalytics();
    }

    async function startSelectedTask() {
      const task = selectedTask();
      if (!task) return;
      try {
        const beforeStatus = String(task.status || '');
        const updated = await api(`/api/tasks/${task.task_id}/start`, {
          method: 'POST',
          body: JSON.stringify({ background: true }),
        });
        state.eventsByTask.delete(task.task_id);
        state.githubSummaryByTask.delete(task.task_id);
        const afterStatus = String((updated && updated.status) || beforeStatus || 'unknown');
        if (beforeStatus === 'running' && afterStatus === 'running') {
          el.actionStatus.textContent = `${task.task_id} already running; wait for next event or keep Background Refresh ON.`;
        } else if (beforeStatus === 'waiting_manual') {
          el.actionStatus.textContent = `${task.task_id} is waiting manual decision; use Approve / Reject / Custom Reply.`;
        } else if (afterStatus === 'queued' && String((updated && updated.last_gate_reason) || '') === 'concurrency_limit') {
          el.actionStatus.textContent = `${task.task_id} deferred by concurrency limit; will retry when slot is available.`;
        } else {
          el.actionStatus.textContent = `Start requested for ${task.task_id} (status=${afterStatus})`;
        }
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Start failed: ${String(err)}`;
      }
    }

    async function cancelSelectedTask() {
      const task = selectedTask();
      if (!task) return;
      try {
        await api(`/api/tasks/${task.task_id}/cancel`, { method: 'POST' });
        state.eventsByTask.delete(task.task_id);
        state.githubSummaryByTask.delete(task.task_id);
        el.actionStatus.textContent = `Cancel requested for ${task.task_id}`;
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Cancel failed: ${String(err)}`;
      }
    }

    async function forceFailSelectedTask() {
      const task = selectedTask();
      if (!task) return;
      const reason = String(el.forceReason.value || '').trim() || 'watchdog_timeout: operator forced fail';
      try {
        await api(`/api/tasks/${task.task_id}/force-fail`, {
          method: 'POST',
          body: JSON.stringify({ reason }),
        });
        state.eventsByTask.delete(task.task_id);
        state.githubSummaryByTask.delete(task.task_id);
        el.actionStatus.textContent = `Force-failed ${task.task_id}`;
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Force-fail failed: ${String(err)}`;
      }
    }

    async function promoteSelectedRound() {
      const task = selectedTask();
      if (!task) return;
      const roundNumber = Math.max(1, Number((el.promoteRound && el.promoteRound.value) || 1));
      const mergeTargetRaw = String((el.mergeTargetPath && el.mergeTargetPath.value) || '').trim();
      try {
        const result = await api(`/api/tasks/${task.task_id}/promote-round`, {
          method: 'POST',
          body: JSON.stringify({
            round: roundNumber,
            merge_target_path: (mergeTargetRaw || null),
          }),
        });
        state.eventsByTask.delete(task.task_id);
        state.githubSummaryByTask.delete(task.task_id);
        const changedCount = Array.isArray(result.changed_files) ? result.changed_files.length : 0;
        el.actionStatus.textContent = `Promoted ${task.task_id} round ${roundNumber} (changed=${changedCount})`;
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Promote failed: ${String(err)}`;
      }
    }

    async function submitAuthorDecision(decision, autoStart = false) {
      const task = selectedTask();
      if (!task) return;
      const normalizedDecision = String(decision || '').trim().toLowerCase();
      const noteFromInput = String((el.manualReplyNote && el.manualReplyNote.value) || '').trim();
      if (normalizedDecision === 'revise' && !noteFromInput) {
        el.actionStatus.textContent = 'Custom Reply requires a non-empty manual note.';
        return;
      }
      const fallbackNote = normalizedDecision === 'approve'
        ? 'approved from operator console'
        : (normalizedDecision === 'reject' ? 'rejected from operator console' : 'custom reply from operator console');
      const note = noteFromInput || fallbackNote;
      try {
        await api(`/api/tasks/${task.task_id}/author-decision`, {
          method: 'POST',
          body: JSON.stringify({
            decision: normalizedDecision,
            approve: normalizedDecision === 'approve',
            note,
            auto_start: !!autoStart,
          }),
        });
        state.eventsByTask.delete(task.task_id);
        state.githubSummaryByTask.delete(task.task_id);
        if (normalizedDecision === 'approve') {
          el.actionStatus.textContent = `Approved ${task.task_id}${autoStart ? ' and requested start' : ''}`;
        } else if (normalizedDecision === 'reject') {
          el.actionStatus.textContent = `Rejected ${task.task_id}`;
        } else {
          el.actionStatus.textContent = `Submitted custom reply for ${task.task_id}${autoStart ? ' and requested re-run' : ''}`;
        }
        if (el.manualReplyNote) {
          el.manualReplyNote.value = '';
        }
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Author decision failed: ${String(err)}`;
      }
    }

    async function clearProjectHistory() {
      const normalizedProject = normalizeProjectPath(state.selectedProject || '');
      const scopedHistory = historyItemsInSelectedProject();
      const scopedLive = tasksInSelectedProject();
      if (!scopedHistory.length && !scopedLive.length) {
        el.actionStatus.textContent = 'No history records or live tasks to clear for current scope.';
        return;
      }

      const scopeLabel = normalizedProject || 'all projects';
      const hint = normalizedProject
        ? 'History records and task rows in this project will be removed.'
        : 'History records and task rows across all projects will be removed.';
      const ok = window.confirm(
        `Clear Project History?\nScope: ${scopeLabel}\nHistory records: ${scopedHistory.length}\nLive tasks: ${scopedLive.length}\n${hint}\nThis also clears old live tasks (including queued/running/waiting_manual) in scope.`
      );
      if (!ok) {
        return;
      }

      try {
        const result = await api('/api/project-history/clear', {
          method: 'POST',
          body: JSON.stringify({
            project_path: normalizedProject || null,
            include_non_terminal: true,
          }),
        });
        const deleted = Number(result.deleted_tasks || 0);
        const skipped = Number(result.skipped_non_terminal || 0);
        const artifacts = Number(result.deleted_artifacts || 0);
        el.actionStatus.textContent = `History/task cleanup completed: deleted=${deleted}, artifacts=${artifacts}, skipped=${skipped}.`;
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Clear history failed: ${String(err)}`;
      }
    }

    async function createTask(autoStart) {
      const evolveUntilRaw = String(document.getElementById('evolveUntil').value || '').trim();
      const sandboxPathRaw = String(document.getElementById('sandboxWorkspacePath').value || '').trim();
      const mergeTargetRaw = String(document.getElementById('mergeTargetPath').value || '').trim();
      const payload = {
        title: document.getElementById('title').value,
        description: document.getElementById('description').value,
        author_participant: readAuthorParticipantFromForm(),
        reviewer_participants: readReviewerParticipantsFromForm(),
        evolution_level: Number(document.getElementById('evolutionLevel').value || 0),
        evolve_until: (evolveUntilRaw || null),
        conversation_language: String(document.getElementById('conversationLanguage').value || 'en').trim().toLowerCase() || 'en',
        provider_models: readProviderModelsFromForm(),
        provider_model_params: readProviderModelParamsFromForm(),
        participant_models: readParticipantModelsFromForm(),
        participant_model_params: readParticipantModelParamsFromForm(),
        claude_team_agents: false,
        codex_multi_agents: false,
        claude_team_agents_overrides: readParticipantAgentOverridesFromForm('claudeAgentsMode'),
        codex_multi_agents_overrides: readParticipantAgentOverridesFromForm('codexMultiAgentsMode'),
        repair_mode: String(document.getElementById('repairMode').value || 'balanced').trim().toLowerCase() || 'balanced',
        plain_mode: Number(document.getElementById('plainMode').value || 1) === 1,
        stream_mode: Number(document.getElementById('streamMode').value || 1) === 1,
        debate_mode: Number(document.getElementById('debateMode').value || 1) === 1,
        sandbox_mode: Number(document.getElementById('sandboxMode').value || 1) === 1,
        sandbox_workspace_path: (sandboxPathRaw || null),
        self_loop_mode: Number(document.getElementById('selfLoopMode').value || 0),
        auto_merge: Number(document.getElementById('autoMerge').value || 1) === 1,
        merge_target_path: (mergeTargetRaw || null),
        workspace_path: document.getElementById('workspacePath').value,
        auto_start: !!autoStart,
        max_rounds: Math.max(1, Math.min(20, Number(document.getElementById('maxRounds').value || 1))),
      };

      try {
        const task = await api('/api/tasks', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        state.selectedProject = normalizeProjectPath(task.project_path || task.workspace_path);
        state.selectedTaskId = task.task_id;
        state.eventsByTask.delete(task.task_id);
        state.githubSummaryByTask.delete(task.task_id);
        state.treeByProject.delete(state.selectedProject);
        el.createStatus.textContent = `Created ${task.task_id}`;
        await loadData({ forceEvents: true });
      } catch (err) {
        el.createStatus.textContent = `Create failed: ${String(err)}`;
      }
    }

    function addReviewerRoleRow() {
      const rows = roleRowsFromState();
      rows.push({ role: 'reviewer', participantId: suggestReviewerParticipantId(rows) });
      state.participantRoleRows = syncRoleRowsToHiddenFields(rows);
      renderParticipantCapabilityMatrix();
    }

    function removeReviewerRoleRowByIndex(rowIndex) {
      if (!Number.isFinite(rowIndex) || rowIndex < 0) return;
      const rows = roleRowsFromState();
      if (rowIndex >= rows.length) return;
      if (String((rows[rowIndex] && rows[rowIndex].role) || '').trim().toLowerCase() !== 'reviewer') {
        return;
      }
      const next = [];
      for (let i = 0; i < rows.length; i += 1) {
        if (i === rowIndex) continue;
        next.push(rows[i]);
      }
      state.participantRoleRows = syncRoleRowsToHiddenFields(next);
      renderParticipantCapabilityMatrix();
    }

    function updateParticipantCapabilityDraftFromEvent(event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const field = String(target.getAttribute('data-field') || '').trim();
      if (!field) return;

      const rowIndex = Number(target.getAttribute('data-row-index'));
      if (field === 'participantId') {
        if (!Number.isFinite(rowIndex) || rowIndex < 0) return;
        const rows = roleRowsFromState();
        if (rowIndex >= rows.length) return;
        rows[rowIndex] = {
          ...rows[rowIndex],
          participantId: String(target.value || '').trim(),
        };
        state.participantRoleRows = syncRoleRowsToHiddenFields(rows);
        if (String((event && event.type) || '').toLowerCase() === 'change') {
          renderParticipantCapabilityMatrix();
        }
        return;
      }

      let participant = String(target.getAttribute('data-participant') || '').trim();
      if (!participant && Number.isFinite(rowIndex) && rowIndex >= 0) {
        const rows = roleRowsFromState();
        participant = String((rows[rowIndex] && rows[rowIndex].participantId) || '').trim();
      }
      if (!participant) return;

      const value = String(target.value || '').trim();
      const current = state.participantCapabilityDraft[participant] || {};
      state.participantCapabilityDraft[participant] = {
        model: String(current.model || '').trim(),
        customModel: String(current.customModel || '').trim(),
        params: String(current.params || '').trim(),
        claudeAgentsMode: String(current.claudeAgentsMode || '0').trim().toLowerCase() || '0',
        codexMultiAgentsMode: String(current.codexMultiAgentsMode || '0').trim().toLowerCase() || '0',
      };
      state.participantCapabilityDraft[participant][field] = value;
    }

    function syncCreateTaskPolicyControls(trigger = 'auto') {
      const sandboxMode = Number((el.sandboxMode && el.sandboxMode.value) || 1);
      const maxRoundsValue = Math.max(1, Math.min(20, Number((el.maxRounds && el.maxRounds.value) || 1)));
      const multiRoundMode = maxRoundsValue > 1;
      if (el.autoMerge) {
        if (sandboxMode === 0) {
          el.autoMerge.value = '0';
          el.autoMerge.disabled = true;
          el.autoMerge.title = 'Auto Merge is available only when Sandbox Mode = 1.';
        } else {
          el.autoMerge.disabled = false;
          if (multiRoundMode && (trigger === 'maxRounds' || trigger === 'sandboxMode')) {
            // Multi-round tasks default to auto-merge on; user may still toggle it off manually.
            el.autoMerge.value = '1';
          }
          el.autoMerge.title = multiRoundMode
            ? 'Max Rounds > 1 defaults Auto Merge to 1. You can still switch it to 0 manually.'
            : '';
        }
      }

      const autoMergeEnabled = Number((el.autoMerge && el.autoMerge.value) || 0) === 1;
      if (el.mergeTargetPath) {
        const disableMergeTarget = !autoMergeEnabled;
        el.mergeTargetPath.disabled = disableMergeTarget;
        if (disableMergeTarget) {
          el.mergeTargetPath.value = '';
          el.mergeTargetPath.placeholder = 'disabled when Auto Merge = 0';
        } else {
          el.mergeTargetPath.placeholder = 'leave blank to merge in-place';
        }
      }

      const hasEvolveUntil = String((el.evolveUntil && el.evolveUntil.value) || '').trim().length > 0;
      if (el.maxRounds) {
        el.maxRounds.disabled = hasEvolveUntil;
        el.maxRounds.title = hasEvolveUntil
          ? 'Ignored when Evolve Until is set (deadline takes priority).'
          : '';
      }
    }

    function setPolling(enabled) {
      state.polling = enabled;
      el.pollBtn.textContent = `Background Refresh: ${enabled ? 'ON' : 'OFF'}`;
      try {
        localStorage.setItem('awe-agentcheck-poll', enabled ? '1' : '0');
      } catch {
      }
      if (state.timer) {
        clearInterval(state.timer);
        state.timer = null;
      }
      state.pollTickInFlight = false;
      if (enabled) {
        state.timer = setInterval(async () => {
          if (state.pollTickInFlight) {
            return;
          }
          state.pollTickInFlight = true;
          try {
            await loadData();
          } catch {
          } finally {
            state.pollTickInFlight = false;
          }
        }, 3500);
      }
    }

    function setStreamDetail(enabled) {
      state.showStreamDetails = !!enabled;
      if (el.streamDetailBtn) {
        el.streamDetailBtn.textContent = `Stream Details: ${state.showStreamDetails ? 'ON' : 'OFF'}`;
      }
      try {
        localStorage.setItem('awe-agentcheck-stream-detail', state.showStreamDetails ? '1' : '0');
      } catch {
      }
    }

    function computeDefaultEvolveUntil() {
      const now = new Date();
      const target = new Date(now);
      target.setHours(6, 0, 0, 0);
      if (now >= target) {
        target.setDate(target.getDate() + 1);
      }
      const yyyy = target.getFullYear();
      const mm = String(target.getMonth() + 1).padStart(2, '0');
      const dd = String(target.getDate()).padStart(2, '0');
      const hh = String(target.getHours()).padStart(2, '0');
      const mi = String(target.getMinutes()).padStart(2, '0');
      return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
    }

    document.getElementById('refreshBtn').addEventListener('click', async () => {
      try {
        await loadData({ forceEvents: true });
      } catch (err) {
        el.actionStatus.textContent = `Refresh failed: ${String(err)}`;
      }
    });
    document.getElementById('reloadEventsBtn').addEventListener('click', async () => {
      await refreshConversation({ force: true });
      await refreshGithubSummary({ force: true });
    });
    document.getElementById('pollBtn').addEventListener('click', () => setPolling(!state.polling));
    if (el.streamDetailBtn) {
      el.streamDetailBtn.addEventListener('click', async () => {
        setStreamDetail(!state.showStreamDetails);
        await refreshConversation({ force: true });
      });
    }
    document.getElementById('startBtn').addEventListener('click', () => startSelectedTask());
    document.getElementById('approveQueueBtn').addEventListener('click', () => submitAuthorDecision('approve', false));
    document.getElementById('approveStartBtn').addEventListener('click', () => submitAuthorDecision('approve', true));
    document.getElementById('rejectBtn').addEventListener('click', () => submitAuthorDecision('reject', false));
    if (el.customReplyBtn) {
      el.customReplyBtn.addEventListener('click', () => submitAuthorDecision('revise', true));
    }
    document.getElementById('cancelBtn').addEventListener('click', () => cancelSelectedTask());
    document.getElementById('forceFailBtn').addEventListener('click', () => forceFailSelectedTask());
    if (el.promoteRoundBtn) {
      el.promoteRoundBtn.addEventListener('click', () => promoteSelectedRound());
    }
    document.getElementById('createBtn').addEventListener('click', () => createTask(false));
    document.getElementById('createAndStartBtn').addEventListener('click', () => createTask(true));
    if (el.expandTreeBtn) {
      el.expandTreeBtn.addEventListener('click', () => setTreeExpansion(true));
    }
    if (el.collapseTreeBtn) {
      el.collapseTreeBtn.addEventListener('click', () => setTreeExpansion(false));
    }
    if (el.toggleHistoryBtn) {
      el.toggleHistoryBtn.addEventListener('click', () => {
        const next = !state.historyCollapsed;
        setHistoryCollapsed(next);
        renderProjectHistory();
      });
    }
    if (el.clearHistoryBtn) {
      el.clearHistoryBtn.addEventListener('click', () => clearProjectHistory());
    }
    if (el.reloadGithubSummaryBtn) {
      el.reloadGithubSummaryBtn.addEventListener('click', async () => {
        await refreshGithubSummary({ force: true });
      });
    }
    if (el.applyPolicyTemplateBtn) {
      el.applyPolicyTemplateBtn.addEventListener('click', () => applySelectedPolicyTemplate());
    }
    if (el.policyTemplate) {
      el.policyTemplate.addEventListener('change', () => applySelectedPolicyTemplate());
    }
    if (el.workspacePath) {
      el.workspacePath.addEventListener('change', async () => {
        await refreshPolicyTemplates();
      });
    }
    for (const field of [
      el.claudeModel,
      el.codexModel,
      el.geminiModel,
      el.claudeModelCustom,
      el.codexModelCustom,
      el.geminiModelCustom,
      el.claudeModelParams,
      el.codexModelParams,
      el.geminiModelParams,
    ]) {
      if (!field) continue;
      field.addEventListener('input', () => renderParticipantCapabilityMatrix());
      field.addEventListener('change', () => renderParticipantCapabilityMatrix());
    }
    if (el.matrixAddReviewerBtn) {
      el.matrixAddReviewerBtn.addEventListener('click', () => addReviewerRoleRow());
    }
    if (el.participantCapabilityMatrix) {
      el.participantCapabilityMatrix.addEventListener('input', (event) => updateParticipantCapabilityDraftFromEvent(event));
      el.participantCapabilityMatrix.addEventListener('change', (event) => updateParticipantCapabilityDraftFromEvent(event));
      el.participantCapabilityMatrix.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const raw = String(target.getAttribute('data-remove-row') || '').trim();
        if (!raw) return;
        const rowIndex = Number(raw);
        if (!Number.isFinite(rowIndex)) return;
        removeReviewerRoleRowByIndex(rowIndex);
      });
    }
    if (el.openCreateHelpBtn) {
      el.openCreateHelpBtn.addEventListener('click', () => setCreateHelpCollapsed(!state.createHelpCollapsed));
    }
    if (el.closeCreateHelpBtn) {
      el.closeCreateHelpBtn.addEventListener('click', () => setCreateHelpCollapsed(true));
    }
    if (el.createHelpLangEnBtn) {
      el.createHelpLangEnBtn.addEventListener('click', () => setCreateHelpLanguage('en'));
    }
    if (el.createHelpLangZhBtn) {
      el.createHelpLangZhBtn.addEventListener('click', () => setCreateHelpLanguage('zh'));
    }

    if (el.evolveUntil && !String(el.evolveUntil.value || '').trim()) {
      el.evolveUntil.value = computeDefaultEvolveUntil();
    }
    state.participantRoleRows = roleRowsFromHiddenFields();
    renderParticipantCapabilityMatrix();
    if (el.sandboxMode) {
      el.sandboxMode.addEventListener('change', () => syncCreateTaskPolicyControls('sandboxMode'));
    }
    if (el.autoMerge) {
      el.autoMerge.addEventListener('change', () => syncCreateTaskPolicyControls('autoMerge'));
    }
    if (el.evolveUntil) {
      el.evolveUntil.addEventListener('input', () => syncCreateTaskPolicyControls('evolveUntil'));
    }
    if (el.maxRounds) {
      el.maxRounds.addEventListener('input', () => syncCreateTaskPolicyControls('maxRounds'));
      el.maxRounds.addEventListener('change', () => syncCreateTaskPolicyControls('maxRounds'));
    }
    syncCreateTaskPolicyControls('init');
    setHistoryCollapsed(readHistoryCollapsePreference(), { persist: false });
    setCreateHelpCollapsed(readCreateHelpCollapsedPreference(), { persist: false });
    setCreateHelpLanguage(readCreateHelpLanguagePreference(), { persist: false });

    initThemeSelector();
    setPolling(readPollPreference());
    setStreamDetail(readStreamDetailPreference());

    el.taskSelect.addEventListener('change', async () => {
      state.selectedTaskId = el.taskSelect.value || null;
      persistSelectionPreference();
      renderTaskSnapshot();
      renderProjectHistory();
      await refreshConversation({ force: true });
      await refreshGithubSummary({ force: true });
      await refreshPolicyTemplates();
    });

    el.projectSelect.addEventListener('change', async () => {
      const project = el.projectSelect.value || null;
      state.selectedProject = project;
      state.selectedTaskId = null;
      ensureSelections();
      renderProjectSelector();
      renderTaskSelect();
      renderTaskSnapshot();
      renderProjectHistory();
      try {
        const tree = await loadProjectTree(state.selectedProject, { force: true });
        renderProjectTree(tree);
      } catch (err) {
        renderProjectTree(null);
        el.actionStatus.textContent = `Project tree load failed: ${String(err)}`;
      }
      await refreshConversation({ force: true });
      await refreshGithubSummary({ force: true });
      await refreshPolicyTemplates();
    });

    el.roleList.addEventListener('click', async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const button = target.closest('button[data-role]');
      if (!button) return;
      state.selectedRole = button.getAttribute('data-role') || 'all';
      persistSelectionPreference();
      renderRoles();
      await refreshConversation();
    });

    loadData({ forceEvents: true }).catch((err) => {
      setApiHealth(false, String(err));
      el.statsLine.textContent = `Failed to load dashboard: ${String(err)}`;
    });
  
