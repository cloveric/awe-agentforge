export function renderModelSelect(elm, values) {
  if (!elm) return;
  const current = String(elm.value || '').trim();
  elm.innerHTML = '';
  const list = Array.isArray(values) ? values : [];
  const seen = new Set();
  const normalized = [];
  for (const raw of list) {
    const text = String(raw || '').trim();
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    normalized.push(text);
  }
  if (current && !seen.has(current.toLowerCase())) {
    normalized.unshift(current);
  }
  for (const text of normalized) {
    const option = document.createElement('option');
    option.value = text;
    option.textContent = text;
    elm.appendChild(option);
  }
  if (normalized.length) {
    elm.value = normalized.includes(current) ? current : normalized[0];
  }
}

export function initElements(doc = document) {
  const grouped = {
    project: {
      select: doc.getElementById('projectSelect'),
      tree: doc.getElementById('projectTree'),
      treeMeta: doc.getElementById('projectTreeMeta'),
      taskSelect: doc.getElementById('taskSelect'),
      taskSnapshot: doc.getElementById('taskSnapshot'),
      roleList: doc.getElementById('roleList'),
      dialogue: doc.getElementById('dialogue'),
    },
    summary: {
      statsLine: doc.getElementById('statsLine'),
      kpiStrip: doc.getElementById('kpiStrip'),
      analyticsSummary: doc.getElementById('analyticsSummary'),
      connBadge: doc.getElementById('connBadge'),
      actionStatus: doc.getElementById('actionStatus'),
      themeSelect: doc.getElementById('themeSelect'),
    },
    github: {
      summaryMeta: doc.getElementById('githubSummaryMeta'),
      summaryText: doc.getElementById('githubSummaryText'),
      reloadBtn: doc.getElementById('reloadGithubSummaryBtn'),
    },
    history: {
      container: doc.getElementById('projectHistory'),
      summary: doc.getElementById('historySummary'),
      body: doc.getElementById('projectHistoryBody'),
      clearBtn: doc.getElementById('clearHistoryBtn'),
      toggleBtn: doc.getElementById('toggleHistoryBtn'),
    },
    controls: {
      pollBtn: doc.getElementById('pollBtn'),
      streamDetailBtn: doc.getElementById('streamDetailBtn'),
      startBtn: doc.getElementById('startBtn'),
      cancelBtn: doc.getElementById('cancelBtn'),
      forceFailBtn: doc.getElementById('forceFailBtn'),
      customReplyBtn: doc.getElementById('customReplyBtn'),
      promoteRoundBtn: doc.getElementById('promoteRoundBtn'),
      promoteRound: doc.getElementById('promoteRound'),
      forceReason: doc.getElementById('forceReason'),
      manualReplyNote: doc.getElementById('manualReplyNote'),
      expandTreeBtn: doc.getElementById('expandTreeBtn'),
      collapseTreeBtn: doc.getElementById('collapseTreeBtn'),
      approveQueueBtn: doc.getElementById('approveQueueBtn'),
      approveStartBtn: doc.getElementById('approveStartBtn'),
      rejectBtn: doc.getElementById('rejectBtn'),
    },
    create: {
      status: doc.getElementById('createStatus'),
      openHelpBtn: doc.getElementById('openCreateHelpBtn'),
      closeHelpBtn: doc.getElementById('closeCreateHelpBtn'),
      helpPanel: doc.getElementById('createHelpPanel'),
      helpHint: doc.getElementById('createHelpHint'),
      helpList: doc.getElementById('createHelpList'),
      helpLangEnBtn: doc.getElementById('createHelpLangEnBtn'),
      helpLangZhBtn: doc.getElementById('createHelpLangZhBtn'),
      policyTemplate: doc.getElementById('policyTemplate'),
      applyPolicyTemplateBtn: doc.getElementById('applyPolicyTemplateBtn'),
      policyProfileHint: doc.getElementById('policyProfileHint'),
      workspacePath: doc.getElementById('workspacePath'),
      author: doc.getElementById('author'),
      reviewers: doc.getElementById('reviewers'),
      matrixAddReviewerBtn: doc.getElementById('matrixAddReviewerBtn'),
      selfLoopMode: doc.getElementById('selfLoopMode'),
      participantCapabilityMatrix: doc.getElementById('participantCapabilityMatrix'),
      sandboxMode: doc.getElementById('sandboxMode'),
      autoMerge: doc.getElementById('autoMerge'),
      mergeTargetPath: doc.getElementById('mergeTargetPath'),
      evolveUntil: doc.getElementById('evolveUntil'),
      maxRounds: doc.getElementById('maxRounds'),
      repairMode: doc.getElementById('repairMode'),
      plainMode: doc.getElementById('plainMode'),
      streamMode: doc.getElementById('streamMode'),
      debateMode: doc.getElementById('debateMode'),
    },
    providers: {
      claudeModel: doc.getElementById('claudeModel'),
      codexModel: doc.getElementById('codexModel'),
      geminiModel: doc.getElementById('geminiModel'),
      claudeModelCustom: doc.getElementById('claudeModelCustom'),
      codexModelCustom: doc.getElementById('codexModelCustom'),
      geminiModelCustom: doc.getElementById('geminiModelCustom'),
      claudeModelParams: doc.getElementById('claudeModelParams'),
      codexModelParams: doc.getElementById('codexModelParams'),
      geminiModelParams: doc.getElementById('geminiModelParams'),
    },
  };
  return {
    ...grouped,
    projectSelect: grouped.project.select,
    projectTree: grouped.project.tree,
    projectTreeMeta: grouped.project.treeMeta,
    roleList: grouped.project.roleList,
    statsLine: grouped.summary.statsLine,
    kpiStrip: grouped.summary.kpiStrip,
    analyticsSummary: grouped.summary.analyticsSummary,
    taskSelect: grouped.project.taskSelect,
    dialogue: grouped.project.dialogue,
    githubSummaryMeta: grouped.github.summaryMeta,
    githubSummaryText: grouped.github.summaryText,
    reloadGithubSummaryBtn: grouped.github.reloadBtn,
    actionStatus: grouped.summary.actionStatus,
    taskSnapshot: grouped.project.taskSnapshot,
    projectHistory: grouped.history.container,
    historySummary: grouped.history.summary,
    projectHistoryBody: grouped.history.body,
    clearHistoryBtn: grouped.history.clearBtn,
    toggleHistoryBtn: grouped.history.toggleBtn,
    openCreateHelpBtn: grouped.create.openHelpBtn,
    closeCreateHelpBtn: grouped.create.closeHelpBtn,
    createHelpPanel: grouped.create.helpPanel,
    createHelpHint: grouped.create.helpHint,
    createHelpList: grouped.create.helpList,
    createHelpLangEnBtn: grouped.create.helpLangEnBtn,
    createHelpLangZhBtn: grouped.create.helpLangZhBtn,
    createStatus: grouped.create.status,
    pollBtn: grouped.controls.pollBtn,
    streamDetailBtn: grouped.controls.streamDetailBtn,
    startBtn: grouped.controls.startBtn,
    cancelBtn: grouped.controls.cancelBtn,
    forceFailBtn: grouped.controls.forceFailBtn,
    customReplyBtn: grouped.controls.customReplyBtn,
    promoteRoundBtn: grouped.controls.promoteRoundBtn,
    promoteRound: grouped.controls.promoteRound,
    forceReason: grouped.controls.forceReason,
    manualReplyNote: grouped.controls.manualReplyNote,
    connBadge: grouped.summary.connBadge,
    themeSelect: grouped.summary.themeSelect,
    expandTreeBtn: grouped.controls.expandTreeBtn,
    collapseTreeBtn: grouped.controls.collapseTreeBtn,
    approveQueueBtn: grouped.controls.approveQueueBtn,
    approveStartBtn: grouped.controls.approveStartBtn,
    rejectBtn: grouped.controls.rejectBtn,
    policyTemplate: grouped.create.policyTemplate,
    applyPolicyTemplateBtn: grouped.create.applyPolicyTemplateBtn,
    policyProfileHint: grouped.create.policyProfileHint,
    workspacePath: grouped.create.workspacePath,
    author: grouped.create.author,
    reviewers: grouped.create.reviewers,
    matrixAddReviewerBtn: grouped.create.matrixAddReviewerBtn,
    selfLoopMode: grouped.create.selfLoopMode,
    claudeModel: grouped.providers.claudeModel,
    codexModel: grouped.providers.codexModel,
    geminiModel: grouped.providers.geminiModel,
    claudeModelCustom: grouped.providers.claudeModelCustom,
    codexModelCustom: grouped.providers.codexModelCustom,
    geminiModelCustom: grouped.providers.geminiModelCustom,
    claudeModelParams: grouped.providers.claudeModelParams,
    codexModelParams: grouped.providers.codexModelParams,
    geminiModelParams: grouped.providers.geminiModelParams,
    participantCapabilityMatrix: grouped.create.participantCapabilityMatrix,
    sandboxMode: grouped.create.sandboxMode,
    autoMerge: grouped.create.autoMerge,
    mergeTargetPath: grouped.create.mergeTargetPath,
    evolveUntil: grouped.create.evolveUntil,
    maxRounds: grouped.create.maxRounds,
    repairMode: grouped.create.repairMode,
    plainMode: grouped.create.plainMode,
    streamMode: grouped.create.streamMode,
    debateMode: grouped.create.debateMode,
  };
}

export function renderParticipantCapabilityMatrixHtml({
  roleRows,
  draftMap,
  parseProvider,
  providerDefaultsFromForm,
  participantModelOptions,
  escapeHtml,
}) {
  const rows = roleRows.map((roleRow, rowIndex) => {
    const role = String((roleRow && roleRow.role) || 'reviewer').trim().toLowerCase() === 'author'
      ? 'author'
      : 'reviewer';
    const participantId = String((roleRow && roleRow.participantId) || '').trim();
    const provider = participantId ? parseProvider(participantId) : '';
    const defaults = providerDefaultsFromForm(provider);
    const draft = participantId ? (draftMap[participantId] || {}) : {};
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
  return rows;
}
