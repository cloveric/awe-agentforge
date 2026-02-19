import { normalizeProjectPath } from './utils.js';

export function readPollPreference() {
  try {
    const raw = String(localStorage.getItem('awe-agentcheck-poll') || '').trim();
    if (raw === '0') return false;
    if (raw === '1') return true;
  } catch {
  }
  return true;
}

export function readSelectionPreference(key = 'awe-agentcheck-selection') {
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
  key = 'awe-agentcheck-selection',
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
