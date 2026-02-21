
import { createApiClient } from './modules/api.js';
import {
  SELECTION_PREF_KEY,
  THEME_OPTIONS,
  applySavedSelection,
  applyTheme as applyThemeStore,
  createInitialState,
  normalizeProviderModelCatalog,
  pruneParticipantCapabilityDraft as pruneParticipantCapabilityDraftStore,
  readThemePreference,
  readCreateHelpCollapsedPreference,
  readCreateHelpLanguagePreference,
  readHistoryCollapsePreference,
  readPollPreference,
  readSelectionPreference,
  readStreamDetailPreference,
  persistSelectionPreference as persistSelectionPreferenceStore,
  setApiHealth as setApiHealthStore,
} from './modules/store.js';
import {
  escapeHtml,
  hashText,
  normalizeProjectPath,
  projectName,
  seededRandom,
  sleep,
} from './modules/utils.js';
import {
  initElements,
  renderModelSelect,
  renderParticipantCapabilityMatrixHtml,
} from './modules/ui.js';
import { CREATE_TASK_HELP_ITEMS } from './modules/create_task_help.js';
import { createAvatarRenderer } from './modules/avatar.js';
import {
  loadProjectTreeData,
  treeOpenStateForProject,
  buildProjectTreeHierarchy,
  renderProjectTreeBranch,
  setProjectTreeExpansion,
  renderProjectTreePanel,
} from './modules/tree.js';
import {
  historyItemsInProject,
  formatHistoryTimeValue,
  parseHistoryDate,
  formatRevisionSummaryValue,
  renderHistoryCollapseUi,
  renderProjectHistoryPanel,
  clearProjectHistoryForScope,
} from './modules/history.js';
import {
  renderDialoguePanel,
} from './modules/dialogue.js';
import {
  formatParticipantBoolOverrides,
  formatParticipantModelParams,
  formatParticipantModels,
  formatProviderModelParams,
  formatProviderModels,
  isActiveStatus,
  statusPill,
  taskSortPriority,
  taskSortStamp,
} from './modules/formatters.js';

    const state = createInitialState();
    const el = initElements(document);
    applySavedSelection(state, readSelectionPreference(SELECTION_PREF_KEY));


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
      applyThemeStore(state, themeId, {
        persist,
        themeSelectEl: el.themeSelect,
      });
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
      setApiHealthStore(state, ok, detail, options, {
        connBadgeEl: el.connBadge,
        actionStatusEl: el.actionStatus,
      });
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

    function parsePhaseTimeoutSecondsFromForm() {
      const raw = String((el.phaseTimeoutSeconds && el.phaseTimeoutSeconds.value) || '').trim();
      if (!raw) return {};
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new Error('Phase Timeouts must be valid JSON object.');
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Phase Timeouts must be a JSON object.');
      }
      const alias = {
        proposal: 'proposal',
        precheck: 'proposal',
        discussion: 'discussion',
        author: 'discussion',
        implementation: 'implementation',
        impl: 'implementation',
        review: 'review',
        verification: 'command',
        command: 'command',
        lint_test: 'command',
      };
      const out = {};
      for (const [rawKey, rawValue] of Object.entries(parsed)) {
        const key = String(rawKey || '').trim().toLowerCase();
        const mapped = alias[key];
        if (!mapped) {
          throw new Error(`Phase Timeouts has invalid key: ${rawKey}`);
        }
        const seconds = Number(rawValue);
        if (!Number.isFinite(seconds) || seconds <= 0) {
          throw new Error(`Phase Timeouts has invalid value for ${mapped}: ${rawValue}`);
        }
        out[mapped] = Math.max(10, Math.min(60000, Math.floor(seconds)));
      }
      return out;
    }

    function writePhaseTimeoutSecondsToForm(value) {
      if (!el.phaseTimeoutSeconds) return;
      const source = (value && typeof value === 'object' && !Array.isArray(value)) ? value : {};
      const keys = ['proposal', 'discussion', 'implementation', 'review', 'command'];
      const normalized = {};
      for (const key of keys) {
        const raw = Number(source[key]);
        if (!Number.isFinite(raw) || raw <= 0) continue;
        normalized[key] = Math.max(10, Math.min(60000, Math.floor(raw)));
      }
      el.phaseTimeoutSeconds.value = Object.keys(normalized).length ? JSON.stringify(normalized) : '';
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
      pruneParticipantCapabilityDraftStore(state, activeParticipants);
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
      host.innerHTML = renderParticipantCapabilityMatrixHtml({
        roleRows,
        draftMap: state.participantCapabilityDraft || {},
        parseProvider,
        providerDefaultsFromForm,
        participantModelOptions,
        escapeHtml,
      });
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

    const avatarRenderer = createAvatarRenderer({ state, seededRandom, hashText });
    const roleAvatarHtml = avatarRenderer.roleAvatarHtml;

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
        memory_mode: 'basic',
        phase_timeout_seconds: {},
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

    function taskChoicesInSelectedProject() {
      const combined = [...tasksInSelectedProject(), ...historyOnlyTasksInSelectedProject()];
      combined.sort((a, b) => {
        const sourceDiff = Number(!!a._history_only) - Number(!!b._history_only);
        if (sourceDiff !== 0) return sourceDiff;
        const prioDiff = taskSortPriority(b.status) - taskSortPriority(a.status);
        if (prioDiff !== 0) return prioDiff;
        const stampDiff = taskSortStamp(b, parseEventDate) - taskSortStamp(a, parseEventDate);
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
      return loadProjectTreeData({ projectPath, force, state, api });
    }

    function treeOpenStateFor(projectPath) {
      return treeOpenStateForProject({ projectPath, state, normalizeProjectPath });
    }

    function buildTreeHierarchy(tree) {
      return buildProjectTreeHierarchy({ tree, normalizeProjectPath, projectName, treeNodeLabel });
    }

    function renderTreeBranch(nodes, depth, dirState) {
      return renderProjectTreeBranch({ nodes, depth, dirState });
    }

    function setTreeExpansion(open) {
      return setProjectTreeExpansion({
        open,
        state,
        normalizeProjectPath,
        treeOpenStateForFn: treeOpenStateFor,
        projectTreeEl: el.projectTree,
      });
    }

    function renderProjectTree(tree) {
      return renderProjectTreePanel({
        tree,
        el,
        buildTreeHierarchyFn: buildTreeHierarchy,
        renderTreeBranchFn: renderTreeBranch,
        treeOpenStateForFn: treeOpenStateFor,
      });
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
      const phaseTimeoutText = (() => {
        const source = (task.phase_timeout_seconds && typeof task.phase_timeout_seconds === 'object')
          ? task.phase_timeout_seconds
          : {};
        const keys = ['proposal', 'discussion', 'implementation', 'review', 'command'];
        const parts = [];
        for (const key of keys) {
          const value = Number(source[key]);
          if (!Number.isFinite(value) || value <= 0) continue;
          parts.push(`${key}=${Math.floor(value)}s`);
        }
        return parts.length ? parts.join(', ') : 'n/a';
      })();
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
        { label: 'MemoryMode', value: String(task.memory_mode || 'basic') },
        { label: 'PhaseTimeouts', value: phaseTimeoutText },
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
      return historyItemsInProject({ state, normalizeProjectPath });
    }

    function formatHistoryTime(value) {
      return formatHistoryTimeValue(value);
    }

    function parseEventDate(raw) {
      return parseHistoryDate(raw);
    }

    function formatRevisionSummary(revisions) {
      return formatRevisionSummaryValue(revisions);
    }

    function renderHistoryCollapseState() {
      return renderHistoryCollapseUi({
        state,
        projectHistoryBodyEl: el.projectHistoryBody,
        toggleHistoryBtnEl: el.toggleHistoryBtn,
      });
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

    function renderProjectHistory() {
      return renderProjectHistoryPanel({
        state,
        el,
        normalizeProjectPath,
        escapeHtml,
        statusPill,
        persistSelectionPreference,
        renderTaskSelect,
        renderTaskSnapshot,
        refreshConversation,
        historyItemsInSelectedProjectFn: historyItemsInSelectedProject,
        formatHistoryTimeFn: formatHistoryTime,
        formatRevisionSummaryFn: formatRevisionSummary,
      });
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
      if (el.memoryMode && defaults.memory_mode) el.memoryMode.value = String(defaults.memory_mode);
      writePhaseTimeoutSecondsToForm(defaults.phase_timeout_seconds || {});
      if (el.evolutionLevel) el.evolutionLevel.value = mapNum(defaults.evolution_level, 0);
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

    function renderDialogue(events) {
      const task = selectedTask();
      state.lastDialogueSignature = renderDialoguePanel({
        dialogueEl: el.dialogue,
        task,
        events,
        selectedRole: state.selectedRole,
        showStreamDetails: state.showStreamDetails,
        previousSignature: state.lastDialogueSignature,
        avatarHtml: avatarRenderer.avatarHtml,
        parseEventDate,
      });
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
        { label: 'Prefix Reuse 50', value: `${(Number(stats.prompt_prefix_reuse_rate_50 || 0) * 100).toFixed(1)}%` },
        { label: 'Cache Break 50', value: String(Number(stats.prompt_cache_break_count_50 || 0)) },
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
        `AvgSec50=${Number(stats.mean_task_duration_seconds_50 || 0).toFixed(1)} | ` +
        `PrefixReuse50=${(Number(stats.prompt_prefix_reuse_rate_50 || 0) * 100).toFixed(1)}% | ` +
        `CacheBreak50=${Number(stats.prompt_cache_break_count_50 || 0)} ` +
        `(model=${Number(stats.prompt_cache_break_model_50 || 0)}, toolset=${Number(stats.prompt_cache_break_toolset_50 || 0)}, prefix=${Number(stats.prompt_cache_break_prefix_50 || 0)})`;
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
        state.providerModelCatalog = normalizeProviderModelCatalog(
          providers,
          state.providerModelCatalog || {},
        );
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
      return clearProjectHistoryForScope({
        state,
        normalizeProjectPath,
        historyItemsInSelectedProjectFn: historyItemsInSelectedProject,
        tasksInSelectedProjectFn: tasksInSelectedProject,
        actionStatusEl: el.actionStatus,
        api,
        loadData,
      });
    }
    async function createTask(autoStart) {
      const evolveUntilRaw = String(document.getElementById('evolveUntil').value || '').trim();
      const sandboxPathRaw = String(document.getElementById('sandboxWorkspacePath').value || '').trim();
      const mergeTargetRaw = String(document.getElementById('mergeTargetPath').value || '').trim();
      let phaseTimeoutSeconds = {};
      try {
        phaseTimeoutSeconds = parsePhaseTimeoutSecondsFromForm();
      } catch (err) {
        el.createStatus.textContent = String(err || 'Invalid phase timeout config.');
        return;
      }
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
        memory_mode: String((el.memoryMode && el.memoryMode.value) || 'basic').trim().toLowerCase() || 'basic',
        phase_timeout_seconds: phaseTimeoutSeconds,
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
  


