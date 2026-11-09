import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app import main
from app.history import HistoryMatcher
from app.reports import build_context, render_markdown
from app.risk import historical_efficiency, historical_sample_count, process_task_batch


class PartialDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="partial-delivery-test-")
        self.addCleanup(self.temp.cleanup)
        for item in (patch.object(main, "DATA_DIR", Path(self.temp.name)),
                     patch.object(main, "DB_PATH", Path(self.temp.name)/"test.sqlite3")):
            item.start()
            self.addCleanup(item.stop)
        main.init_db()
        self.task_id = main.insert_ongoing(dict(title="数据库报告",course="数据库",due_date=(date.today()-timedelta(days=1)).isoformat(),
            estimated_hours=5,spent_hours=2,progress_percent=40,priority=3,data_precision="exact",started_at="2026-01-01T10:00:00"))

    def close(self, **changes):
        return main.finish_partial_task(self.task_id, {"progress_percent":70,"actual_hours":3,"closure_note":"只交核心部分",**changes})

    def test_preserves_delivery_ratio_and_original_estimate(self):
        result = self.close()
        row = main.raw_task_rows()[0]
        self.assertEqual(result["unfulfilled_percent"],30)
        self.assertEqual(row["status"],"done")
        self.assertEqual(row["completion_scope"],"partial")
        self.assertEqual(row["progress_percent"],70)
        self.assertEqual(row["estimated_hours"],5)
        self.assertEqual(row["actual_hours"],3)
        self.assertEqual(row["priority"],3)
        self.assertEqual(row["completion_type"],"late")
        self.assertEqual(row["closure_note"],"只交核心部分")

    def test_partial_excluded_from_full_efficiency_and_matching(self):
        self.close()
        tasks = main.raw_task_rows()
        self.assertEqual(historical_sample_count(tasks),0)
        self.assertEqual(historical_efficiency(tasks),1)
        self.assertEqual(len(HistoryMatcher(tasks).samples),0)

    def test_partial_is_closed_not_future_work_or_full_completion(self):
        self.close()
        result = process_task_batch(main.raw_task_rows(),main.settings())
        self.assertEqual(result["active_count"],0)
        self.assertEqual(result["completed_count"],0)
        self.assertEqual(result["partial_count"],1)
        self.assertEqual(result["closed_count"],1)
        self.assertEqual(result["remaining_hours"],0)
        self.assertIn("部分交付",result["tasks"][0]["explanation"])

    def test_partial_separate_from_full_timeliness_denominator(self):
        self.close()
        insight = main.insights()
        self.assertEqual(insight["sample_count"],0)
        self.assertIsNone(insight["on_time_rate"])
        self.assertEqual(insight["partial_count"],1)
        self.assertEqual(insight["partial_late_count"],1)

    def test_report_distinguishes_partial_from_full(self):
        self.close()
        context = build_context(main.raw_task_rows(), main.settings())
        self.assertEqual(context["summary"]["partial_count"],1)
        self.assertEqual(context["history"]["recent_completed"],0)
        self.assertIn("已完整完成 0 项，另有 1 项部分交付后结束",render_markdown(context))

    def test_invalid_progress_and_hours_do_not_change_task(self):
        before = main.raw_task_rows()
        for progress in (0,100,101,-1,True,float('nan'),float('inf'),99.999):
            with self.subTest(progress=progress),self.assertRaises((ValueError,TypeError)):
                self.close(progress_percent=progress)
        for hours in (0,-1,True,float('nan'),float('inf'),"bad"):
            with self.subTest(hours=hours),self.assertRaises((ValueError,TypeError)):
                self.close(actual_hours=hours)
        self.assertEqual(main.raw_task_rows(),before)

    def test_tracking_off_does_not_invent_actual_hours(self):
        connection = main.db()
        connection.execute("UPDATE settings SET tracking_mode=0 WHERE id=1")
        connection.commit()
        connection.close()
        self.close(actual_hours=None)
        row = main.raw_task_rows()[0]
        self.assertIsNone(row["actual_hours"])
        self.assertEqual(row["spent_hours"],2)

    def test_repeated_closure_cannot_overwrite_original_delivery(self):
        self.close()
        with self.assertRaises(main.ReportError) as error:
            self.close(progress_percent=80)
        self.assertEqual(error.exception.status,409)
        self.assertEqual(main.raw_task_rows()[0]["progress_percent"],70)

    def test_additive_schema_upgrade_preserves_legacy_task(self):
        old_path = Path(self.temp.name)/"old.sqlite3"
        connection = sqlite3.connect(old_path)
        connection.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,course TEXT,due_date TEXT,estimated_hours REAL,priority INTEGER,status TEXT,created_at TEXT)")
        connection.execute("INSERT INTO tasks VALUES(17,'旧记录','','2026-10-01',2,2,'done','2026-09-30 10:00:00')")
        connection.commit()
        connection.close()
        with patch.object(main,"DB_PATH",old_path):
            main.init_db()
            row = main.raw_task_rows()[0]
        self.assertEqual(row["id"],17)
        self.assertEqual(row["title"],"旧记录")
        self.assertEqual(row["completion_scope"],"full")
        self.assertEqual(row["closure_note"],"")
        self.assertEqual(row["must_complete"],0)
        self.assertIsNone(row["minimum_delivery_percent"])


if __name__ == "__main__":
    unittest.main()
