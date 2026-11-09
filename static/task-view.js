/* Presentation-only helpers. No changes to workload or scheduling algorithms. */
const RadarTaskView = (() => {
  function dayDelta(dueDate, baseDate) {
    const parse = (value) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return NaN;
      const [year, month, day] = value.split('-').map(Number);
      const stamp = Date.UTC(year, month - 1, day);
      const check = new Date(stamp);
      return check.getUTCFullYear() === year && check.getUTCMonth() === month - 1 && check.getUTCDate() === day ? stamp : NaN;
    };
    return Math.round((parse(dueDate) - parse(baseDate)) / 86400000);
  }

  function urgency(task, baseDate) {
    if (task.status === 'done') return null;
    const days = dayDelta(task.due_date, baseDate);
    if (days < 0) return {kind:'overdue', label:`已逾期 ${-days} 天`, days};
    if (days === 0) return {kind:'today', label:'今天截止', days};
    if (days === 1) return {kind:'soon', label:'明天截止', days};
    if (days === 2) return {kind:'soon', label:'后天截止', days};
    return null;
  }

  function compareTasks(a, b) {
    const closed = Number(a.status === 'done') - Number(b.status === 'done');
    if (closed) return closed;
    if (a.status === 'done' && b.status === 'done') {
      return String(b.completed_at || '').localeCompare(String(a.completed_at || '')) || Number(b.id) - Number(a.id);
    }
    return String(a.due_date).localeCompare(String(b.due_date)) || Number(b.priority) - Number(a.priority) || Number(a.id) - Number(b.id);
  }

  function attention(tasks, baseDate) {
    const active = tasks.filter(task => task.status !== 'done').sort(compareTasks);
    const urgent = active.filter(task => urgency(task, baseDate));
    const highRisk = active.filter(task => task.risk === 'high');
    return {
      active, urgent, highRisk,
      overdue: urgent.filter(task => urgency(task, baseDate).kind === 'overdue').length,
      today: urgent.filter(task => urgency(task, baseDate).kind === 'today').length,
      soon: urgent.filter(task => urgency(task, baseDate).kind === 'soon').length,
      attentionCount: new Set([...urgent, ...highRisk].map(task => task.id)).size,
    };
  }

  function paginate(tasks, requestedPage, pageSize = 6) {
    const size = Number.isInteger(pageSize) && pageSize > 0 ? pageSize : 6;
    const pages = Math.max(1, Math.ceil(tasks.length / size));
    const page = Math.min(pages, Math.max(1, Math.floor(Number(requestedPage) || 1)));
    const start = (page - 1) * size;
    return {items:tasks.slice(start,start+size), page, pages, total:tasks.length, start, end:Math.min(start+size,tasks.length)};
  }
  return {dayDelta, urgency, compareTasks, attention, paginate};
})();
if (typeof module !== 'undefined' && module.exports) module.exports = RadarTaskView;
else window.RadarTaskView = RadarTaskView;
