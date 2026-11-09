import itertools
import random
import unittest
from datetime import date, timedelta

from app.history import HistoryMatcher, search_tasks
from app.risk import assess_tasks, available_hours_until, historical_efficiency, process_task_batch


TODAY = date(2026, 10, 9)
SETTINGS = {"weekday_hours": 8, "weekend_hours": 8, "buffer_ratio": 0.2, "max_extra_daily_hours": 0}


def task(identifier, hours, priority=1, days=0):
    return {"id": identifier, "title": f"task {identifier}", "due_date": (TODAY + timedelta(days=days)).isoformat(),
            "estimated_hours": hours, "priority": priority, "status": "todo"}


class StrategyTests(unittest.TestCase):
    def test_extra_effort_removes_every_deadline_deficit_and_keeps_buffer(self):
        tasks = [task(1, 8, 3), task(2, 9, 2, 1)]
        settings = {**SETTINGS, "max_extra_daily_hours": 4}
        plan = process_task_batch(tasks, settings, today=TODAY)["tradeoff_plan"]
        extra = next(item for item in plan["strategies"] if item["id"] == "extra_effort")
        self.assertTrue(extra["feasible"])
        self.assertEqual(plan["recommended_strategy"], "extra_effort")
        expanded = {**settings, "weekday_hours": 8 + extra["additional_daily_hours"],
                    "weekend_hours": 8 + extra["additional_daily_hours"]}
        for result in assess_tasks(tasks, expanded, today=TODAY):
            self.assertGreaterEqual(result["slack_hours"], 0)
        self.assertEqual(expanded["buffer_ratio"], settings["buffer_ratio"])

    def test_overdue_task_cannot_be_rescued_by_future_extra_hours(self):
        plan = process_task_batch([task(1, 1, 3, -1)], {**SETTINGS, "max_extra_daily_hours": 8}, today=TODAY)["tradeoff_plan"]
        extra = plan["strategies"][0]
        self.assertFalse(extra["feasible"])
        self.assertIsNone(extra["additional_daily_hours"])
        self.assertNotEqual(plan["recommended_strategy"], "extra_effort")

    def test_recommends_more_completions_when_high_priority_retention_is_equal(self):
        tasks = [task(1, 2, 3), task(2, 4, 2)] + [task(index, 2) for index in range(3, 6)]
        plan = process_task_batch(tasks, SETTINGS, today=TODAY)["tradeoff_plan"]
        self.assertEqual(plan["recommended_strategy"], "finish_more")
        by_id = {item["id"]: item for item in plan["strategies"]}
        self.assertEqual(by_id["protect_important"]["full_completion_count"], 2)
        self.assertEqual(by_id["finish_more"]["full_completion_count"], 3)
        self.assertIn(1, [item["task_id"] for item in by_id["finish_more"]["kept_tasks"]])

    def test_recommends_key_tasks_when_completion_count_would_drop_important_task(self):
        tasks = [task(1, 4, 3)] + [task(index, 2) for index in range(2, 5)]
        plan = process_task_batch(tasks, SETTINGS, today=TODAY)["tradeoff_plan"]
        self.assertEqual(plan["recommended_strategy"], "protect_important")

    def test_maximum_completion_count_matches_bruteforce_for_varied_deadlines(self):
        generator = random.Random(20261009)
        settings = {"weekday_hours": 2, "weekend_hours": 1, "buffer_ratio": 0.2}
        for case in range(30):
            tasks = [task(index, generator.choice([0.5, 1, 2, 3, 4]), generator.randint(1, 3), generator.randint(0, 5))
                     for index in range(6)]
            deadlines = sorted({item["due_date"] for item in tasks})
            capacities = {due: available_hours_until(date.fromisoformat(due), **settings, today=TODAY) for due in deadlines}

            def feasible(subset):
                return all(sum(item["estimated_hours"] for item in subset if item["due_date"] <= due) <= capacities[due] + 1e-9
                           for due in deadlines)

            optimum = max(len(subset) for amount in range(7) for subset in itertools.combinations(tasks, amount) if feasible(subset))
            plan = process_task_batch(tasks, settings, today=TODAY)["tradeoff_plan"]
            if plan["status"] == "comfortable":
                self.assertEqual(optimum, 6)
                continue
            counted = next(item for item in plan["strategies"] if item["id"] == "finish_more")
            with self.subTest(case=case):
                self.assertEqual(counted["full_completion_count"], optimum)
                selected = [item for item in tasks if item["id"] in {kept["task_id"] for kept in counted["kept_tasks"]}]
                self.assertTrue(feasible(selected))


class RetrievalTests(unittest.TestCase):
    def test_similar_history_changes_estimate_without_using_rough_or_unrelated_samples(self):
        current = {**task(1, 4, 3, 5), "title": "数据库实验报告三", "course": "数据库"}
        history = [
            {**task(2, 4), "title": "数据库实验报告一", "course": "数据库", "status": "done", "actual_hours": 8},
            {**task(3, 4), "title": "数据库实验报告二", "course": "数据库", "status": "done", "actual_hours": 8},
            {**task(4, 4), "title": "数据库实验报告四", "course": "数据库", "status": "done", "actual_hours": 100, "data_precision": "rough"},
        ] + [{**task(index, 4), "title": f"英语阅读练习{index}", "course": "英语", "status": "done", "actual_hours": 2}
             for index in range(5, 9)]
        result = assess_tasks([current, *history], SETTINGS, today=TODAY)[0]
        self.assertEqual({item["task_id"] for item in result["similar_history"]}, {2, 3})
        self.assertEqual(result["history_calibration"], "similar_blended")
        self.assertGreater(result["historical_efficiency_ratio"], historical_efficiency(history))
        self.assertLess(result["historical_efficiency_ratio"], 2)
        self.assertGreater(result["remaining_hours"], 4 * historical_efficiency(history))

    def test_unrelated_history_falls_back_and_blank_courses_do_not_match(self):
        current = {**task(1, 2), "title": "接口编程"}
        sample = {**task(2, 2), "title": "英语阅读", "status": "done", "actual_hours": 3}
        result = HistoryMatcher([sample]).calibrate(current, 1.5, 1)
        self.assertEqual(result["similar_history"], [])
        self.assertEqual(result["efficiency_ratio"], 1.5)
        self.assertEqual(result["history_calibration"], "global")

    def test_search_combines_chinese_course_and_title_keywords_and_normalizes_english(self):
        tasks = [
            {**task(1, 2), "title": "完成 API 实验报告", "course": "数据库"},
            {**task(2, 2), "title": "阅读练习", "course": "数据库"},
        ]
        self.assertEqual([item["id"] for item in search_tasks(tasks, "数据库 ＡＰＩ 报告")], [1])
        self.assertEqual(search_tasks(tasks, "数据库 作文"), [])
        self.assertEqual(search_tasks(tasks, "' OR 1=1 --"), [])


if __name__ == "__main__":
    unittest.main()
