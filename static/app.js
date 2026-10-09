const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
let trackingMode = true;
let allTasks = [];
let taskFilter = 'all';
let taskSearch = '';

function riskLabel(risk) {
  return { high: '高风险', medium: '需关注', low: '时间可控' }[risk] || risk;
}

function confidenceLabel(level) {
  return { high: '预测可信度高', medium: '预测可信度中', low: '预测可信度低' }[level] || '预测可信度待定';
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function renderMetrics(data) {
  $('#metrics').innerHTML = [
    ['全部任务', data.total_tasks, '项', 'neutral'],
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

function filteredTasks() {
  return allTasks.filter((task) => {
    const matchesFilter = taskFilter === 'all'
      || (taskFilter === 'active' && task.status !== 'done')
      || (taskFilter === 'high' && task.status !== 'done' && task.risk === 'high')
      || (taskFilter === 'done' && task.status === 'done');
    const haystack = `${task.title || ''} ${task.course || ''}`.toLowerCase();
    return matchesFilter && haystack.includes(taskSearch);
  });
}

function renderTasks() {
  const tasks = filteredTasks();
  if (!tasks.length) {
    $('#task-list').innerHTML = `<div class="empty"><strong>${allTasks.length ? '没有符合条件的任务' : '还没有任务'}</strong><span>${allTasks.length ? '换一个筛选条件或关键词试试。' : '先添加一个截止日期明确的任务吧。'}</span></div>`;
    return;
  }
  $('#task-list').innerHTML = tasks.map((task) => {
    const progress = task.status === 'done' ? 100 : Math.max(0, Math.min(Number(task.progress_percent || 0), 99));
    const finish = task.projected_finish_date ? `预计 ${escapeHtml(task.projected_finish_date)} 完成` : '暂无法给出完成日';
    const shortfall = Number(task.shortfall_hours || 0);
    const slack = Number(task.slack_hours || 0);
    return `
      <article class="task-card ${task.risk} ${task.status === 'done' ? 'completed' : ''}">
        <div class="task-main">
          <div class="task-title-row">
            ${task.recommended_rank ? `<span class="rank-badge">#${task.recommended_rank}</span>` : ''}
            <div><h3>${escapeHtml(task.title)}</h3><p>${escapeHtml(task.course || '未分类')}${task.is_historical ? ' · 历史记录' : ''}</p></div>
          </div>
          <span class="risk-pill ${task.risk}">${task.status === 'done' ? '已完成' : riskLabel(task.risk)}</span>
        </div>
        <div class="progress-row">
          <div class="progress-track"><span style="width:${progress}%"></span></div>
          <span>${progress}%</span>
        </div>
        <div class="task-meta">
          <span>截止 ${escapeHtml(task.due_date)}</span>
          <span>剩余 ${task.remaining_hours ?? task.estimated_hours}h</span>
          ${task.status !== 'done' ? `<span>每日约需 ${task.required_daily_hours ?? '—'}h</span>` : ''}
          ${task.status !== 'done' ? `<span class="${slack < 0 ? 'negative' : ''}">时间余量 ${slack >= 0 ? '+' : ''}${slack}h</span>` : ''}
        </div>
        <div class="forecast-row">
          <span class="confidence ${task.prediction_confidence || 'low'}" title="${escapeHtml(task.confidence_reason || '')}">${confidenceLabel(task.prediction_confidence)}</span>
          <span class="schedule ${shortfall > 0 ? 'late' : ''}">${shortfall > 0 ? `截止前缺 ${shortfall}h` : finish}</span>
        </div>
        <p class="explanation">${escapeHtml(task.explanation)}</p>
        ${task.prediction_warning ? `<p class="prediction-warning">⚠ ${escapeHtml(task.prediction_warning)}</p>` : ''}
        ${task.status !== 'done' ? `<p class="schedule-note">${escapeHtml(task.schedule_note || '')}</p>` : ''}
        <div class="task-actions">
          ${task.status !== 'done' ? `<button class="ghost progress-button" data-id="${task.id}">更新进度</button><button class="ghost complete" data-id="${task.id}">标记完成</button>` : ''}
          <button class="danger delete" data-id="${task.id}">删除</button>
        </div>
      </article>`;
  }).join('');
  document.querySelectorAll('.delete').forEach((button) => button.addEventListener('click', () => removeTask(button.dataset.id)));
  document.querySelectorAll('.complete').forEach((button) => button.addEventListener('click', () => markDone(button.dataset.id)));
  document.querySelectorAll('.progress-button').forEach((button) => button.addEventListener('click', () => updateProgress(button.dataset.id)));
}

async function loadDashboard() {
  try {
    const data = await api('/api/dashboard');
    allTasks = data.tasks || [];
    renderMetrics(data);
    renderActions(data.next_actions || []);
    renderTasks();
  } catch (error) {
    $('#task-list').innerHTML = `<div class="empty"><strong>加载失败</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderBatchSummary(data) {
  const highest = data.highest_risk_task;
  const risk = data.risk_counts || {};
  $('#batch-summary').innerHTML = `<strong>计算完成</strong><span>共 ${data.processed_count} 项，剩余 ${data.remaining_hours}h；高 / 中 / 低风险：${risk.high || 0} / ${risk.medium || 0} / ${risk.low || 0}。${data.schedule_missed_count ? `有 ${data.schedule_missed_count} 项按当前容量无法按期排完。` : 'EDF 排程未发现截止缺口。'}${highest ? `最高风险：${escapeHtml(highest.title)}（${highest.risk_score} 分）。` : ''}</span>`;
}

async function processBatch() {
  try {
    const result = await api('/api/tasks/batch/process', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    renderBatchSummary(result);
    allTasks = result.tasks || [];
    renderTasks();
    renderActions(result.next_actions || []);
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
    ['统计样本', data.sample_count, `历史 ${data.historical_count} / 实时 ${data.live_count}`],
    ['按时完成率', onTime, '提前或按时'],
    ['实际/预计用时', ratio, '中位数'],
    ['临近截止效率', effect, '相对提前完成'],
  ].map(([label, value, note]) => `<div class="insight-card"><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  const current = Math.round(data.current_buffer_ratio * 100);
  const suggested = Math.round(data.suggested_buffer_ratio * 100);
  $('#insight-note').innerHTML = `<strong>缓冲建议：</strong>当前 ${current}%，${data.can_adjust ? `建议尝试 ${suggested}%` : '暂不自动调整'}。${escapeHtml(data.recommendation || '继续积累完成记录后再分析。')}`;
}

async function loadInsights() {
  try { renderInsights(await api('/api/insights')); } catch (error) { $('#insight-note').textContent = error.message; }
}

async function removeTask(id) {
  if (!window.confirm('确定删除这条任务记录吗？')) return;
  try { await api(`/api/tasks/${id}`, { method: 'DELETE' }); await loadDashboard(); } catch (error) { window.alert(error.message); }
}

async function updateProgress(id) {
  const task = allTasks.find((item) => String(item.id) === String(id));
  if (!task) return;
  const spent = window.prompt(`“${task.title}”目前累计投入多少小时？`, String(task.spent_hours || 0));
  if (spent === null) return;
  const progress = window.prompt('当前完成百分比（0-99）？', String(task.progress_percent || 0));
  if (progress === null) return;
  const spentHours = Number(spent);
  const progressPercent = Number(progress);
  if (!Number.isFinite(spentHours) || spentHours < 0 || !Number.isFinite(progressPercent) || progressPercent < 0 || progressPercent >= 100) {
    window.alert('请输入有效的累计工时和 0-99 之间的进度');
    return;
  }
  try {
    await api(`/api/tasks/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ spent_hours: spentHours, progress_percent: progressPercent }) });
    await loadDashboard();
  } catch (error) { window.alert(error.message); }
}

async function markDone(id) {
  const task = allTasks.find((item) => String(item.id) === String(id));
  if (!task) return;
  let actualHours = Number(task.spent_hours || task.estimated_hours);
  if (trackingMode) {
    const entered = window.prompt(`完成“${task.title}”实际一共用了多少小时？`, String(actualHours));
    if (entered === null) return;
    actualHours = Number(entered);
    if (!Number.isFinite(actualHours) || actualHours <= 0) { window.alert('请输入正数工时'); return; }
  }
  try {
    await api(`/api/tasks/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'done', actual_hours: actualHours }) });
    await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) { window.alert(error.message); }
}

$('#task-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  payload.estimated_hours = Number(payload.estimated_hours);
  payload.priority = Number(payload.priority);
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
  const message = $('#ongoing-message');
  try {
    await api('/api/tasks/ongoing', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    event.target.reset(); message.textContent = '进行中任务已加入风险分析。'; await loadDashboard();
  } catch (error) { message.textContent = error.message; }
});

async function importCsv(inputSelector, endpoint) {
  const file = $(inputSelector).files[0];
  if (!file) { window.alert('请先选择 CSV 文件'); return; }
  try {
    const result = await api(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ csv: await file.text() }) });
    window.alert(`成功导入 ${result.created.length} 条，失败 ${result.errors.length} 条`);
    await Promise.all([loadDashboard(), loadInsights()]);
  } catch (error) { window.alert(error.message); }
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
  document.querySelectorAll('.filter').forEach((item) => item.classList.remove('active'));
  button.classList.add('active'); taskFilter = button.dataset.filter; renderTasks();
}));
$('#task-search').addEventListener('input', (event) => { taskSearch = event.target.value.trim().toLowerCase(); renderTasks(); });
$('#import-ongoing').addEventListener('click', () => importCsv('#ongoing-file', '/api/tasks/ongoing/import'));
$('#import-history').addEventListener('click', () => importCsv('#history-file', '/api/tasks/import'));
$('#refresh').addEventListener('click', loadDashboard);
$('#refresh-insights').addEventListener('click', loadInsights);
$('#batch-process').addEventListener('click', processBatch);
loadSettings().then(() => Promise.all([loadDashboard(), loadInsights()])).catch((error) => { $('#task-list').textContent = error.message; });
