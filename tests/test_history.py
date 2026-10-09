import os
import shutil
import tempfile
import unittest

import app.main as main


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="academic-load-history-")
        self.original_db_path = main.DB_PATH
        main.DB_PATH = os.path.join(self.folder, "history.sqlite3")
        main.init_db()

    def tearDown(self):
        main.DB_PATH = self.original_db_path
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_historical_task_is_classified_and_counted(self):
        task_id = main.insert_historical(
            {
                "title": "历史需求文档",
                "course": "软件工程",
                "due_date": "2099-09-25",
                "started_at": "2099-09-20T19:00",
                "completed_at": "2099-09-23T20:00",
                "estimated_hours": 4,
                "actual_hours": 5,
                "data_precision": "exact",
            }
        )
        self.assertEqual(task_id, 1)
        task = main.task_rows()[0]
        self.assertTrue(task["is_historical"])
        self.assertEqual(task["completion_type"], "early")
        self.assertEqual(main.insights()["historical_count"], 1)


if __name__ == "__main__":
    unittest.main()
