"""Delivery-aware tradeoffs: independent enumeration, regression and HTTP checks."""
import csv
import itertools
import json
import random
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import main
from app.reports import build_context, render_markdown
from app.risk import (available_hours_until, compare_strategies, minimum_retained_units,
                      process_task_batch, suggest_tradeoffs)

TODAY = date(2026, 10, 16)
SETTINGS = dict(weekday_hours=4, weekend_hours=4, buffer_ratio=0, max_extra_daily_hours=0)


def task(identifier, hours, priority=2, days=0, **changes):
    return dict(id=identifier, title=f"task {identifier}", status="todo", priority=priority,
                due_date=(TODAY+timedelta(days=days)).isoformat(), remaining_hours=hours,
                estimated_hours=hours, prediction_confidence="high", **changes)


def plan_for(tasks, settings=None):
    settings = settings or SETTINGS
    plan = suggest_tradeoffs(tasks, settings, today=TODAY)
    plan.update(compare_strategies(tasks, settings, plan, today=TODAY))
    return plan


class DeliveryPlanningTests(unittest.TestCase):
    def test_report_adjustment_details_do_not_leak_omitted_task_titles(self):
        tasks = [task(i,1,3) for i in range(1,22)]
        tasks.append(task(22,40,1,1))
        tasks[-1]['title']='SECRET-LATE-TASK'
        context=build_context(tasks,{**SETTINGS,'tracking_window_days':30})
        self.assertNotIn('SECRET-LATE-TASK',json.dumps(context))
        self.assertTrue(any(s['omitted_adjustments']>0 for s in context['tradeoffs']['strategies']))

    def test_more_low_adjustments_are_preferred_to_one_high_reduction(self):
        tasks = [task(1, 4, 3)] + [task(i, 1, 1) for i in range(2, 6)]
        plan = plan_for(tasks)
        self.assertEqual(plan["priority_loss_hours"]["high"], 0)
        self.assertEqual({item["task_id"] for item in plan["suggestions"]}, {2, 3, 4, 5})
        self.assertEqual(plan["recommended_strategy"], "protect_important")

    def test_high_task_is_only_reduced_by_unavoidable_shortage(self):
        tasks = [task(1, 6, 3), task(2, 2, 2), task(3, 1, 1)]
        plan = plan_for(tasks)
        self.assertEqual(plan["priority_loss_hours"], {"high": 2, "medium": 2, "low": 1})
        self.assertEqual(plan["high_priority_shortfall_hours"], 2)
        high = next(item for item in plan["suggestions"] if item["task_id"] == 1)
        self.assertEqual(high["retained_hours"], 4)
        self.assertTrue(high["core_unconfirmed"])
        self.assertIn("核心交付尚未确认", high["reason"])

    def test_later_optional_task_cannot_fix_earlier_shortage(self):
        plan = plan_for([task(1, 5, 3), task(2, 10, 1, 5)])
        self.assertEqual(plan["priority_loss_hours"]["high"], 1)
        self.assertEqual(plan["high_priority_shortfalls"][0]["due_date"], TODAY.isoformat())

    def test_full_delivery_never_adjusted_even_when_low_priority(self):
        plan = plan_for([task(1, 3, 1, must_complete=True), task(2, 3, 3)])
        for strategy in plan["strategies"]:
            self.assertNotIn(1, [item["task_id"] for item in strategy["adjusted_tasks"]])
        self.assertEqual(plan["priority_loss_hours"]["high"], 2)

    def test_more_count_is_not_recommended_if_same_high_count_loses_more_high_work(self):
        plan=plan_for([task(1,3,3),task(2,3,3),task(3,0.5,1),task(4,0.5,1)])
        important=next(s for s in plan['strategies'] if s['id']=='protect_important')
        count=next(s for s in plan['strategies'] if s['id']=='finish_more')
        self.assertGreater(count['full_completion_count'],important['full_completion_count'])
        self.assertGreater(count['priority_loss_hours']['high'],important['priority_loss_hours']['high'])
        self.assertEqual(plan['recommended_strategy'],'protect_important')

    def test_core_floor_is_absolute_and_uses_ceiling_not_downward_rounding(self):
        self.assertEqual(minimum_retained_units(task(1, 8, progress_percent=20, minimum_delivery_percent=60)), 400)
        self.assertEqual(minimum_retained_units(task(1, 0.03, progress_percent=0, minimum_delivery_percent=50)), 2)
        self.assertEqual(minimum_retained_units(task(1, 8, progress_percent=80, minimum_delivery_percent=60)), 0)
        self.assertEqual(minimum_retained_units(task(1, 8, must_complete=True, minimum_delivery_percent=30)), 800)

    def test_core_floor_protected_in_both_tradeoff_strategies(self):
        plan = plan_for([task(1, 6, 3, minimum_delivery_percent=50), task(2, 3, 1)])
        for strategy in plan["strategies"][1:]:
            self.assertTrue(strategy["feasible"])
            for item in strategy["adjusted_tasks"]:
                self.assertGreaterEqual(item["retained_hours"], item["minimum_retained_hours"])

    def test_impossible_floors_have_no_fake_feasible_recommendation(self):
        tasks = [task(1, 5, 3, must_complete=True), task(2, 2, 1)]
        plan = plan_for(tasks)
        self.assertFalse(plan["constraints_feasible"])
        self.assertIsNone(plan["recommended_strategy"])
        self.assertTrue(all(not item["feasible"] for item in plan["strategies"]))
        self.assertEqual(plan["unresolved_checkpoints"][0]["shortage_hours"], 1)
        self.assertNotIn(1, [item["task_id"] for item in plan["suggestions"]])
        rescued = plan_for(tasks, {**SETTINGS, "max_extra_daily_hours": 4})
        self.assertEqual(rescued["recommended_strategy"], "extra_effort")
        self.assertTrue(rescued["strategies"][0]["feasible"])

    def test_low_mandatory_conflict_is_not_called_high_priority_shortage(self):
        plan = plan_for([task(1, 5, 1, must_complete=True)])
        self.assertFalse(plan["constraints_feasible"])
        self.assertEqual(plan["high_priority_shortfall_hours"], 0)

    def test_overdue_mandatory_cannot_be_rescued_by_future_hours(self):
        plan = plan_for([task(1, 1, 3, -1, must_complete=True)], {**SETTINGS, "max_extra_daily_hours": 8})
        self.assertIsNone(plan["recommended_strategy"])
        self.assertFalse(plan["strategies"][0]["feasible"])
        self.assertEqual(plan["suggestions"], [])

    def test_fractional_priority_loss_matches_independent_bruteforce(self):
        rng = random.Random(20261016)
        settings = {**SETTINGS, "weekday_hours": 0.04, "weekend_hours": 0.02}
        for case in range(45):
            tasks = [task(i, rng.randint(1, 5)/100, rng.randint(1, 3), rng.randint(0, 2),
                          minimum_delivery_percent=rng.choice([None, 0, 50, 100])) for i in range(4)]
            sizes = [round(t["remaining_hours"]*100) for t in tasks]
            # Independent percentage-to-hour conversion for zero-progress fixtures.
            floors = [0 if t["minimum_delivery_percent"] is None else
                      (size*int(t["minimum_delivery_percent"])+99)//100 for t, size in zip(tasks, sizes)]
            dues = sorted({t["due_date"] for t in tasks})
            caps = {due: round(100*available_hours_until(date.fromisoformat(due), weekday_hours=0.04,
                    weekend_hours=0.02, buffer_ratio=0, today=TODAY)) for due in dues}
            feasible = [r for r in itertools.product(*(range(lo, hi+1) for lo, hi in zip(floors, sizes)))
                        if all(sum(x for x,t in zip(r,tasks) if t["due_date"]<=due)<=caps[due] for due in dues)]
            plan = suggest_tradeoffs(tasks, settings, today=TODAY)
            if plan["status"] == "comfortable":
                continue
            with self.subTest(case=case):
                self.assertEqual(plan["constraints_feasible"], bool(feasible))
                if feasible:
                    score = lambda r: tuple(sum(size-x for size,x,t in zip(sizes,r,tasks) if t["priority"]==p) for p in (3,2,1))
                    expected = min(map(score, feasible))
                    actual = tuple(round(plan["priority_loss_hours"][key]*100) for key in ("high","medium","low"))
                    self.assertEqual(actual, expected)

    def test_maximum_count_with_floors_matches_subset_enumeration(self):
        rng = random.Random(16)
        settings = {**SETTINGS, "weekday_hours": 2, "weekend_hours": 1}
        for case in range(60):
            tasks = [task(i, rng.choice([1,2,3,4]), rng.randint(1,3), rng.randint(0,4),
                          must_complete=rng.choice([False,False,True]), minimum_delivery_percent=rng.choice([None,50])) for i in range(6)]
            floor = [round(t["remaining_hours"]*100) if t["must_complete"] else
                     round(t["remaining_hours"]*50) if t["minimum_delivery_percent"]==50 else 0 for t in tasks]
            dues = sorted({t["due_date"] for t in tasks})
            caps = {d:round(100*available_hours_until(date.fromisoformat(d), weekday_hours=2,weekend_hours=1,buffer_ratio=0,today=TODAY)) for d in dues}
            candidates=[]
            for mask in range(1<<6):
                retained=[round(t["remaining_hours"]*100) if mask&(1<<i) else floor[i] for i,t in enumerate(tasks)]
                if all(sum(x for x,t in zip(retained,tasks) if t["due_date"]<=d)<=caps[d] for d in dues):
                    candidates.append(sum(x==round(t["remaining_hours"]*100) for x,t in zip(retained,tasks)))
            plan=plan_for(tasks,settings)
            if plan["status"]=="comfortable" or not candidates:
                continue
            count=next(s for s in plan["strategies"] if s["id"]=="finish_more")
            with self.subTest(case=case):
                self.assertEqual(count["full_completion_count"],max(candidates))

    def test_csv_regression_reduces_document_loss_and_explains_later_shortfall(self):
        rows=[]
        folder = main.ROOT / 'examples' / 'evaluation'
        for name,status in [('历史任务-评测.csv','done'),('当前任务-评测.csv','todo')]:
            with (folder/name).open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):
                    row.update(id=len(rows)+1,status=status,priority=int(row.get('priority') or 2))
                    for key in ('estimated_hours','actual_hours','spent_hours','progress_percent'):
                        if row.get(key): row[key]=float(row[key])
                    rows.append(row)
        settings={**SETTINGS,'weekday_hours':2,'weekend_hours':4,'buffer_ratio':0.2,'max_extra_daily_hours':1}
        result=process_task_batch(rows,settings,today=TODAY)
        plan=result['tradeoff_plan']
        document=next(item for item in plan['suggestions'] if item['task_id']==25)
        self.assertEqual(document['suggested_reduction_hours'],3.08)
        self.assertEqual(document['retained_hours'],5.12)
        self.assertEqual(plan['priority_loss_hours']['high'],5.01)
        self.assertEqual(plan['high_priority_shortfall_hours'],5.01)


class DeliveryConstraintApiTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='delivery-constraints-')
        self.addCleanup(temporary.cleanup)
        for item in (patch.object(main,'DATA_DIR',Path(temporary.name)),patch.object(main,'DB_PATH',Path(temporary.name)/'tasks.sqlite3')):
            item.start(); self.addCleanup(item.stop)
        main.init_db()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),main.Handler)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True); self.worker.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join(timeout=5)

    def request(self,path,payload=None,method='POST'):
        req=Request(f'http://127.0.0.1:{self.server.server_port}'+path,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type':'application/json'},method=method)
        try: response=urlopen(req,timeout=5)
        except HTTPError as error: response=error
        with response: return response.status,json.load(response)

    def create(self,**changes):
        return self.request('/api/tasks',dict(title='重要报告',due_date=date.today().isoformat(),estimated_hours=5,priority=3,**changes))

    def test_create_update_reload_report_and_read_only_plans(self):
        status,data=self.create(must_complete=True,minimum_delivery_percent=60)
        self.assertEqual(status,201)
        identifier=data['id']
        status,_=self.request(f'/api/tasks/{identifier}',{'must_complete':False,'minimum_delivery_percent':80},'PUT')
        self.assertEqual(status,200)
        before=main.raw_task_rows()
        self.assertEqual(before[0]['minimum_delivery_percent'],80)
        self.assertEqual(before[0]['progress_percent'],0)
        main.init_db(); self.assertEqual(main.raw_task_rows(),before)
        _,dashboard=self.request('/api/dashboard',method='GET')
        self.assertIsNone(dashboard['tradeoff_plan']['recommended_strategy'])
        self.assertEqual(main.raw_task_rows(),before)
        context=build_context(before,main.settings())
        self.assertEqual(context['tasks'][0]['minimum_delivery_percent'],80)
        self.assertIn('底线',render_markdown(context))
        self.assertTrue(context['tradeoffs']['unresolved_checkpoints'])

    def test_bad_constraints_are_rejected_without_partial_writes(self):
        _,data=self.create()
        before=main.raw_task_rows()
        for payload in [{'minimum_delivery_percent':-1},{'minimum_delivery_percent':101},
                        {'minimum_delivery_percent':float('nan')},{'minimum_delivery_percent':True},
                        {'minimum_delivery_percent':50.123},{'must_complete':2},{'must_complete':'yes'},
                        {'must_complete':True,'progress_percent':40}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.request(f"/api/tasks/{data['id']}",payload,'PUT')[0],400)
                self.assertEqual(main.raw_task_rows(),before)
        self.assertEqual(self.request('/api/tasks/99999',{'must_complete':True},'PUT')[0],404)
        self.assertEqual(self.create(minimum_delivery_percent=101)[0],400)

    def test_csv_optional_columns_and_legacy_defaults(self):
        content='title,course,due_date,estimated_hours,spent_hours,progress_percent,priority,must_complete,minimum_delivery_percent\n'
        content+=f'必交报告,软件工程,{date.today().isoformat()},4,1,25,3,true,70\n'
        status,data=self.request('/api/tasks/ongoing/import',{'csv':content})
        self.assertEqual(status,201,data); self.assertEqual(len(data['created']),1)
        row=main.raw_task_rows()[0]
        self.assertEqual(row['must_complete'],1); self.assertEqual(row['minimum_delivery_percent'],70)
        _,data=self.create()
        default=next(t for t in main.raw_task_rows() if t['id']==data['id'])
        self.assertEqual(default['must_complete'],0); self.assertIsNone(default['minimum_delivery_percent'])


if __name__=='__main__':
    unittest.main()
