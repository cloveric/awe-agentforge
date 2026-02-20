export function historyItemsInProject({ state, normalizeProjectPath }) {
  const key = normalizeProjectPath(state.selectedProject || '');
  const all = Array.isArray(state.historyItems) ? state.historyItems : [];
  if (!key) return all;
  return all.filter((item) => normalizeProjectPath(item.project_path || '') === key);
}

export function parseHistoryDate(raw) {
  const text = String(raw || '').trim();
  if (!text) return new Date(NaN);
  const hasOffset = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text);
  const normalized = hasOffset ? text : `${text}Z`;
  return new Date(normalized);
}

export function formatHistoryTimeValue(value) {
  const text = String(value || '').trim();
  if (!text) return 'n/a';
  const dt = parseHistoryDate(text);
  if (Number.isNaN(dt.getTime())) return text;
  return dt.toLocaleString();
}

export function formatRevisionSummaryValue(revisions) {
  const rev = revisions && typeof revisions === 'object' ? revisions : {};
  if (!rev.auto_merge) return 'auto-merge: off or not reached';
  const changed = Number(rev.changed_files || 0);
  const copied = Number(rev.copied_files || 0);
  const deleted = Number(rev.deleted_files || 0);
  const mode = String(rev.mode || 'n/a');
  return `mode=${mode} | changed=${changed} copied=${copied} deleted=${deleted}`;
}

export function renderHistoryCollapseUi({ state, projectHistoryBodyEl, toggleHistoryBtnEl }) {
  const collapsed = !!state.historyCollapsed;
  if (projectHistoryBodyEl) {
    projectHistoryBodyEl.classList.toggle('is-collapsed', collapsed);
  }
  if (toggleHistoryBtnEl) {
    toggleHistoryBtnEl.textContent = collapsed ? 'Expand' : 'Collapse';
    toggleHistoryBtnEl.title = collapsed ? 'Expand project history' : 'Collapse project history';
    toggleHistoryBtnEl.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}

export function renderProjectHistoryPanel({
  state,
  el,
  normalizeProjectPath,
  escapeHtml,
  statusPill,
  persistSelectionPreference,
  renderTaskSelect,
  renderTaskSnapshot,
  refreshConversation,
  historyItemsInSelectedProjectFn,
  formatHistoryTimeFn,
  formatRevisionSummaryFn,
}) {
  const scoped = historyItemsInSelectedProjectFn();
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
        <span>${escapeHtml(formatHistoryTimeFn(item.updated_at || item.created_at))}</span>
      </div>
      <div class="history-meta"><strong>Core Findings</strong>\n${escapeHtml(findingsText)}</div>
      <div class="history-meta"><strong>Revisions</strong>\n${escapeHtml(formatRevisionSummaryFn(item.revisions))}</div>
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

export async function clearProjectHistoryForScope({
  state,
  normalizeProjectPath,
  historyItemsInSelectedProjectFn,
  tasksInSelectedProjectFn,
  actionStatusEl,
  api,
  loadData,
}) {
  const normalizedProject = normalizeProjectPath(state.selectedProject || '');
  const scopedHistory = historyItemsInSelectedProjectFn();
  const scopedLive = tasksInSelectedProjectFn();
  if (!scopedHistory.length && !scopedLive.length) {
    actionStatusEl.textContent = 'No history records or live tasks to clear for current scope.';
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
    actionStatusEl.textContent = `History/task cleanup completed: deleted=${deleted}, artifacts=${artifacts}, skipped=${skipped}.`;
    await loadData({ forceEvents: true });
  } catch (err) {
    actionStatusEl.textContent = `Clear history failed: ${String(err)}`;
  }
}
