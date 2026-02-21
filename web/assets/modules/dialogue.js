export function eventActorProvider(event, actor) {
  const payload = event.payload || {};
  if (payload.provider) return String(payload.provider || '').trim().toLowerCase();
  if (payload.participant && String(payload.participant).includes('#')) {
    return String(payload.participant).split('#')[0].toLowerCase();
  }
  if (actor && String(actor).includes('#')) {
    return String(actor).split('#')[0].toLowerCase();
  }
  return String(actor || 'system').toLowerCase();
}

export function eventActor(event, task) {
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

export function stripControlLines(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  for (const raw of lines) {
    const line = String(raw || '');
    if (/^\s*(VERDICT|NEXT_ACTION)\s*:/i.test(line)) continue;
    out.push(line);
  }
  return out.join('\n').trim();
}

export function stripInternalNoise(text) {
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

export function isKeyDialogueEvent(type) {
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

export function verdictLabel(verdict, lang) {
  const v = String(verdict || '').trim().toLowerCase();
  if (lang === 'zh') {
    if (v === 'no_blocker') return '方案可执行（不代表无问题）';
    if (v === 'blocker') return '方案需补充后再执行';
    return '信息不足，需要补充';
  }
  if (v === 'no_blocker') return 'Plan executable (not bug-free)';
  if (v === 'blocker') return 'Plan needs revision before execution';
  return 'Insufficient info';
}

function parseControlJson(raw) {
  const text = String(raw || '').trim();
  if (!text || !text.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== 'object') return null;
    return {
      verdict: String(parsed.verdict || '').trim().toLowerCase(),
      issue: String(parsed.issue || '').trim(),
      impact: String(parsed.impact || '').trim(),
      next: String(parsed.next || '').trim(),
    };
  } catch {
    return null;
  }
}

export function eventText(event, task, { showStreamDetails = false } = {}) {
  const payload = event.payload || {};
  const plainMode = !!(task && task.plain_mode !== false);
  const lang = String((task && task.conversation_language) || 'en').toLowerCase();

  if (payload.output) {
    const raw = String(payload.output || '');
    if (plainMode) {
      const cleaned = stripControlLines(stripInternalNoise(raw));
      if (event.type === 'proposal_review' || event.type === 'proposal_precheck_review' || event.type === 'review') {
        const control = parseControlJson(raw);
        const verdict = verdictLabel((control && control.verdict) || payload.verdict, lang);
        const issue = (control && control.issue) || cleaned || (lang === 'zh' ? '无' : 'n/a');
        const impact = (control && control.impact) || '';
        const next = (control && control.next) || '';
        if (lang === 'zh') {
          let text = `结论: ${verdict}\n发现: ${issue}`;
          if (impact) text += `\n影响: ${impact}`;
          if (next) text += `\n建议: ${next}`;
          return text;
        }
        let text = `Conclusion: ${verdict}\nFinding: ${issue}`;
        if (impact) text += `\nImpact: ${impact}`;
        if (next) text += `\nNext: ${next}`;
        return text;
      }
      return cleaned || raw || (lang === 'zh' ? '暂无可读内容' : 'No readable content yet');
    }
    return raw;
  }
  if (payload.chunk) {
    const streamName = String(payload.stream || 'stdout').toLowerCase();
    if (!showStreamDetails) return '';
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

export function eventClass(event) {
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

export function dialogueSignature(task, events, role, { showStreamDetails = false } = {}) {
  const taskId = task ? String(task.task_id || '') : 'none';
  const list = Array.isArray(events) ? events : [];
  const last = list.length ? list[list.length - 1] : null;
  const lastSeq = last ? String(last.seq ?? '') : '';
  const lastType = last ? String(last.type || '') : '';
  const lastCreatedAt = last ? String(last.created_at || '') : '';
  const streamMode = showStreamDetails ? 'stream-on' : 'stream-off';
  return `${taskId}|${String(role || 'all')}|${streamMode}|${list.length}|${lastSeq}|${lastType}|${lastCreatedAt}`;
}

export function renderDialoguePanel({
  dialogueEl,
  task,
  events,
  selectedRole,
  showStreamDetails,
  previousSignature,
  avatarHtml,
  parseEventDate,
}) {
  const stream = Array.isArray(events) ? events : [];
  const filtered = stream.filter((event) => {
    if (String(event.type || '') === 'participant_stream' && !showStreamDetails) return false;
    if (selectedRole === 'all') return true;
    return eventActor(event, task) === selectedRole;
  });
  const displayItems = [];
  for (const event of filtered) {
    const text = String(eventText(event, task, { showStreamDetails }) || '').trim();
    if (!text && !isKeyDialogueEvent(event.type)) {
      continue;
    }
    displayItems.push({ event, text });
  }
  const displayEvents = displayItems.map((item) => item.event);
  const signature = dialogueSignature(task, displayEvents, selectedRole, { showStreamDetails });
  if (signature === previousSignature) {
    return previousSignature;
  }

  dialogueEl.innerHTML = '';
  if (!task) {
    dialogueEl.innerHTML = '<div class="empty">Select a project/task to view dialogue.</div>';
    return signature;
  }
  if (!displayItems.length) {
    dialogueEl.innerHTML = '<div class="empty">No dialogue for this role scope yet.</div>';
    return signature;
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
    dialogueEl.appendChild(item);
  }
  requestAnimationFrame(() => { dialogueEl.scrollTop = dialogueEl.scrollHeight; });
  return signature;
}
