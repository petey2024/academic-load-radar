import os
import shutil
import tempfile
import unittest

import app.main as main


class OngoingTaskTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="academic-load-ongoing-")
        self.original_db_path = main.DB_PATH
        main.DB_PATH = os.path.join(self.folder, "ongoing.sqlite3")
        main.init_db()

    def tearDown(self):
        main.DB_PATH = self.original_db_path
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_ongoing_task_uses_remaining_workload(self):
        task_id = main.insert_ongoing(
            {
                "title": "进行中的项目原型",
                "course": "软件工程",
                "due_date": "2099-09-30",
                "started_at": "2099-09-20T19:00",
                "estimated_hours": 10,
                "spent_hours": 4,
                "progress_percent": 40,
                "priority": 3,
                "data_precision": "exact",
            }
        )
        self.assertEqual(task_id, 1)
        task = main.task_rows()[0]
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["data_source"], "ongoing_import")
        self.assertEqual(task["remaining_hours"], 6.0)
        self.assertEqual(main.insights()["sample_count"], 0)

    def test_progress_update_changes_remaining_time_prediction(self):
        task_id = main.insert_ongoing(
            {
                "title": "迭代原型",
                "course": "软件工程",
                "due_date": "2099-09-30",
                "estimated_hours": 10,
                "spent_hours": 2,
                "progress_percent": 20,
                "priority": 2,
                "data_precision": "exact",
            }
        )
        updated = main.update_task_progress(task_id, {"spent_hours": 6, "progress_percent": 75})
        task = main.task_rows()[0]
        self.assertEqual(updated["progress_percent"], 75)
        self.assertEqual(task["spent_hours"], 6)
        self.assertEqual(task["data_source"], "live_progress")
        self.assertLess(task["remaining_hours"], 4)
        self.assertEqual(task["prediction_confidence"], "high")

if __name__ == "__main__":
    unittest.main()
