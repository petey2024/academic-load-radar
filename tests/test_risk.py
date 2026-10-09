import unittest
from datetime import date

from app.risk import assess_task, assess_tasks, available_hours_until, predict_remaining_hours, process_task_batch


SETTINGS = {"weekday_hours": 2, "weekend_hours": 1, "buffer_ratio": 0.2}


class RiskTests(unittest.TestCase):
    def test_capacity_respects_buffer(self):
        hours = available_hours_until(date(2026, 9, 21), **SETTINGS, today=date(2026, 9, 18))
        self.assertEqual(hours, 4.8)

    def test_overloaded_task_is_high_risk(self):
        result = assess_task(
            {"title": "report", "due_date": "2026-09-19", "estimated_hours": 10, "priority": 3, "status": "todo"},
            SETTINGS,
            today=date(2026, 9, 18),
        )
        self.assertEqual(result["risk"], "high")
        self.assertGreater(result["risk_score"], 70)

    def test_completed_task_is_low_risk(self):
        result = assess_task(
            {"title": "done", "due_date": "2026-09-18", "estimated_hours": 10, "priority": 3, "status": "done"},
            SETTINGS,
            today=date(2026, 9, 18),
        )
        self.assertEqual(result["risk"], "low")

    def test_cumulative_tasks_share_the_same_capacity(self):
        tasks = [
            {"id": 1, "title": "report A", "due_date": "2026-09-21", "estimated_hours": 3, "priority": 2, "status": "todo"},
            {"id": 2, "title": "report B", "due_date": "2026-09-21", "estimated_hours": 3, "priority": 2, "status": "todo"},
        ]
        results = assess_tasks(tasks, SETTINGS, today=date(2026, 9, 18))
        self.assertTrue(all(result["risk"] == "high" for result in results))
        self.assertEqual(results[0]["cumulative_remaining_hours"], 6.0)

    def test_progress_and_history_predict_remaining_time(self):
        result = predict_remaining_hours(
            {"estimated_hours": 10, "spent_hours": 4, "progress_percent": 40},
            efficiency_ratio=1.2,
        )
        self.assertEqual(result["prediction_method"], "progress_and_history")
        self.assertAlmostEqual(result["remaining_hours"], 7.2)

    def test_prediction_confidence_uses_observed_progress_and_history(self):
        result = predict_remaining_hours(
            {
                "estimated_hours": 10,
                "spent_hours": 6,
                "progress_percent": 60,
                "data_precision": "exact",
            },
            efficiency_ratio=1.1,
            history_samples=5,
        )
        self.assertEqual(result["prediction_confidence"], "high")
        self.assertIn("历史样本充足", result["confidence_reason"])

    def test_inconsistent_progress_lowers_confidence_and_warns(self):
        result = predict_remaining_hours(
            {
                "estimated_hours": 10,
                "spent_hours": 8,
                "progress_percent": 10,
                "data_precision": "exact",
            },
            efficiency_ratio=1.0,
            history_samples=5,
        )
        self.assertEqual(result["prediction_confidence"], "medium")
        self.assertIsNotNone(result["prediction_warning"])
        self.assertIn("不一致", result["confidence_reason"])

    def test_large_portfolio_expands_schedule_horizon(self):
        result = assess_task(
            {
                "id": 1,
                "title": "large portfolio item",
                "due_date": "2026-09-19",
                "estimated_hours": 2000,
                "priority": 3,
                "status": "todo",
            },
            SETTINGS,
            today=date(2026, 9, 18),
        )
        self.assertIsNotNone(result["projected_finish_date"])
        self.assertGreater(result["projected_finish_date"], "2028-09-18")
        self.assertIn("预计", result["schedule_note"])

    def test_edf_schedule_prioritizes_higher_priority_on_same_deadline(self):
        tasks = [
            {"id": 1, "title": "low", "due_date": "2026-09-21", "estimated_hours": 1.4, "priority": 1, "status": "todo"},
            {"id": 2, "title": "high", "due_date": "2026-09-21", "estimated_hours": 1.6, "priority": 3, "status": "todo"},
        ]
        results = assess_tasks(tasks, SETTINGS, today=date(2026, 9, 21))
        by_title = {task["title"]: task for task in results}
        self.assertEqual(by_title["high"]["recommended_rank"], 1)
        self.assertEqual(by_title["high"]["schedule_status"], "on_track")
        self.assertEqual(by_title["low"]["schedule_status"], "late")
        self.assertAlmostEqual(by_title["low"]["shortfall_hours"], 1.4)

    def test_batch_process_returns_portfolio_summary(self):
        result = process_task_batch(
            [
                {"id": 1, "title": "A", "due_date": "2026-09-21", "estimated_hours": 3, "priority": 2, "status": "todo"},
                {"id": 2, "title": "B", "due_date": "2026-09-21", "estimated_hours": 3, "priority": 2, "status": "todo"},
                {"id": 3, "title": "C", "due_date": "2026-09-18", "estimated_hours": 1, "priority": 1, "status": "done", "actual_hours": 1},
            ],
            SETTINGS,
            today=date(2026, 9, 18),
        )
        self.assertEqual(result["processed_count"], 3)
        self.assertEqual(result["active_count"], 2)
        self.assertEqual(result["risk_counts"]["high"], 2)
        self.assertEqual(result["overloaded_count"], 2)
        self.assertEqual(len(result["next_actions"]), 2)
        self.assertIn("scheduling", result["algorithm"])


if __name__ == "__main__":
    unittest.main()
