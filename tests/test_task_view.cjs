const test = require('node:test');
const assert = require('node:assert/strict');
const view = require('../static/task-view.js');
const base = '2026-10-16';
const task = (id, due, risk='low', status='todo', priority=2) => ({id,due_date:due,risk,status,priority});

test('calendar dates handle month/year/leap-day boundaries without elapsed-hour rounding', () => {
  assert.equal(view.dayDelta('2027-01-01','2026-12-31'),1);
  assert.equal(view.dayDelta('2028-03-01','2028-02-28'),2);
  assert(Number.isNaN(view.dayDelta('2026-02-30',base)));
});
test('deadline labels include overdue, today, tomorrow and day after tomorrow', () => {
  assert.equal(view.urgency(task(1,'2026-10-15'),base).label,'已逾期 1 天');
  assert.equal(view.urgency(task(1,base),base).label,'今天截止');
  assert.equal(view.urgency(task(1,'2026-10-17'),base).label,'明天截止');
  assert.equal(view.urgency(task(1,'2026-10-18'),base).label,'后天截止');
  assert.equal(view.urgency(task(1,'2026-10-19'),base),null);
});
test('closed and partly delivered tasks do not enter urgency or high risk queues', () => {
  const model = view.attention([task(1,base,'high','done'), {...task(2,base,'high','done'),completion_scope:'partial'},task(3,base)],base);
  assert.equal(model.urgent.length,1);
  assert.equal(model.highRisk.length,0);
  assert.equal(model.attentionCount,1);
});
test('attention totals count overlapping urgent/high-risk tasks once', () => {
  const model=view.attention([task(1,base,'high'),task(2,'2026-10-25','high'),task(3,'2026-10-17')],base);
  assert.equal(model.attentionCount,3);
  assert.equal(model.today,1);
  assert.equal(model.soon,1);
  assert.equal(model.highRisk.length,2);
});
test('active tasks precede old history and same-day importance breaks ties', () => {
  const values=[task(1,'2026-09-01','low','done'),task(2,base,'high','todo',1),task(3,base,'high','todo',3)];
  assert.deepEqual(values.sort(view.compareTasks).map(item=>item.id),[3,2,1]);
});
test('pagination has consistent counts and clamps after filtering/deletion', () => {
  const tasks=Array.from({length:14},(_,index)=>task(index+1,base));
  assert.deepEqual(view.paginate(tasks,2).items.map(item=>item.id),[7,8,9,10,11,12]);
  assert.equal(view.paginate(tasks,3).end,14);
  assert.equal(view.paginate(tasks.slice(0,2),3).page,1);
  assert.equal(view.paginate([],4).items.length,0);
  assert.equal(view.paginate([],4).page,1);
});
