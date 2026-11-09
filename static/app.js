const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
let trackingMode = true;
let allTasks = [];
let taskFilter = 'active';
let taskPage = 1;
const taskPageSize = 6;
let analysisDate = '';
let taskSearch = '';
let searchResults = null;
let searchTimer;
let searchVersion = 0;
let selectedStrategyId = null;
let reportCapabilities = null;
let currentReportJob = null;
let reportRunning = false;
let deliveryTaskId = null;
let progressTaskId = null;

function riskLabel(risk) {
  return { high: '高风险', medium: '需关注', low: '时间可控' }[risk] || risk;
}

function confidenceLabel(level) {
  return { high: '预测可信度高', medium: '预测可信度中', low: '预测可信度低', not_applicable: '已结束，不再预测' }[level] || '预测可信度待定';
}

function completionLabel(task) {
  if (task.completion_scope === 'partial') return task.completion_type === 'late' ? '逾期部分交付·已结束' : '部分交付·已结束';
  return { early: '提前完整完成', on_time: '按时完整完成', late: '逾期完整完成' }[task.completion_type] || '已完整完成';
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || '请求失败');
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function renderMetrics(data) {
  $('#metrics').innerHTML = [
    ['待处理任务', allTasks.filter((task) => task.status !== 'done').length, '项', 'neutral'],
    ['待完成工时', data.planned_hours, '小时', 'neutral'],
    ['高风险', data.high_risk_tasks, '项', data.high_risk_tasks ? 'danger' : 'safe'],
    ['排程缺口', data.schedule_missed_tasks, '项', data.schedule_missed_tasks ? 'warning' : 'safe'],
  ].map(([label, value, unit, tone]) => `<div class="metric ${tone}"><span>${label}</span><strong>${value}</strong><small>${unit}</small></div>`).join('');
}

function renderActions(tasks) {
  const target = $('#action-list');
  if (!tasks.length) {
    target.innerHTML = '<div class="empty compact"><strong>当前没有待执行任务</strong><span>新增任务后，这里会给出建议顺序。</span></div>';
    return;
  }
  target.innerHTML = tasks.map((task, index) => `
    <article class="action-card ${task.schedule_status === 'late' ? 'late' : ''}">
      <span class="action-rank">${index + 1}</span>
      <div class="action-copy">
        <strong>${escapeHtml(task.title)}</strong>
        <span>${escapeHtml(task.course || '未分类')} · 截止 ${escapeHtml(task.due_date)}</span>
        <small>${escapeHtml(task.schedule_note || task.explanation)}</small>
      </div>
      <div class="action-hours"><strong>${task.remaining_hours}h</strong><span>预计剩余</span></div>
    </article>`).join('');
}

function renderAttention() {
  const model = RadarTaskView.attention(allTasks, analysisDate);
  $('#attention-summary').textContent = `${model.attentionCount} 项需关注 · 分析日期 ${analysisDate}`;
  const group = (title, tasks, filter, note) => `<div class="attention-group ${filter}">
    <div class="attention-heading"><h3>${title}<span>${tasks.length} 项</span></h3><button type="button" class="ghost" data-attention-filter="${filter}">查看全部</button></div>
    <p class="attention-description">${note}</p>
    ${tasks.length ? tasks.slice(0, 3).map((task) => {
      const deadline = RadarTaskView.urgency(task, analysisDate);
      return `<button type="button" class="attention-task" data-locate-task="${Number(task.id)}">
        <span class="attention-task-title">${escapeHtml(task.title)}</span>
        <span class="attention-task-info"><span class="deadline-badge ${deadline?.kind || 'normal'}">${deadline ? deadline.label : `截止 ${escapeHtml(task.due_date)}`}</span><span>重要度${{3:'高',2:'中',1:'低'}[task.priority] || '未设'} · 剩余 ${Number(task.remaining_hours ?? task.estimated_hours)}h</span></span>
      </button>`;
    }).join('') : '<p class="attention-empty">暂无此类待处理任务</p>'}
    ${tasks.length > 3 ? `<p class="muted">另有 ${tasks.length - 3} 项，点击“查看全部”筛选。</p>` : ''}
  </div>`;
  $('#attention-list').innerHTML = group('临期／已逾期', model.urgent, 'urgent', `已逾期 ${model.overdue} · 今天截止 ${model.today} · 明后天截止 ${model.soon}`)
    + group('高风险任务', model.highRisk, 'high', '即使截止日期较远，也可能因前序任务占用时间而高风险。');
  document.querySelectorAll('[data-attention-filter]').forEach((button) => button.addEventListener('click', () => {
    selectTaskFilter(button.dataset.attentionFilter, true);
    $('.risk-board').scrollIntoView({behavior:'smooth', block:'start'});
  }));
  document.querySelectorAll('[data-locate-task]').forEach((button) => button.addEventListener('click', () => locateTask(button.dataset.locateTask, 'active')));
}

function selectTaskFilter(filter, clearSearch = false) {
  taskFilter = filter;
  taskPage = 1;
  if (clearSearch) {
    clearTimeout(searchTimer); searchVersion += 1;
    taskSearch = ''; searchResults = null;
    $('#task-search').value = ''; $('#search-status').textContent = '';
  }
  document.querySelectorAll('.filter').forEach((button) => {
    const selected = button.dataset.filter === filter;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  renderTasks();
}

function locateTask(id, filter) {
  selectTaskFilter(filter, true);
  const index = filteredTasks().findIndex((task) => String(task.id) === String(id));
  if (index < 0) return;
  taskPage = Math.floor(index / taskPageSize) + 1;
  renderTasks();
  const card = document.getElementById(`task-${id}`);
  if (card) { card.scrollIntoView({behavior:'smooth', block:'start'}); card.focus({preventScroll:true}); }
}

function renderTradeoffs(plan) {
  const panel = $('#tradeoff-panel');
  if (!plan || plan.status !== 'overloaded') {
    panel.hidden = true;
    selectedStrategyId = null;
    $('#tradeoff-content').innerHTML = '';
    return;
  }
  panel.hidden = false;
  const strategies = plan.strategies || [];
  if (!strategies.some((strategy) => strategy.id === selectedStrategyId)) {
    selectedStrategyId = plan.recommended_strategy || strategies[0]?.id;
  }
  const chosen = strategies.find((strategy) => strategy.id === selectedStrategyId);
  const suggestions = [...(chosen?.adjusted_tasks || plan.suggestions || [])].sort((a, b) => Number(b.priority) - Number(a.priority) || String(a.due_date).localeCompare(String(b.due_date)));
  const visibleSuggestions = suggestions.slice(0, 6);
  const labels = { 1: '低', 2: '中', 3: '高' };
  const suggestionCard = (item) => `
    <article class="tradeoff-card">
      <div><strong>${escapeHtml(item.title)}</strong><span>重要度：${labels[item.priority] || '未设'} · 截止 ${escapeHtml(item.due_date)} · 预计剩余 ${Number(item.remaining_hours)}h</span></div>
      <div class="tradeoff-relief"><b>期限内保留 ${Number(item.retained_hours ?? 0)}h · 另有 ${Number(item.suggested_reduction_hours)}h 待协商</b><span>${escapeHtml(item.proposal)}</span></div>
      ${item.minimum_delivery_percent != null ? `<p>已设最低交付比例 ${Number(item.minimum_delivery_percent)}%，至少保留 ${Number(item.minimum_retained_hours)}h 剩余工时。</p>` : '<p>核心交付尚未确认，不代表允许舍弃这些内容。</p>'}
      <p>${escapeHtml(item.reason)}${item.prediction_confidence === 'low' ? ' 当前估时可信度低，建议先复核。' : ''}</p>
    </article>`;
  $('#tradeoff-content').innerHTML = `
    <p class="tradeoff-lead">最早在 <strong>${escapeHtml(plan.critical_due_date)}</strong> 出现缓冲后缺口 <strong>${Number(plan.critical_shortage_hours)}h</strong>。点击方案比较需要增加的投入或调整的任务。</p>
    <p class="strategy-recommendation"><strong>${plan.recommended_strategy ? `建议选择：${escapeHtml(strategies.find((strategy) => strategy.id === plan.recommended_strategy)?.name || '')}` : '当前没有满足交付底线与时间约束的方案'}</strong><span>${escapeHtml(plan.recommendation_reason || '')}</span></p>
    ${(plan.warnings || []).map((warning) => `<p class="constraint-warning">${escapeHtml(warning)}</p>`).join('')}
    ${(plan.unresolved_checkpoints || []).map((item) => `<p class="constraint-warning">截至 ${escapeHtml(item.due_date)}，必保交付至少需 ${Number(item.minimum_required_hours)}h，可用 ${Number(item.available_hours)}h，仍缺 ${Number(item.shortage_hours)}h。需增加容量或先协商期限，不能自动突破底线。</p>`).join('')}
    <div class="strategy-options">${strategies.map((strategy) => `
      <button type="button" class="strategy-option ${strategy.id === selectedStrategyId ? 'selected' : ''}" aria-pressed="${strategy.id === selectedStrategyId}" data-strategy="${escapeHtml(strategy.id)}">
        <strong>${escapeHtml(strategy.name)}</strong>
        ${!strategy.feasible ? '<small class="negative">当前条件下不可行</small>' : strategy.requires_confirmation ? '<small>容量情景可排入 · 范围和延期待确认</small>' : ''}
        <span>${strategy.id === plan.recommended_strategy ? '推荐 · ' : ''}${strategy.id === 'extra_effort' ? (strategy.additional_daily_hours == null ? '已有逾期任务' : `额外 ${Number(strategy.additional_daily_hours)}h / 天${strategy.feasible ? '' : ' · 超出当前上限'}`) : `完整保留 ${Number(strategy.full_completion_count)} 项 · 调整 ${strategy.adjusted_tasks.length} 项`}</span>
        <small>${escapeHtml(strategy.suitable_when)}</small>
      </button>`).join('')}</div>
    ${chosen ? `<div class="strategy-detail"><h3>${escapeHtml(chosen.name)}</h3><p>${escapeHtml(chosen.summary)}</p>${chosen.priority_loss_hours ? `<p class="priority-impact">原期限外工时：高重要度 ${Number(chosen.priority_loss_hours.high)}h ／ 中 ${Number(chosen.priority_loss_hours.medium)}h ／ 低 ${Number(chosen.priority_loss_hours.low)}h。工时不等同于内容价值。</p>` : ''}${chosen.id !== 'extra_effort' ? `<p class="muted">原期限外共约 ${Number(chosen.released_hours)}h；不自动删除任务，必须确认对应交付内容与延期条件。</p>` : ''}
      ${chosen.kept_tasks.length ? `<details class="strategy-kept"><summary>${chosen.id === 'extra_effort' ? '查看此方案涉及的全部任务' : `查看保留原范围的 ${chosen.kept_tasks.length} 项任务及执行顺序`}</summary><ol>${chosen.kept_tasks.map((task) => `<li><strong>${escapeHtml(task.title)}</strong><span>重要度${labels[task.priority]} · 截止 ${escapeHtml(task.due_date)} · ${Number(task.remaining_hours)}h</span></li>`).join('')}</ol></details>` : ''}</div>` : ''}
    <div class="tradeoff-list">${visibleSuggestions.map(suggestionCard).join('')}</div>
    ${suggestions.length > visibleSuggestions.length ? `<details class="tradeoff-more"><summary>展开其余 ${suggestions.length - visibleSuggestions.length} 项建议</summary><div class="tradeoff-list">${suggestions.slice(6).map(suggestionCard).join('')}</div></details>` : ''}
    <p class="tradeoff-note">${escapeHtml(plan.assumptions || plan.note || '')}</p>`;
  document.querySelectorAll('[data-strategy]').forEach((button) => button.addEventListener('click', () => {
    selectedStrategyId = button.dataset.strategy;
    renderTradeoffs(plan);
  }));
}

function filteredTasks() {
  const byId = new Map(allTasks.map((task) => [task.id, task]));
  const source = taskSearch && searchResults ? searchResults.map((id) => byId.get(id)).filter(Boolean) : allTasks;
  const matches = source.filter((task) => {
    const matchesFilter = taskFilter === 'all'
      || (taskFilter === 'active' && task.status !== 'done')
      || (taskFilter === 'high' && task.status !== 'done' && task.risk === 'high')
      || (taskFilter === 'urgent' && Boolean(RadarTaskView.urgency(task, analysisDate)))
      || (taskFilter === 'done' && task.status === 'done');
    const haystack = `${task.title || ''} ${task.course || ''}`.normalize('NFKC').toLowerCase();
    const terms = taskSearch.normalize('NFKC').toLowerCase().match(/[a-z0-9_]+|[\u4e00-\u9fff]+/g) || [];
    return matchesFilter && (!taskSearch || (terms.length > 0 && terms.every((term) => haystack.includes(term))));
  });
  return taskSearch ? matches : matches.sort(RadarTaskView.compareTasks);
}

async function searchCurrentTasks() {
  const query = taskSearch;
  const version = ++searchVersion;
  if (!query) {
    searchResults = null;
    $('#search-status').textContent = '';
    renderTasks();
    return;
  }
  $('#search-status').textContent = '检索中…';
  try {
    const result = await api(`/api/tasks/search?q=${encodeURIComponent(query)}`);
    if (version !== searchVersion || query !== taskSearch) return;
    searchResults = result.tasks.map((task) => task.id);
    $('#search-status').textContent = `找到 ${result.count} 项任务（含历史记录）；多个关键词需同时匹配。`;
    renderTasks();
  } catch (error) {
    if (version !== searchVersion || query !== taskSearch) return;
    searchResults = null;
    $('#search-status').textContent = `检索接口暂不可用，已使用本地匹配：${error.message}`;
    renderTasks();
  }
}

function renderHistoryEvidence(task) {
  if (task.status === 'done') return '';
  const matches = task.similar_history || [];
  const confidence = task.confidence_reason ? `<p><strong>可信度依据：</strong>${escapeHtml(task.confidence_reason)}</p>` : '';
  if (!matches.length) return `<details class="history-evidence"><summary>查看预测依据 · 暂无相似历史</summary>${confidence}<p>估时参考：${escapeHtml(task.history_calibration_note || '初始估时与总体历史效率。')}</p></details>`;
  return `<details class="history-evidence"><summary>查看预测依据 · ${matches.length} 项相似历史</summary>${confidence}<p>${escapeHtml(task.history_calibration_note)}</p><ul>${matches.map((item) => `
    <li><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.course || '未分类')} · 匹配分 ${Math.round(Number(item.similarity) * 100)}/100 · 预计 ${Number(item.estimated_hours)}h / 实际 ${Number(item.actual_hours)}h</span><small>${escapeHtml(item.reason)} · ${item.data_precision === 'approx' ? '回忆数据，降低权重' : '精确记录'}</small>${item.task_id != null ? `<button type="button" class="ghost view-history" data-history-id="${Number(item.task_id)}">查看历史记录</button>` : ''}</li>`).join('')}</ul><p class="muted">匹配分表示文字和课程相似程度，不表示难度相同的概率。</p></details>`;
}

function showHistoryTask(id) {
  locateTask(id, 'done');
}

function renderTasks() {
  const tasks = filteredTasks();
  const pagination = RadarTaskView.paginate(tasks, taskPage, taskPageSize);
  taskPage = pagination.page;
  $('#task-page-info').textContent = tasks.length
    ? `第 ${taskPage} / ${pagination.pages} 页 · 共 ${tasks.length} 项 · 显示第 ${pagination.start + 1}–${pagination.end} 项 · ${taskSearch ? '按关键词相关度展示' : taskFilter === 'done' ? '按结束时间由近到远' : taskFilter === 'all' ? '待处理优先，历史记录靠后' : '按截止日期，同日按重要程度排序'}`
    : '共 0 项';
  $('#task-pagination').innerHTML = pagination.pages > 1
    ? `<button type="button" data-page="${taskPage - 1}" ${taskPage === 1 ? 'disabled' : ''}>上一页</button><span>第 ${taskPage} / ${pagination.pages} 页</span><button type="button" data-page="${taskPage + 1}" ${taskPage === pagination.pages ? 'disabled' : ''}>下一页</button>` : '';
  document.querySelectorAll('[data-page]').forEach((button) => button.addEventListener('click', () => {
    taskPage = Number(button.dataset.page); renderTasks();
    $('#task-page-info').scrollIntoView({behavior:'smooth', block:'start'});
  }));
  if (!tasks.length) {
    $('#task-list').innerHTML = `<div class="empty"><strong>${allTasks.length ? '没有符合条件的任务' : '还没有任务'}</strong><span>${allTasks.length ? '换一个筛选条件或关键词试试。' : '先添加一个截止日期明确的任务吧。'}</span></div>`;
    return;
  }
  $('#task-list').innerHTML = pagination.items.map((task) => {
    const deadline = RadarTaskView.urgency(task, analysisDate);
    const partial = task.status === 'done' && task.completion_scope === 'partial';
    const progress = task.status === 'done' && !partial ? 100 : Math.max(0, Math.min(Number(task.progress_percent || 0), 99.99));
    const importance = { 3: '高', 2: '中', 1: '低' }[task.priority] || '未设';
    const finish = task.status === 'done'
      ? (task.completed_at ? `实际结束 ${escapeHtml(task.completed_at.slice(0, 16).replace('T', ' '))}` : '实际结束时间未记录')
      : task.projected_finish_date ? `预计 ${escapeHtml(task.projected_finish_date)} 完成` : '暂无法给出完成日';
    const shortfall = Number(task.shortfall_hours || 0);
    const slack = Number(task.slack_hours || 0);
    return `
      <article id="task-${Number(task.id)}" tabindex="-1" class="task-card ${task.risk} ${deadline ? `deadline-${deadline.kind}` : ''} ${task.status === 'done' ? 'completed' : ''}">
        <div class="task-main">
          <div class="task-title-row">
            ${task.recommended_rank ? `<span class="rank-badge">#${task.recommended_rank}</span>` : ''}
            <div><h3>${escapeHtml(task.title)}</h3><p class="task-subtitle">${escapeHtml(task.course || '未分类')}${task.is_historical ? ' · 历史记录' : ''}<span class="importance importance-${Number(task.priority)}">重要程度：${importance}</span></p></div>
          </div>
          <div class="task-badges">${deadline ? `<span class="deadline-badge ${deadline.kind}">${deadline.label}</span>` : ''}<span class="risk-pill ${partial || (task.status === 'done' && task.completion_type === 'late') ? 'medium' : task.risk}">${task.status === 'done' ? completionLabel(task) : riskLabel(task.risk)}</span></div>
        </div>
        <div class="progress-row">
          <span>${task.status === 'done' ? '交付比例' : '当前进度'}</span>
          <div class="progress-track" role="progressbar" aria-label="${task.status === 'done' ? '交付比例' : '当前进度'}" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progress}%"></span></div>
          <span>${progress}%</span>
        </div>
        <dl class="task-facts">
          <div><dt>${partial ? '未交付（不再安排）' : task.status === 'done' ? '实际用时' : '预计还需'}</dt><dd>${partial ? `${(100 - progress).toFixed(2).replace(/\.00$/, '')}%` : task.status === 'done' ? (task.actual_hours == null ? '未记录' : `${Number(task.actual_hours)}<small>小时</small>`) : `${Number(task.remaining_hours ?? task.estimated_hours)}<small>小时</small>`}</dd></div>
          <div><dt>截止日期</dt><dd class="fact-date">${escapeHtml(task.due_date)}</dd></div>
          <div><dt>${task.status === 'done' ? '实际结束' : '截至该日的组合余量'}</dt><dd class="${task.status !== 'done' && slack < 0 ? 'negative' : ''} ${task.status === 'done' ? 'fact-date' : ''}">${task.status === 'done' ? escapeHtml(task.completed_at?.slice(0, 10) || '未记录') : `${slack >= 0 ? '+' : ''}${slack}<small>小时</small>`}</dd></div>
        </dl>
        ${task.status !== 'done' ? `<div class="task-context"><span class="delivery-floor">${task.must_complete ? '必保：完整交付' : task.minimum_delivery_percent != null ? `最低交付：${Number(task.minimum_delivery_percent)}%` : '核心交付：待确认'}</span><span>组合日均需 ${task.required_daily_hours ?? '—'}h</span></div>` : ''}
        <div class="forecast-row">
          ${task.status !== 'done' ? `<span class="confidence ${task.prediction_confidence || 'low'}" title="${escapeHtml(task.confidence_reason || '')}">${confidenceLabel(task.prediction_confidence)}</span>` : ''}
          <span class="schedule ${shortfall > 0 ? 'late' : ''}">${shortfall > 0 ? `截止前缺 ${shortfall}h` : finish}</span>
        </div>
        <p class="explanation">${escapeHtml(task.explanation)}</p>
        ${partial && task.closure_note ? `<p class="schedule-note">交付说明：${escapeHtml(task.closure_note)}</p>` : ''}
        ${task.prediction_warning ? `<p class="prediction-warning">⚠ ${escapeHtml(task.prediction_warning)}</p>` : ''}
        ${task.status !== 'done' ? `<p class="schedule-note">${escapeHtml(task.schedule_note || '')}</p>` : ''}
        ${renderHistoryEvidence(task)}
        <div class="task-actions">
          ${task.status !== 'done' ? `<div class="task-primary-actions"><button class="progress-button" data-id="${task.id}">更新进度</button><button class="ghost complete" data-id="${task.id}">完整完成</button></div><div class="task-secondary-actions"><button class="ghost delivery-constraints" data-id="${task.id}">交付约束</button><button class="ghost partial-close" data-id="${task.id}">部分交付后结束</button></div>` : ''}
          <button class="danger delete" data-id="${task.id}">删除</button>
        </div>
      </article>`;
  }).join('');
  document.querySelectorAll('.delete').forEach((button) => button.addEventListener('click', () => removeTask(button.dataset.id)));
  document.querySelectorAll('.complete').forEach((button) => button.addEventListener('click', () => markDone(button.dataset.id)));
  document.querySelectorAll('.partial-close').forEach((button) => button.addEventListener('click', () => markPartial(button.dataset.id)));
  document.querySelectorAll('.progress-button').forEach((button) => button.addEventListener('click', () => updateProgress(button.dataset.id)));
  document.querySelectorAll('.delivery-constraints').forEach((button) => button.addEventListener('click', () => editDeliveryConstraints(button.dataset.id)));
  document.querySelectorAll('.view-history').forEach((button) => button.addEventListener('click', () => showHistoryTask(button.dataset.historyId)));
}

async function loadDashboard() {
  try {
    const data = await api('/api/dashboard');
    allTasks = data.tasks || [];
    analysisDate = data.today;
    renderMetrics(data);
    renderAttention();
    renderActions(data.next_actions || []);
    renderTradeoffs(data.tradeoff_plan);
    renderTasks();
    await refreshReportStaleness();
    if (taskSearch) await searchCurrentTasks();
  } catch (error) {
    $('#task-list').innerHTML = `<div class="empty"><strong>加载失败</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderBatchSummary(data) {
  const highest = data.highest_risk_task;
  const risk = data.risk_counts || {};
  $('#batch-summary').innerHTML = `<strong>计算完成</strong><span>共 ${data.processed_count} 项，完整完成 ${data.completed_count} 项，部分交付结束 ${data.partial_count || 0} 项，剩余 ${data.remaining_hours}h；高 / 中 / 低风险：${risk.high || 0} / ${risk.medium || 0} / ${risk.low || 0}。${data.schedule_missed_count ? `有 ${data.schedule_missed_count} 项按当前容量无法按期排完。` : 'EDF 排程未发现截止缺口。'}${highest ? `最高风险：${escapeHtml(highest.title)}（${highest.risk_score} 分）。` : ''}</span>`;
}

async function processBatch() {
  try {
    const result = await api('/api/tasks/batch/process', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    renderBatchSummary(result);
    await loadDashboard();
  } catch (error) { window.alert(error.message); }
}

async function loadSettings() {
  const data = await api('/api/settings');
  trackingMode = Boolean(data.tracking_mode);
  Object.entries(data).forEach(([key, value]) => {
    const input = $(`[name="${key}"]`);
    if (!input) return;
    if (input.type === 'checkbox') input.checked = Boolean(value);
    else input.value = value;
  });
}

function renderInsights(data) {
  const ratio = data.median_estimation_ratio == null ? '—' : `${data.median_estimation_ratio}×`;
  const onTime = data.on_time_rate == null ? '—' : `${Math.round(data.on_time_rate * 100)}%`;
  const effect = data.deadline_effect == null ? '数据不足' : `${data.deadline_effect}×`;
  $('#insight-metrics').innerHTML = [
    ['完整完成样本', data.sample_count, `历史 ${data.historical_count} / 实时 ${data.live_count}`],
    ['按时完成率', onTime, '提前或按时'],
    ['实际/预计用时', ratio, '中位数'],
    ['临近截止效率', effect, '相对提前完成'],
  ].map(([label, value, note]) => `<div class="insight-card"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  const current = Math.round(data.current_buffer_ratio * 100);
  const suggested = Math.round(data.suggested_buffer_ratio * 100);
  $('#insight-note').innerHTML = `<strong>缓冲建议：</strong>当前 ${current}%，${data.can_adjust ? `建议尝试 ${suggested}%` : '暂不自动调整'}。${escapeHtml(data.recommendation || '继续积累完成记录后再分析。')}<br>本窗口另有 ${Number(data.partial_count || 0)} 项部分交付后结束，其中 ${Number(data.partial_late_count || 0)} 项逾期交付；不计入完整完成按时率或完整工时校准。`;
}

async function loadInsights() {
  try { renderInsights(await api('/api/insights')); } catch (error) { $('#insight-note').textContent = error.message; }
}

async function removeTask(id) {
  if (!window.confirm('确定删除这条任务记录吗？')) return;
  try { await api(`/api/tasks/${id}`, { method: 'DELETE' }); await loadDashboard(); } catch (error) { window.alert(error.message); }
}

function updateProgress(id) {
  if ($('#progress-form [type="submit"]').disabled) return;
  const task = allTasks.find((item) => String(item.id) === String(id));
  if (!task) return;
  progressTaskId = task.id;
  $('#progress-task-name').textContent = task.title;
  $('#progress-spent').value = task.spent_hours || 0;
  $('#progress-percent').value = task.progress_percent || 0;
  $('#progress-message').textContent = '';
  $('#progress-dialog').showModal();
  $('#progress-spent').select();
}

$('#progress-cancel').addEventListener('click', () => $('#progress-dialog').close());
$('#progress-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const spentHours = Number($('#progress-spent').value);
  const progressPercent = Number($('#progress-percent').value);
  if (!Number.isFinite(spentHours) || spentHours < 0 || !Number.isFinite(progressPercent) || progressPercent < 0 || progressPercent >= 100) {
    $('#progress-message').textContent = '累计工时不能为负，进度须大于等于0且小于100%。';
    return;
  }
  const submit = event.target.querySelector('[type="submit"]');
  if (submit.disabled) return;
  submit.disabled = true;
  submit.textContent = '正在保存…';
  try {
    await api(`/api/tasks/${progressTaskId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ spent_hours: spentHours, progress_percent: progressPercent }) });
    $('#progress-dialog').close();
    $('#task-feedback').textContent = '进度已保存。';
    await loadDashboard();
  } catch (error) { $('#progress-message').textContent = error.message; }
  finally { submit.disabled = false; submit.textContent = '保存并重新分析'; }
});

async function markDone(id) {
  const task = allTasks.find((item) => String(item.id) === String(id));
  if (!task) return;
  let actualHours = null;
  if (trackingMode) {
    const entered = window.prompt(`完成“${task.title}”实际一共用了多少小时？`, String(task.spent_hours || ''));
    if (entered === null) return;
    actualHours = Number(entered);
    if (!Number.isFinite(actualHours) || actualHours <= 0) { window.alert('请输入正数工时'); return; }
  }
  try {
    await api(`/api/tasks/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'done', actual_hours: actualHours }) });
    await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) { window.alert(error.message); }
}

async function markPartial(id) {
  const task = allTasks.find((item) => String(item.id) === String(id));
  if (!task) return;
  const entered = window.prompt(`“${task.title}”实际交付了多少百分比？大于0且小于100，最多两位小数。`, String(task.progress_percent || 50));
  if (entered === null) return;
  const progress = Number(entered);
  if (!Number.isFinite(progress) || progress <= 0 || progress >= 100) { window.alert('请填写大于0且小于100的实际交付比例'); return; }
  if (Math.abs(progress * 100 - Math.round(progress * 100)) > 1e-7) { window.alert('交付比例最多保留两位小数'); return; }
  let actual = null;
  if (trackingMode) {
    const hours = window.prompt('本次部分交付实际累计投入多少小时？不会用它冒充完整完成的工时。', String(task.spent_hours || ''));
    if (hours === null) return;
    actual = Number(hours);
    if (!Number.isFinite(actual) || actual <= 0) { window.alert('请填写正数实际工时'); return; }
  }
  const note = window.prompt('交付说明（可留空，例如：已交核心章节，剩余内容本次不交）', '');
  if (note === null) return;
  if (note.length > 500) { window.alert('交付说明不能超过500字'); return; }
  if (!window.confirm(`确认以${progress}%交付后结束？未交付部分不再排程，不计作完整完成。如需之后继续，应另建后续任务。`)) return;
  try {
    await api(`/api/tasks/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'done', completion_scope: 'partial', progress_percent: progress, actual_hours: actual, closure_note: note }) });
    await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) { window.alert(error.message); }
}

function deliveryPayload(form) {
  return {must_complete:form.elements.must_complete.checked,
    minimum_delivery_percent:form.elements.minimum_delivery_percent.value === '' ? null : Number(form.elements.minimum_delivery_percent.value)};
}

function editDeliveryConstraints(id) {
  const task = allTasks.find((item) => String(item.id) === String(id));
  if (!task || task.status === 'done') return;
  deliveryTaskId = task.id;
  const form = $('#delivery-form');
  form.elements.must_complete.checked = Boolean(task.must_complete);
  form.elements.minimum_delivery_percent.value = task.minimum_delivery_percent ?? '';
  $('#delivery-task-name').textContent = task.title;
  $('#delivery-message').textContent = '';
  $('#delivery-dialog').showModal();
}

$('#delivery-cancel').addEventListener('click', () => $('#delivery-dialog').close());
$('#delivery-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    await api(`/api/tasks/${deliveryTaskId}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(deliveryPayload(event.target))});
    $('#delivery-dialog').close();
    await loadDashboard();
  } catch (error) { $('#delivery-message').textContent = error.message; }
  finally { button.disabled = false; }
});

$('#task-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.estimated_hours = Number(payload.estimated_hours);
  payload.priority = Number(payload.priority);
  Object.assign(payload, deliveryPayload(event.target));
  const message = $('#form-message');
  try {
    await api('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    event.target.reset(); message.textContent = '已加入风险分析。'; await loadDashboard();
  } catch (error) { message.textContent = error.message; }
});

$('#settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.weekday_hours = Number(payload.weekday_hours); payload.weekend_hours = Number(payload.weekend_hours); payload.buffer_ratio = Number(payload.buffer_ratio);
  payload.tracking_window_days = Number(payload.tracking_window_days); payload.tracking_mode = event.target.tracking_mode.checked;
  payload.max_extra_daily_hours = Number(payload.max_extra_daily_hours);
  try {
    await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    trackingMode = payload.tracking_mode; await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) { window.alert(error.message); }
});

$('#ongoing-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.estimated_hours = Number(payload.estimated_hours); payload.spent_hours = Number(payload.spent_hours);
  payload.progress_percent = Number(payload.progress_percent); payload.priority = Number(payload.priority);
  Object.assign(payload, deliveryPayload(event.target));
  const message = $('#ongoing-message');
  try {
    await api('/api/tasks/ongoing', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    event.target.reset(); message.textContent = '进行中任务已加入风险分析。'; await loadDashboard();
  } catch (error) { message.textContent = error.message; }
});

async function importCsv(inputSelector, endpoint) {
  const file = $(inputSelector).files[0];
  if (!file) { window.alert('请先选择 CSV 文件'); return; }
  if (file.size > 1024 * 1024) { window.alert('CSV 文件不能超过 1 MiB，请拆分后再导入。'); return; }
  const showResult = (result) => {
    const failures = (result.errors || []).slice(0, 5).map((item) => `第 ${item.line} 行：${item.error}`).join('\n');
    window.alert(`成功导入 ${(result.created || []).length} 条，失败 ${(result.errors || []).length} 条${failures ? '\n' + failures : ''}${(result.errors || []).length > 5 ? '\n仅展示前 5 条错误，请修正后仅重试失败行。' : ''}`);
  };
  try {
    const payload = { csv: await file.text() };
    const submit = () => api(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    let result;
    try { result = await submit(); }
    catch (error) {
      if (error.status !== 409) throw error;
      if (!window.confirm(error.message)) return;
      payload.confirm_duplicate = true;
      result = await submit();
    }
    showResult(result);
    await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) {
    if (error.data?.errors) showResult(error.data);
    else window.alert(error.message);
  }
}

$('#history-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.estimated_hours = Number(payload.estimated_hours); payload.actual_hours = Number(payload.actual_hours); payload.priority = 2;
  const message = $('#history-message');
  try {
    await api('/api/tasks/history', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    event.target.reset(); message.textContent = '历史任务已保存，将参与后续分析。'; await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) { message.textContent = error.message; }
});

document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => {
  selectTaskFilter(button.dataset.filter);
}));
document.querySelectorAll('[data-open-panel]').forEach((button) => button.addEventListener('click', () => {
  const panel = document.getElementById(button.dataset.openPanel);
  panel.open = true;
  panel.scrollIntoView({behavior:'smooth', block:'start'});
  panel.querySelector('summary').focus({preventScroll:true});
}));
$('#task-search').addEventListener('input', (event) => {
  taskSearch = event.target.value.trim().toLowerCase(); searchResults = null;
  taskPage = 1;
  searchVersion += 1; clearTimeout(searchTimer); renderTasks();
  searchTimer = setTimeout(searchCurrentTasks, 250);
});
$('#import-ongoing').addEventListener('click', () => importCsv('#ongoing-file', '/api/tasks/ongoing/import'));
$('#import-history').addEventListener('click', () => importCsv('#history-file', '/api/tasks/import'));
$('#refresh').addEventListener('click', () => loadDashboard().catch((error) => window.alert(error.message)));
$('#refresh-insights').addEventListener('click', () => loadInsights().catch((error) => window.alert(error.message)));
$('#batch-process').addEventListener('click', processBatch);

function reportMessage(text, tone = '') {
  $('#report-status').textContent = text;
  $('#report-status').className = tone;
}

function setReportBusy(busy) {
  reportRunning = busy;
  ['#report-local', '#report-copy'].forEach((selector) => { $(selector).disabled = busy || !reportCapabilities; });
  $('#report-codex').disabled = busy || !reportCapabilities?.codex_available;
}

async function loadReportCapabilities() {
  try {
    reportCapabilities = await api('/api/reports/context');
    $('#report-capability').textContent = reportCapabilities.codex_available
      ? '已检测到本机 Codex CLI，请确认已登录。它会独立生成，不直接续写当前聊天；确认后发送任务快照并使用登录额度。报告仅保留在内存，服务重启后需重新生成。'
      : '未检测到 Codex CLI；仍可生成本地简报，或复制材料到当前 Codex 对话。报告重启后需重新生成。';
  } catch (error) {
    reportCapabilities = null;
    $('#report-capability').textContent = `报告入口不可用：${error.message}`;
  }
  setReportBusy(false);
  if (reportCapabilities) await restoreReport();
}

function rememberReport(id) {
  try { sessionStorage.setItem('radar-report-job', id); } catch { /* Storage may be disabled. */ }
}

async function waitReport(job) {
  const deadline = Date.now() + 210000;
  while (job.status === 'running') {
    if (Date.now() > deadline) throw new Error('等待超时，请稍后刷新查看；也可生成本地简报。');
    await new Promise((resolve) => setTimeout(resolve, 1500));
    job = await api(`/api/reports/${encodeURIComponent(job.id)}`);
  }
  if (job.status !== 'completed') throw new Error(job.error?.message || '生成失败，请改用本地简报。');
  return api(`/api/reports/${encodeURIComponent(job.id)}`);
}

async function restoreReport() {
  let id;
  try { id = sessionStorage.getItem('radar-report-job'); } catch { return; }
  if (!id || !/^[a-f0-9]{32}$/.test(id)) return;
  setReportBusy(true);
  try {
    const job = await api(`/api/reports/${id}`);
    if (job.status === 'running') reportMessage('已恢复尚在生成的报告，正在等待Codex完成…');
    showReport(await waitReport(job));
  } catch (error) {
    try { sessionStorage.removeItem('radar-report-job'); } catch { /* Optional persistence. */ }
    reportMessage(`先前报告无法恢复：${error.message}`, 'report-error');
  } finally { setReportBusy(false); }
}

function showReport(job) {
  currentReportJob = job;
  $('#report-preview').hidden = false;
  // AI and task text are displayed literally, never injected as HTML.
  $('#report-body').textContent = job.report.markdown.replace(/^#{1,3}\s/gm, '');
  $('#report-exports').hidden = false;
  for (const format of ['txt', 'md', 'json']) {
    $(`#report-download-${format}`).href = `/api/reports/${encodeURIComponent(job.id)}?format=${format}`;
  }
  $('#report-print').href = `/api/reports/${encodeURIComponent(job.id)}?format=html`;
  const source = job.provider === 'codex' ? 'Codex解读简报' : '本地规则简报（未调用AI）';
  reportMessage(job.stale ? '任务、设置或日期已变化。这是一份旧快照，请重新生成。'
    : `${source}已生成，基准日期 ${job.report.context.as_of_date}；可导出或打开打印版。`, job.stale ? 'report-stale' : '');
}

async function refreshReportStaleness() {
  if (!currentReportJob || reportRunning) return;
  try {
    const job = await api(`/api/reports/${encodeURIComponent(currentReportJob.id)}`);
    if (job.status === 'completed') showReport(job);
  } catch (error) {
    $('#report-exports').hidden = true;
    reportMessage(`旧简报无法重新核对：${error.message}，请重新生成。`, 'report-error');
  }
}

async function generateReport(provider, copyForChat = false) {
  if (reportRunning) return;
  $('#report-panel').open = true;
  if (!reportCapabilities) { await loadReportCapabilities(); if (!reportCapabilities) return; }
  if (provider === 'codex' && !window.confirm(
    '将本次分析摘要和最早的最多20条当前任务（名称、课程、期限、重要度、投入与进度）发送给本机已登录的Codex生成建议，可能使用账户额度。不会发送账号凭据，也不会修改任务。是否继续？')) return;
  setReportBusy(true);
  currentReportJob = null;
  $('#report-preview').hidden = true;
  $('#report-exports').hidden = true;
  $('#report-prompt-label').hidden = true;
  reportMessage(provider === 'codex' ? 'Codex正在生成建议，通常需要一段时间；你仍可查看任务。' : '正在汇总当前任务与时间设置…');
  try {
    let job = await api('/api/reports', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Radar-Report-Token': reportCapabilities.token },
      body: JSON.stringify({ provider, consent: provider === 'codex' }) });
    const prompt = job.prompt;
    rememberReport(job.id);
    job = await waitReport(job);
    showReport(job);
    if (copyForChat) {
      $('#report-prompt').value = prompt;
      $('#report-prompt-label').hidden = false;
      try {
        await navigator.clipboard.writeText(prompt);
        reportMessage('材料已复制，请粘贴到当前Codex对话。这次只生成了本地简报，尚未发送给AI。');
      } catch {
        $('#report-prompt').focus(); $('#report-prompt').select();
        reportMessage('浏览器未允许自动复制；请复制下方已选中的材料，再粘贴到当前Codex对话。');
      }
    }
  } catch (error) {
    reportMessage(`未生成成功：${error.message} 未修改任务数据，可重试或选择本地简报。`, 'report-error');
  } finally { setReportBusy(false); }
}

$('#report-local').addEventListener('click', () => generateReport('local'));
$('#report-codex').addEventListener('click', () => generateReport('codex'));
$('#report-copy').addEventListener('click', () => generateReport('local', true));
$('#report-panel').addEventListener('toggle', () => {
  if ($('#report-panel').open && !reportRunning) loadReportCapabilities();
});
loadSettings().then(() => Promise.all([loadDashboard(), loadInsights()])).catch((error) => { $('#task-list').textContent = error.message; });
