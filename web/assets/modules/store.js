import { normalizeProjectPath } from './utils.js';

export const DEFAULT_PROVIDER_KEYS = Object.freeze(['claude', 'codex', 'gemini']);

export const THEME_OPTIONS = [
  { id: 'neon', label: 'Neon Grid' },
  { id: 'pixel', label: 'Terminal Pixel' },
  { id: 'pixel-sw', label: 'Terminal Pixel: Star Wars' },
  { id: 'pixel-sg', label: 'Terminal Pixel: Three Kingdoms' },
  { id: 'executive', label: 'Executive Glass' },
];

export const SELECTION_PREF_KEY = 'awe-agentcheck-selection';
const THEME_PREF_KEY = 'awe-agentcheck-theme';

export function createInitialState() {
  const providerModelCatalog = {};
  for (const provider of DEFAULT_PROVIDER_KEYS) {
    providerModelCatalog[provider] = [];
  }
  return {
    tasks: [],
    historyItems: [],
    stats: null,
    analytics: null,
    policyTemplates: null,
    historyLoadedOnce: false,
    providerModelCatalog,
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
}

export function normalizeProviderModelCatalog(rawCatalog, fallbackCatalog = null) {
  const source = rawCatalog && typeof rawCatalog === 'object' ? rawCatalog : {};
  const fallback = fallbackCatalog && typeof fallbackCatalog === 'object' ? fallbackCatalog : {};
  const out = {};
  for (const provider of DEFAULT_PROVIDER_KEYS) {
    const seen = new Set();
    const merged = [
      ...(Array.isArray(source[provider]) ? source[provider] : []),
      ...(Array.isArray(fallback[provider]) ? fallback[provider] : []),
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

export function applySavedSelection(state, savedSelection) {
  if (!state || !savedSelection) return;
  state.selectedProject = savedSelection.selectedProject;
  state.selectedTaskId = savedSelection.selectedTaskId;
  state.selectedRole = savedSelection.selectedRole;
  state.selectionNeedsValidation = true;
}

export function normalizeTheme(themeId) {
  const value = String(themeId || '').trim().toLowerCase();
  return THEME_OPTIONS.some((theme) => theme.id === value) ? value : 'neon';
}

export function readThemePreference() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_PREF_KEY));
  } catch {
    return 'neon';
  }
}

export function applyTheme(state, themeId, { persist = true, themeSelectEl = null } = {}) {
  if (!state) return 'neon';
  const theme = normalizeTheme(themeId);
  state.theme = theme;
  if (typeof document !== 'undefined' && document.body) {
    document.body.dataset.theme = theme;
  }
  if (themeSelectEl) {
    themeSelectEl.value = theme;
  }
  if (persist) {
    try {
      localStorage.setItem(THEME_PREF_KEY, theme);
    } catch {
    }
  }
  return theme;
}

export function setApiHealth(state, ok, detail = '', options = {}, { connBadgeEl = null, actionStatusEl = null } = {}) {
  if (!state) return;
  const increment = options.increment !== undefined ? !!options.increment : true;
  state.apiHealthy = !!ok;
  if (ok) {
    state.apiFailureCount = 0;
    if (connBadgeEl) {
      connBadgeEl.className = 'pill ok';
      connBadgeEl.textContent = 'API: ONLINE';
    }
    return;
  }
  if (increment) {
    state.apiFailureCount += 1;
  } else if (state.apiFailureCount <= 0) {
    state.apiFailureCount = 1;
  }
  if (connBadgeEl) {
    connBadgeEl.className = 'pill warn';
    connBadgeEl.textContent = `API: RETRY(${state.apiFailureCount})`;
  }
  if (detail && actionStatusEl) {
    actionStatusEl.textContent = `API unstable: ${detail}`;
  }
}

export function pruneParticipantCapabilityDraft(state, activeParticipants) {
  if (!state) return {};
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
  return next;
}

export function readPollPreference() {
  try {
    const raw = String(localStorage.getItem('awe-agentcheck-poll') || '').trim();
    if (raw === '0') return false;
    if (raw === '1') return true;
  } catch {
  }
  return true;
}

export function readSelectionPreference(key = SELECTION_PREF_KEY) {
  try {
    const raw = String(localStorage.getItem(key) || '').trim();
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const selectedProject = normalizeProjectPath(parsed.selectedProject || '') || null;
    const selectedTaskId = String(parsed.selectedTaskId || '').trim() || null;
    const selectedRoleRaw = String(parsed.selectedRole || '').trim();
    const selectedRole = selectedRoleRaw || 'all';
    return { selectedProject, selectedTaskId, selectedRole };
  } catch {
    return null;
  }
}

export function persistSelectionPreference(
  {
    selectedProject,
    selectedTaskId,
    selectedRole,
  },
  key = SELECTION_PREF_KEY,
) {
  try {
    const project = normalizeProjectPath(selectedProject || '') || '';
    const taskId = String(selectedTaskId || '').trim();
    const roleRaw = String(selectedRole || '').trim();
    const role = roleRaw || 'all';
    if (!project && !taskId && role === 'all') {
      localStorage.removeItem(key);
      return;
    }
    localStorage.setItem(
      key,
      JSON.stringify({
        selectedProject: project,
        selectedTaskId: taskId,
        selectedRole: role,
      }),
    );
  } catch {
  }
}

export function readStreamDetailPreference() {
  try {
    const raw = String(localStorage.getItem('awe-agentcheck-stream-detail') || '').trim();
    if (raw === '1') return true;
    if (raw === '0') return false;
  } catch {
  }
  return false;
}

export function readHistoryCollapsePreference() {
  try {
    return localStorage.getItem('awe-agentcheck-history-collapsed') === '1';
  } catch {
    return false;
  }
}

export function readCreateHelpCollapsedPreference() {
  try {
    const raw = String(localStorage.getItem('awe-agentcheck-create-help-collapsed') || '').trim();
    if (raw === '0') return false;
    if (raw === '1') return true;
  } catch {
  }
  return true;
}

export function readCreateHelpLanguagePreference() {
  try {
    const current = String(localStorage.getItem('awe-agentcheck-create-help-lang') || '').trim().toLowerCase();
    if (current === 'en' || current === 'zh') return current;
    const legacy = String(localStorage.getItem('awe-agentcheck-create-help-language') || '').trim().toLowerCase();
    if (legacy === 'en' || legacy === 'zh') return legacy;
  } catch {
  }
  return 'zh';
}
