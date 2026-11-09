import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app import main


class TradeoffApiTests(unittest.TestCase):
    def test_existing_settings_gain_extra_limit_without_losing_capacity(self):
        with tempfile.TemporaryDirectory(prefix="tradeoff-migration-") as temporary_dir:
            database_path = Path(temporary_dir) / "tasks.sqlite3"
            connection = sqlite3.connect(database_path)
            connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, weekday_hours REAL, weekend_hours REAL, buffer_ratio REAL)")
            connection.execute("INSERT INTO settings VALUES (1, 3, 2, 0.25)")
            connection.commit()
            connection.close()
            with patch.object(main, "DB_PATH", database_path):
                main.init_db()
                settings = main.settings()
                self.assertEqual(settings["weekday_hours"], 3)
                self.assertEqual(settings["buffer_ratio"], 0.25)
                self.assertEqual(settings["max_extra_daily_hours"], 0)

    def test_dashboard_and_batch_expose_read_only_tradeoff_plan(self):
        with tempfile.TemporaryDirectory(prefix="tradeoff-api-") as temporary_dir:
            database_path = Path(temporary_dir) / "tasks.sqlite3"
            with patch.object(main, "DB_PATH", database_path):
                main.init_db()
                connection = main.db()
                due_date = date.today().isoformat()
                connection.executemany(
                    "INSERT INTO tasks(title, course, due_date, estimated_hours, priority) VALUES (?, ?, ?, ?, ?)",
                    [("关键任务", "软件工程", due_date, 2.0, 3), ("可调整任务", "软件工程", due_date, 2.0, 1)],
                )
                connection.commit()
                before = [tuple(row) for row in connection.execute(
                    "SELECT id, title, due_date, estimated_hours, priority, status FROM tasks ORDER BY id"
                )]
                connection.close()

                server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    base_url = f"http://127.0.0.1:{server.server_port}"
                    with urlopen(f"{base_url}/api/dashboard", timeout=5) as response:
                        dashboard = json.load(response)
                    request = Request(
                        f"{base_url}/api/tasks/batch/process",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=5) as response:
                        batch = json.load(response)
                    with urlopen(f"{base_url}/api/tasks/search?{urlencode({'q': '软件 关键'})}", timeout=5) as response:
                        found = json.load(response)
                    self.assertEqual(found["count"], 1)
                    self.assertEqual(found["tasks"][0]["title"], "关键任务")
                    with urlopen(f"{base_url}/api/settings", timeout=5) as response:
                        settings = json.load(response)
                    self.assertEqual(settings["max_extra_daily_hours"], 0)
                    request = Request(
                        f"{base_url}/api/settings", data=json.dumps({**settings, "max_extra_daily_hours": 8}).encode(),
                        headers={"Content-Type": "application/json"}, method="PUT",
                    )
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(json.load(response)["max_extra_daily_hours"], 8)
                    with urlopen(f"{base_url}/api/dashboard", timeout=5) as response:
                        expanded = json.load(response)
                    self.assertEqual(expanded["tradeoff_plan"]["recommended_strategy"], "extra_effort")
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=5)

                self.assertEqual(dashboard["tradeoff_plan"], batch["tradeoff_plan"])
                self.assertEqual(batch["tradeoff_plan"]["status"], "overloaded")
                self.assertEqual(len(batch["tradeoff_plan"]["strategies"]), 3)
                self.assertEqual(batch["tradeoff_plan"]["suggestions"][0]["title"], "可调整任务")
                connection = main.db()
                after = [tuple(row) for row in connection.execute(
                    "SELECT id, title, due_date, estimated_hours, priority, status FROM tasks ORDER BY id"
                )]
                connection.close()
                self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
