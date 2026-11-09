import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import main, reports


def example_task(**changes):
    task = dict(id=1, title="数据库实验报告", course="数据库", due_date=date.today().isoformat(),
                estimated_hours=4, spent_hours=1, progress_percent=25, priority=3,
                status="todo", data_precision="exact", actual_hours=None, completed_at=None)
    task.update(changes)
    return task


CONFIG = dict(weekday_hours=2, weekend_hours=4, buffer_ratio=0.2, max_extra_daily_hours=1,
              tracking_window_days=30)
AI = {"summary": "当前期限较紧，应先核对可用时间。",
      "actions": [{"task_id": 1, "advice": "先完成核心实验，再检查报告是否符合提交要求。"}],
      "caution": "需要延期时先与老师协商，不能直接放弃必交任务。"}


class ReportTests(unittest.TestCase):
    def test_explicit_cli_location_wins_and_invalid_location_does_not_fallback(self):
        with patch.dict(os.environ, {"RADAR_CODEX_BIN": "chosen-codex"}), patch.object(
                reports.shutil, "which", return_value="chosen-path") as which:
            self.assertEqual(reports.codex_executable(), "chosen-path")
            which.assert_called_once_with("chosen-codex")
        with patch.dict(os.environ, {"RADAR_CODEX_BIN": "missing-codex"}), patch.object(
                reports.shutil, "which", return_value=None):
            self.assertIsNone(reports.codex_executable())

    @unittest.skipUnless(os.name == "nt", "Windows desktop CLI layout")
    def test_desktop_cli_is_found_without_path_and_newest_install_is_used(self):
        with tempfile.TemporaryDirectory(prefix="codex-detection-") as directory:
            base = Path(directory) / "OpenAI" / "Codex" / "bin"
            for name, timestamp in (("old", 100), ("new", 200)):
                executable = base / name / "codex.exe"
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"detection fixture only")
                os.utime(executable, (timestamp, timestamp))
            with patch.dict(os.environ, {"RADAR_CODEX_BIN": "", "LOCALAPPDATA": directory}), patch.object(
                    reports.shutil, "which", return_value=None):
                self.assertEqual(reports.codex_executable(), str((base / "new" / "codex.exe").resolve()))

    def test_path_cli_wins_over_desktop_fallback(self):
        with patch.dict(os.environ, {"RADAR_CODEX_BIN": ""}), patch.object(
                reports.shutil, "which", return_value="path-codex") as which:
            self.assertEqual(reports.codex_executable(), "path-codex")
            which.assert_called_once_with("codex")

    def test_empty_context_and_local_source(self):
        context = reports.build_context([], CONFIG)
        text = reports.render_markdown(context)
        self.assertIn("目前没有待完成任务", text)
        self.assertIn("未调用AI", text)
        self.assertIsNone(context["history"]["on_time_percent"])

    def test_snapshot_changes_and_stable_input_order(self):
        tasks = [example_task(), example_task(id=2, title="另一项任务")]
        original = reports.snapshot_fingerprint(tasks, CONFIG)
        self.assertEqual(original, reports.snapshot_fingerprint(tasks[::-1], CONFIG))
        tasks[0]["spent_hours"] = 2
        self.assertNotEqual(original, reports.snapshot_fingerprint(tasks, CONFIG))

    def test_all_tasks_analyzed_but_codex_details_bounded(self):
        tasks = [example_task(id=i, estimated_hours=1, spent_hours=0, progress_percent=0) for i in range(1, 23)]
        context = reports.build_context(tasks, CONFIG)
        self.assertEqual(context["summary"]["active_count"], 22)
        self.assertEqual(len(context["tasks"]), 20)
        self.assertEqual(context["omitted_task_count"], 2)

    def test_future_completions_not_counted_as_recent(self):
        now = datetime.now().astimezone()
        future = example_task(status="done", actual_hours=3, progress_percent=100,
                              completed_at=(now+timedelta(days=1)).isoformat(), completion_type="early")
        self.assertEqual(reports.build_context([future], CONFIG, now)["history"]["recent_completed"], 0)

    def test_ai_unknown_task_and_extra_numbers_rejected(self):
        context = reports.build_context([example_task()], CONFIG)
        self.assertEqual(reports.validate_ai(AI, context), AI)
        with self.assertRaises(reports.ReportError):
            reports.validate_ai({**AI, "actions": [{"task_id": 99, "advice": "无效引用"}]}, context)
        with self.assertRaises(reports.ReportError):
            reports.validate_ai({**AI, "summary": "每天增加99小时。"}, context)

    def test_html_escapes_task_text_and_marks_old_snapshot(self):
        context = reports.build_context([example_task(title="<script>alert('x')</script>")], CONFIG)
        report = {"context": context, "markdown": reports.render_markdown(context), "stale": True}
        body, mime = reports.export_report(report, "html")
        self.assertIn("text/html", mime)
        self.assertNotIn(b"<script>", body)
        self.assertIn(b"&lt;script&gt;", body)
        self.assertIn("旧快照".encode(), body)

    def test_codex_requires_consent_and_failure_is_not_ai_success(self):
        context = reports.build_context([example_task()], CONFIG)
        store = reports.ReportStore(runner=lambda _: (_ for _ in ()).throw(reports.ReportError("CODEX_TIMEOUT", "超时", 504)))
        with self.assertRaises(reports.ReportError):
            store.create(context, "codex")
        with patch.object(reports, "codex_executable", return_value="codex"):
            job = store.create(context, "codex", True)
            job = self.wait_job(store, job["id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "CODEX_TIMEOUT")
        self.assertIsNone(job["report"]["ai"])
        self.assertNotIn("Codex简明建议", job["report"]["markdown"])

    def test_codex_success_busy_limit_and_expiry(self):
        context = reports.build_context([example_task()], CONFIG)
        release = threading.Event()
        store = reports.ReportStore(runner=lambda _: (release.wait(2), AI)[1])
        with patch.object(reports, "codex_executable", return_value="codex"):
            job = store.create(context, "codex", True)
            try:
                with self.assertRaises(reports.ReportError) as error:
                    store.create(context, "local")
                self.assertEqual(error.exception.code, "REPORT_BUSY")
            finally:
                release.set()
            job = self.wait_job(store, job["id"])
        self.assertEqual(job["status"], "completed")
        self.assertIn("Codex简明建议", job["report"]["markdown"])
        with patch.object(reports.time, "monotonic", return_value=time.monotonic()+reports.JOB_TTL_SECONDS+1):
            with self.assertRaises(reports.ReportError):
                store.get(job["id"])

    def test_cli_timeout_uses_readonly_isolated_invocation(self):
        context = reports.build_context([example_task()], CONFIG)
        with patch.object(reports, "codex_executable", return_value="codex"), patch.object(
                reports.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 180)) as run:
            with self.assertRaises(reports.ReportError) as failure:
                reports.run_codex(context)
        self.assertEqual(failure.exception.code,"CODEX_TIMEOUT")
        arguments = run.call_args.args[0]
        self.assertIn("read-only",arguments)
        self.assertIn("--ignore-user-config",arguments)
        self.assertIn("--ephemeral",arguments)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertNotIn("danger-full-access",arguments)

    def test_cli_failure_does_not_leak_subprocess_stderr(self):
        context = reports.build_context([example_task()], CONFIG)
        result = subprocess.CompletedProcess("codex",1,stdout="",stderr="private-config-secret")
        with patch.object(reports,"codex_executable",return_value="codex"), patch.object(reports.subprocess,"run",return_value=result):
            with self.assertRaises(reports.ReportError) as failure:
                reports.run_codex(context)
        self.assertEqual(failure.exception.code,"CODEX_FAILED")
        self.assertNotIn("private-config-secret",str(failure.exception))

    @staticmethod
    def wait_job(store, identifier):
        for _ in range(100):
            job = store.get(identifier)
            if job["status"] != "running":
                return job
            time.sleep(0.01)
        raise AssertionError("job did not finish")


class ReportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="report-api-test-")
        self.addCleanup(self.temp.cleanup)
        self.patches = [patch.object(main, "DATA_DIR", Path(self.temp.name)),
                        patch.object(main, "DB_PATH", Path(self.temp.name)/"test.sqlite3"),
                        patch.object(main, "REPORTS", reports.ReportStore(runner=lambda _: AI))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        main.init_db()
        connection = main.db()
        connection.execute("INSERT INTO tasks(title,due_date,estimated_hours) VALUES(?,?,?)", ("数据库实验报告", date.today().isoformat(), 4))
        connection.commit()
        connection.close()
        class QuietHandler(main.Handler):
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = self.request("/api/reports/context")[1]["token"]

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)

    def request(self, path, payload=None, extra_headers=None, method=None):
        headers = {"Content-Type": "application/json", "X-Radar-Report-Token": getattr(self, "token", "")}
        headers.update(extra_headers or {})
        req = Request(self.base+path, headers=headers,
                      data=None if payload is None else json.dumps(payload).encode(), method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            content = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw
            return response.status, content, response.headers

    def test_local_generation_exports_and_no_task_writes(self):
        before = main.raw_task_rows()
        status, job, _ = self.request("/api/reports", {"provider": "local"})
        self.assertEqual(status, 200)
        self.assertIn("待完成 1 项", job["report"]["markdown"])
        self.assertIn("以下JSON仅为待分析数据", job["prompt"])
        for kind in ("txt", "md", "json", "html", "prompt"):
            status, body, headers = self.request(f"/api/reports/{job['id']}?format={kind}")
            self.assertEqual(status, 200)
            self.assertIn("Content-Disposition", headers)
            self.assertTrue(body)
        self.assertEqual(main.raw_task_rows(), before)

    def test_changed_data_is_marked_stale_in_download(self):
        _, job, _ = self.request("/api/reports", {"provider": "local"})
        connection = main.db()
        connection.execute("UPDATE tasks SET estimated_hours=5 WHERE id=1")
        connection.commit()
        connection.close()
        status, fresh_job, _ = self.request(f"/api/reports/{job['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(fresh_job["stale"])
        _, text, _ = self.request(f"/api/reports/{job['id']}?format=txt")
        self.assertIn("旧快照".encode(), text)

    def test_missing_token_cross_origin_bad_host_and_public_bind_rejected(self):
        for headers in ({"X-Radar-Report-Token": ""}, {"Origin": "https://evil.invalid"},
                        {"Host": f"evil.invalid:{self.server.server_port}"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/reports", {"provider": "local"}, headers)[0], 403)
        self.server.server_address = ("0.0.0.0", self.server.server_port)
        self.assertEqual(self.request("/api/reports/context")[0], 403)

    def test_codex_failure_and_input_validation(self):
        self.assertEqual(self.request("/api/reports", {"provider": "codex"})[0], 422)
        self.assertEqual(self.request("/api/reports", {"provider": "unknown"})[0], 422)
        self.assertEqual(self.request("/api/reports", {"provider": "local", "command": "ignored"})[0], 422)
        self.assertEqual(self.request("/api/reports/not-a-report")[0], 404)
        _, job, _ = self.request("/api/reports", {"provider": "local"})
        self.assertEqual(self.request(f"/api/reports/{job['id']}?format=pdf")[0], 400)

    def test_partial_closure_http_is_reported_separately(self):
        status, result, _ = self.request('/api/tasks/1', {'status':'done','completion_scope':'partial',
            'progress_percent':70,'actual_hours':2,'closure_note':'本次只交核心章节'}, method='PUT')
        self.assertEqual(status,200)
        self.assertEqual(result['unfulfilled_percent'],30)
        _, dashboard, _ = self.request('/api/dashboard')
        self.assertEqual(dashboard['completed_tasks'],0)
        self.assertEqual(dashboard['partial_tasks'],1)
        _, job, _ = self.request('/api/reports', {'provider':'local'})
        self.assertEqual(job['report']['context']['summary']['partial_count'],1)
        self.assertEqual(self.request('/api/tasks/1', {'status':'done','actual_hours':3},method='PUT')[0],409)


if __name__ == "__main__":
    unittest.main()
