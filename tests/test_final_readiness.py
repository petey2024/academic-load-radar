"""HTTP failure and recovery checks, exclusively on a disposable database."""
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import main


class FinalReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="radar-final-check-")
        self.addCleanup(temporary.cleanup)
        for item in (patch.object(main, "DATA_DIR", Path(temporary.name)),
                     patch.object(main, "DB_PATH", Path(temporary.name) / "tasks.sqlite3"),
                     patch.object(main.Handler, "log_message", lambda *args: None)):
            item.start()
            self.addCleanup(item.stop)
        main.init_db()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        self.task = {"title": "健壮性演示", "due_date": date.today().isoformat(), "estimated_hours": 2}

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)

    def request(self, path, payload=None, method=None, raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        body = raw if raw is not None else None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        try:
            connection.request(method or ("GET" if body is None else "POST"), path, body,
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_malformed_json_types_and_nonfinite_numbers_do_not_write(self):
        for body in (b"[]", b"null", b'"text"', b"{broken", b"\xff",
                     b'{"estimated_hours":NaN}', b'{"estimated_hours":1e999}'):
            with self.subTest(body=body):
                self.assertEqual(self.request("/api/tasks", raw=body)[0], 400)
        for value in ("NaN", "Infinity", "-Infinity", True):
            self.assertEqual(self.request("/api/tasks", {**self.task, "estimated_hours": value})[0], 400)
        self.assertEqual(main.raw_task_rows(), [])
        self.assertEqual(self.request("/api/tasks", self.task)[0], 201)

    def test_settings_and_progress_reject_nonfinite_without_changes(self):
        _, created = self.request("/api/tasks", self.task)
        before_settings, before_tasks = main.settings(), main.raw_task_rows()
        for field in ("weekday_hours", "weekend_hours", "buffer_ratio", "early_threshold_hours"):
            self.assertEqual(self.request("/api/settings", {**before_settings, field: "NaN"}, "PUT")[0], 400)
        self.assertEqual(self.request(f"/api/tasks/{created['id']}", {"spent_hours": "Infinity"}, "PUT")[0], 400)
        self.assertEqual(self.request(f"/api/tasks/{created['id']}",
                                     {"status": "done", "actual_hours": 2, "stress_level": "bad"}, "PUT")[0], 400)
        self.assertEqual(main.settings(), before_settings)
        self.assertEqual(main.raw_task_rows(), before_tasks)

    def test_query_validation_and_static_allowlist(self):
        for window in ("bad", "0", "366", "NaN", ""):
            self.assertEqual(self.request("/api/insights?window=" + window)[0], 400 if window else 200)
        self.assertEqual(self.request("/api/insights?window=30")[0], 200)
        for path in ("/../app/main.py", "/%2e%2e/app/main.py", "/academic_load_radar.sqlite3"):
            self.assertEqual(self.request(path)[0], 404)

    def test_size_boundary_uses_utf8_csv_content_not_json_wrapper(self):
        # A large but valid header-only file; no tasks are written at either boundary.
        content = "title," + "x" * (main.MAX_CONTENT_BYTES - 6)
        status, result = self.request("/api/tasks/ongoing/import", {"csv": content})
        self.assertEqual(status, 400)  # Parsed successfully, but contains no rows.
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.request("/api/tasks/ongoing/import", {"csv": content + "中"})[0], 413)
        self.assertEqual(self.request("/api/tasks", raw=b"", headers={"Content-Length": str(main.MAX_CONTENT_BYTES + 1)})[0], 413)
        self.assertEqual(self.request("/api/tasks", raw=b"", headers={"Content-Length": "-1"})[0], 413)
        self.assertEqual(main.raw_task_rows(), [])

    def test_csv_bom_row_errors_duplicates_and_explicit_retry(self):
        content = "\ufefftitle,course,due_date,estimated_hours,spent_hours,progress_percent,priority\r\n"
        content += f'"实验,报告",软件工程,{date.today().isoformat()},2,1,40,3\r\n'
        content += f"坏记录,软件工程,{date.today().isoformat()},NaN,1,40,3\r\n"
        status, result = self.request("/api/tasks/ongoing/import", {"csv": content})
        self.assertEqual(status, 201)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["errors"][0]["line"], 3)
        self.assertEqual(main.raw_task_rows()[0]["title"], "实验,报告")
        self.assertEqual(self.request("/api/tasks/ongoing/import", {"csv": content.replace("\r\n", "\n")})[0], 409)
        self.assertEqual(len(main.raw_task_rows()), 1)
        self.assertEqual(self.request("/api/tasks/ongoing/import", {"csv": content, "confirm_duplicate": True})[0], 201)
        self.assertEqual(len(main.raw_task_rows()), 2)

    def test_read_only_storage_failure_is_clear_and_recovery_preserves_data(self):
        self.request("/api/tasks", self.task)
        before = main.raw_task_rows()
        def readonly_db():
            connection = sqlite3.connect(main.DB_PATH.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            return connection
        with patch.object(main, "db", readonly_db):
            status, result = self.request("/api/tasks", {**self.task, "title": "不能写入"})
        self.assertEqual(status, 503)
        self.assertIn("存储暂不可用", result["error"])
        self.assertNotIn(str(main.DB_PATH), result["error"])
        self.assertEqual(main.raw_task_rows(), before)
        self.assertEqual(self.request("/api/tasks", {**self.task, "title": "恢复写入"})[0], 201)

    def test_server_restart_preserves_tasks_and_settings(self):
        self.request("/api/tasks", self.task)
        self.request("/api/settings", {**main.settings(), "weekday_hours": 3}, "PUT")
        before = main.raw_task_rows(), main.settings()
        self.stop_server()
        main.init_db()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.assertEqual(self.request("/api/dashboard")[0], 200)
        self.assertEqual((main.raw_task_rows(), main.settings()), before)

    def test_tracking_off_full_completion_does_not_invent_observed_hours(self):
        from app.risk import historical_sample_count
        self.request("/api/settings", {**main.settings(), "tracking_mode": False}, "PUT")
        _, created = self.request("/api/tasks", self.task)
        self.assertEqual(self.request(f"/api/tasks/{created['id']}", {"status": "done"}, "PUT")[0], 200)
        rows = main.raw_task_rows()
        self.assertEqual(rows[0]["progress_percent"], 100)
        self.assertIsNone(rows[0]["actual_hours"])
        self.assertEqual(historical_sample_count(rows), 0)

    def test_buffer_outside_suggestion_band_is_not_silently_changed(self):
        for ratio in (0, 0.8):
            self.request("/api/settings", {**main.settings(), "buffer_ratio": ratio}, "PUT")
            status, result = self.request("/api/insights")
            self.assertEqual(status, 200)
            self.assertEqual(result["suggested_buffer_ratio"], ratio)
            self.assertFalse(result["can_adjust"])

    def test_broken_csv_structure_is_rejected_before_any_row_is_saved(self):
        content = f'title,due_date,estimated_hours,spent_hours,progress_percent\n正常,{date.today().isoformat()},2,1,40\n"未闭合'
        status, result = self.request("/api/tasks/ongoing/import", {"csv": content})
        self.assertEqual(status, 400)
        self.assertIn("尚未写入", result["error"])
        self.assertEqual(main.raw_task_rows(), [])


if __name__ == "__main__":
    unittest.main()
