"""Zero-dependency HTTP API and static web server for the first MVP."""

from __future__ import annotations

import json
import csv
import io
import os
import sqlite3
import statistics
import tempfile
import uuid
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .risk import assess_tasks, process_task_batch

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
# The managed Windows runtime may reject Python writes under a non-ASCII
# workspace path. Keep the default data file in a writable temp directory;
# deployments can set ACADEMIC_LOAD_RADAR_DATA to a persistent directory.
DATA_DIR = Path(os.environ.get("ACADEMIC_LOAD_RADAR_DATA", tempfile.gettempdir()))
DB_PATH = DATA_DIR / "academic_load_radar.sqlite3"
HOST = os.environ.get("HOST", "127.0.0.1")


def configured_port() -> int:
    """Read the platform port while keeping the local default predictable."""
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return port


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = db()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            course TEXT NOT NULL DEFAULT '',
            due_date TEXT NOT NULL,
            estimated_hours REAL NOT NULL CHECK (estimated_hours > 0),
            priority INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
            status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'done')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            completed_at TEXT,
            actual_hours REAL,
            spent_hours REAL NOT NULL DEFAULT 0,
            progress_percent REAL NOT NULL DEFAULT 0,
            completion_type TEXT,
            stress_level INTEGER,
            is_historical INTEGER NOT NULL DEFAULT 0,
            data_source TEXT NOT NULL DEFAULT 'live',
            data_precision TEXT NOT NULL DEFAULT 'exact',
            import_batch_id TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            weekday_hours REAL NOT NULL DEFAULT 2,
            weekend_hours REAL NOT NULL DEFAULT 1,
            buffer_ratio REAL NOT NULL DEFAULT 0.2,
            tracking_mode INTEGER NOT NULL DEFAULT 1,
            tracking_window_days INTEGER NOT NULL DEFAULT 30,
            early_threshold_hours REAL NOT NULL DEFAULT 24
        );
        INSERT OR IGNORE INTO settings(id) VALUES (1);
        """
    )
    # Keep the MVP compatible with databases created before tracking mode was added.
    existing = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
    for name, definition in {
        "started_at": "TEXT",
        "completed_at": "TEXT",
        "actual_hours": "REAL",
        "spent_hours": "REAL NOT NULL DEFAULT 0",
        "progress_percent": "REAL NOT NULL DEFAULT 0",
        "completion_type": "TEXT",
        "stress_level": "INTEGER",
        "is_historical": "INTEGER NOT NULL DEFAULT 0",
        "data_source": "TEXT NOT NULL DEFAULT 'live'",
        "data_precision": "TEXT NOT NULL DEFAULT 'exact'",
        "import_batch_id": "TEXT",
    }.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
    settings_existing = {row[1] for row in connection.execute("PRAGMA table_info(settings)")}
    for name, definition in {
        "tracking_mode": "INTEGER NOT NULL DEFAULT 1",
        "tracking_window_days": "INTEGER NOT NULL DEFAULT 30",
        "early_threshold_hours": "REAL NOT NULL DEFAULT 24",
    }.items():
        if name not in settings_existing:
            connection.execute(f"ALTER TABLE settings ADD COLUMN {name} {definition}")
    connection.commit()
    connection.close()


def settings() -> dict:
    connection = db()
    row = connection.execute(
        "SELECT weekday_hours, weekend_hours, buffer_ratio, tracking_mode, tracking_window_days, early_threshold_hours FROM settings WHERE id=1"
    ).fetchone()
    connection.close()
    result = dict(row)
    result["tracking_mode"] = bool(result["tracking_mode"])
    return result


def raw_task_rows() -> list[dict]:
    connection = db()
    rows = [dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY due_date, priority DESC, id")]
    connection.close()
    return rows


def task_rows() -> list[dict]:
    rows = raw_task_rows()
    config = settings()
    return assess_tasks(rows, config)


def completion_type_for(due_date: str, completed_at: str, threshold_hours: float) -> str:
    completed = datetime.fromisoformat(completed_at.replace("Z", ""))
    days_early = (date.fromisoformat(due_date) - completed.date()).days
    return "early" if days_early * 24 >= threshold_hours else "on_time" if days_early >= 0 else "late"


def insert_historical(payload: dict, *, batch_id: str | None = None) -> int:
    title = str(payload.get("title", "")).strip()
    course = str(payload.get("course", "")).strip()
    due_date = str(payload.get("due_date", ""))
    started_at = str(payload.get("started_at", "")).strip() or None
    completed_at = str(payload.get("completed_at", "")).strip()
    estimated_hours = float(payload.get("estimated_hours", 0))
    actual_hours = float(payload.get("actual_hours", 0))
    data_precision = str(payload.get("data_precision", "exact"))
    stress_level = payload.get("stress_level")
    date.fromisoformat(due_date)
    datetime.fromisoformat(completed_at.replace("Z", ""))
    if started_at:
        datetime.fromisoformat(started_at.replace("Z", ""))
    if not title or estimated_hours <= 0 or actual_hours <= 0 or data_precision not in ("exact", "approx", "rough"):
        raise ValueError("历史任务需要名称、截止日期、完成时间、正数预计/实际工时和有效数据精度")
    if stress_level not in (None, ""):
        stress_level = max(1, min(int(stress_level), 5))
    else:
        stress_level = None
    connection = db()
    cursor = connection.execute(
        """INSERT INTO tasks(
            title, course, due_date, estimated_hours, priority, status,
            started_at, completed_at, actual_hours, completion_type,
            stress_level, is_historical, data_source, data_precision, import_batch_id
        ) VALUES (?, ?, ?, ?, ?, 'done', ?, ?, ?, ?, ?, 1, 'historical', ?, ?)""",
        (
            title,
            course,
            due_date,
            estimated_hours,
            int(payload.get("priority", 2)),
            started_at,
            completed_at,
            actual_hours,
            completion_type_for(due_date, completed_at, float(settings()["early_threshold_hours"])),
            stress_level,
            data_precision,
            batch_id,
        ),
    )
    connection.commit()
    task_id = cursor.lastrowid
    connection.close()
    return task_id


def insert_ongoing(payload: dict, *, batch_id: str | None = None) -> int:
    """Insert a task that has started but is not completed yet."""
    title = str(payload.get("title", "")).strip()
    course = str(payload.get("course", "")).strip()
    due_date = str(payload.get("due_date", ""))
    started_at = str(payload.get("started_at", "")).strip() or None
    estimated_hours = float(payload.get("estimated_hours", 0))
    spent_hours = float(payload.get("spent_hours", 0))
    progress_percent = float(payload.get("progress_percent", 0))
    priority = int(payload.get("priority", 2))
    data_precision = str(payload.get("data_precision", "exact"))
    stress_level = payload.get("stress_level")
    date.fromisoformat(due_date)
    if started_at:
        datetime.fromisoformat(started_at.replace("Z", ""))
    if (
        not title
        or estimated_hours <= 0
        or spent_hours < 0
        or progress_percent < 0
        or progress_percent >= 100
        or priority not in (1, 2, 3)
        or data_precision not in ("exact", "approx", "rough")
    ):
        raise ValueError("进行中任务需要名称、截止日期、正数预计工时、有效进度和 1-3 优先级")
    if stress_level not in (None, ""):
        stress_level = max(1, min(int(stress_level), 5))
    else:
        stress_level = None
    connection = db()
    cursor = connection.execute(
        """INSERT INTO tasks(
            title, course, due_date, estimated_hours, priority, status,
            started_at, spent_hours, progress_percent, stress_level,
            is_historical, data_source, data_precision, import_batch_id
        ) VALUES (?, ?, ?, ?, ?, 'todo', ?, ?, ?, ?, 0, 'ongoing_import', ?, ?)""",
        (
            title,
            course,
            due_date,
            estimated_hours,
            priority,
            started_at,
            spent_hours,
            progress_percent,
            stress_level,
            data_precision,
            batch_id,
        ),
    )
    connection.commit()
    task_id = cursor.lastrowid
    connection.close()
    return task_id


def update_task_progress(task_id: int, payload: dict) -> dict:
    """Update observed effort/progress without marking the task complete."""
    connection = db()
    existing = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if existing is None:
        connection.close()
        raise LookupError("任务不存在")
    if existing["status"] == "done":
        connection.close()
        raise ValueError("已完成任务不能更新进行中进度")

    spent_hours = float(payload.get("spent_hours", existing["spent_hours"] or 0))
    progress_percent = float(payload.get("progress_percent", existing["progress_percent"] or 0))
    if spent_hours < 0 or progress_percent < 0 or progress_percent >= 100:
        connection.close()
        raise ValueError("已投入工时不能为负，进度应在 0（含）到 100（不含）之间")
    started_at = existing["started_at"]
    if not started_at and (spent_hours > 0 or progress_percent > 0):
        started_at = datetime.now().isoformat(timespec="minutes")
    connection.execute(
        "UPDATE tasks SET spent_hours=?, progress_percent=?, started_at=?, data_source='live_progress' WHERE id=?",
        (spent_hours, progress_percent, started_at, task_id),
    )
    connection.commit()
    connection.close()
    return {
        "id": task_id,
        "spent_hours": spent_hours,
        "progress_percent": progress_percent,
        "started_at": started_at,
    }


def insights(window_days: int | None = None) -> dict:
    config = settings()
    days = window_days or int(config["tracking_window_days"])
    days = max(7, min(days, 365))
    cutoff = datetime.now() - timedelta(days=days)
    connection = db()
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM tasks WHERE status='done' AND completed_at IS NOT NULL AND completed_at >= ? ORDER BY completed_at DESC",
            (cutoff.isoformat(timespec="minutes"),),
        )
    ]
    connection.close()
    total = len(rows)
    historical_count = sum(bool(row.get("is_historical")) for row in rows)
    early = sum(row.get("completion_type") == "early" for row in rows)
    on_time = sum(row.get("completion_type") == "on_time" for row in rows)
    late = sum(row.get("completion_type") == "late" for row in rows)
    reliable_rows = [row for row in rows if row.get("actual_hours") and row.get("data_precision") != "rough"]
    ratios = [float(row["actual_hours"]) / float(row["estimated_hours"]) for row in reliable_rows]
    near_ratios = [float(row["estimated_hours"]) / float(row["actual_hours"]) for row in reliable_rows if row.get("completion_type") == "on_time"]
    early_ratios = [float(row["estimated_hours"]) / float(row["actual_hours"]) for row in reliable_rows if row.get("completion_type") == "early"]
    near_efficiency = round(statistics.mean(near_ratios), 2) if near_ratios else None
    early_efficiency = round(statistics.mean(early_ratios), 2) if early_ratios else None
    deadline_effect = round(near_efficiency / early_efficiency, 2) if near_efficiency and early_efficiency else None

    current = float(config["buffer_ratio"])
    suggested = current
    reason_parts = []
    if total >= 5:
        on_time_rate = (early + on_time) / total
        if on_time_rate < 0.8:
            suggested += 0.05
            reason_parts.append("近期开工后按时完成率偏低")
        if ratios and statistics.median(ratios) > 1.1:
            suggested += 0.05
            reason_parts.append("实际用时经常高于预计用时")
        if late / total > 0.2:
            suggested += 0.05
            reason_parts.append("逾期任务比例较高")
        if on_time_rate >= 0.9 and ratios and statistics.median(ratios) < 0.95 and late == 0:
            suggested -= 0.05
            reason_parts.append("估时稳定且近期没有逾期")
        if deadline_effect and deadline_effect > 1.15:
            reason_parts.append("临近截止日期时效率提高，但不建议因此直接降低缓冲")
    else:
        reason_parts.append("已完成任务少于 5 个，继续记录后再调整")
    suggested = round(max(0.05, min(0.4, current + max(-0.05, min(suggested - current, 0.05)))), 2)
    return {
        "window_days": days,
        "sample_count": total,
        "historical_count": historical_count,
        "live_count": total - historical_count,
        "early_count": early,
        "on_time_count": on_time,
        "late_count": late,
        "on_time_rate": round((early + on_time) / total, 2) if total else None,
        "median_estimation_ratio": round(statistics.median(ratios), 2) if ratios else None,
        "early_efficiency": early_efficiency,
        "near_deadline_efficiency": near_efficiency,
        "deadline_effect": deadline_effect,
        "current_buffer_ratio": current,
        "suggested_buffer_ratio": suggested,
        "can_adjust": total >= 5 and suggested != current,
        "recommendation": "；".join(reason_parts),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AcademicLoadRadar/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: dict | list, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self.send_json({"status": "ok", "service": "academic-load-radar"})
        elif path == "/api/tasks":
            self.send_json(task_rows())
        elif path == "/api/tasks/history":
            self.send_json([task for task in task_rows() if task.get("is_historical")])
        elif path == "/api/tasks/ongoing":
            self.send_json([task for task in task_rows() if task.get("status") != "done" and task.get("data_source") == "ongoing_import"])
        elif path == "/api/settings":
            self.send_json(settings())
        elif path == "/api/dashboard":
            portfolio = process_task_batch(raw_task_rows(), settings())
            tasks = portfolio["tasks"]
            active = [task for task in tasks if task["status"] != "done"]
            self.send_json(
                {
                    "today": date.today().isoformat(),
                    "total_tasks": len(tasks),
                    "completed_tasks": sum(task["status"] == "done" for task in tasks),
                    "planned_hours": round(sum(float(task.get("remaining_hours", task["estimated_hours"])) for task in active), 2),
                    "high_risk_tasks": sum(task["risk"] == "high" for task in active),
                    "medium_risk_tasks": sum(task["risk"] == "medium" for task in active),
                    "schedule_missed_tasks": portfolio["schedule_missed_count"],
                    "next_actions": portfolio["next_actions"],
                    "algorithm": portfolio["algorithm"],
                    "tasks": tasks,
                }
            )
        elif path == "/api/insights":
            query = parse_qs(parsed.query)
            requested_days = query.get("window", [None])[0]
            self.send_json(insights(int(requested_days) if requested_days else None))
        elif path in ("/", "/index.html", "/app.js", "/style.css"):
            self.serve_static(path)
        else:
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/tasks/batch/process":
            try:
                result = process_task_batch(raw_task_rows(), settings())
                self.send_json(result)
            except (TypeError, ValueError, KeyError) as error:
                self.send_json({"error": str(error) or "批量处理失败"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/tasks/ongoing":
            try:
                task_id = insert_ongoing(self.read_json())
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error) or "进行中任务数据无效"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"id": task_id, "ongoing": True}, HTTPStatus.CREATED)
            return
        if path == "/api/tasks/ongoing/import":
            try:
                payload = self.read_json()
                content = str(payload.get("csv", ""))
                if not content.strip():
                    raise ValueError("CSV 内容为空")
                batch_id = uuid.uuid4().hex[:12]
                reader = csv.DictReader(io.StringIO(content))
                created = []
                errors = []
                aliases = {
                    "title": "任务名称",
                    "course": "课程",
                    "due_date": "截止日期",
                    "started_at": "开始时间",
                    "estimated_hours": "预计总工时",
                    "spent_hours": "已投入工时",
                    "progress_percent": "当前进度",
                    "priority": "优先级",
                    "data_precision": "数据精度",
                    "stress_level": "压力感受",
                }
                for line, row in enumerate(reader, start=2):
                    try:
                        normalized = {key: row.get(key) or row.get(label) for key, label in aliases.items()}
                        normalized["data_precision"] = {
                            "精确记录": "exact",
                            "大致回忆": "approx",
                            "粗略估计": "rough",
                        }.get(normalized.get("data_precision"), normalized.get("data_precision"))
                        created.append(insert_ongoing(normalized, batch_id=batch_id))
                    except (TypeError, ValueError, KeyError) as error:
                        errors.append({"line": line, "error": str(error)})
                self.send_json(
                    {"batch_id": batch_id, "created": created, "errors": errors},
                    HTTPStatus.CREATED if created else HTTPStatus.BAD_REQUEST,
                )
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error) or "CSV 数据无效"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/tasks/history":
            try:
                task_id = insert_historical(self.read_json())
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error) or "历史任务数据无效"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"id": task_id, "historical": True}, HTTPStatus.CREATED)
            return
        if path == "/api/tasks/import":
            try:
                payload = self.read_json()
                content = str(payload.get("csv", ""))
                if not content.strip():
                    raise ValueError("CSV 内容为空")
                batch_id = uuid.uuid4().hex[:12]
                reader = csv.DictReader(io.StringIO(content))
                created = []
                errors = []
                for line, row in enumerate(reader, start=2):
                    try:
                        aliases = {
                            "title": "任务名称",
                            "course": "课程",
                            "due_date": "截止日期",
                            "started_at": "开始时间",
                            "completed_at": "完成时间",
                            "estimated_hours": "预计工时",
                            "actual_hours": "实际工时",
                            "data_precision": "数据精度",
                            "stress_level": "压力感受",
                        }
                        normalized = {key: row.get(key) or row.get(label) for key, label in aliases.items()}
                        normalized["data_precision"] = {
                            "精确记录": "exact",
                            "大致回忆": "approx",
                            "粗略估计": "rough",
                        }.get(normalized.get("data_precision"), normalized.get("data_precision"))
                        created.append(insert_historical(normalized, batch_id=batch_id))
                    except (TypeError, ValueError, KeyError) as error:
                        errors.append({"line": line, "error": str(error)})
                self.send_json({"batch_id": batch_id, "created": created, "errors": errors}, HTTPStatus.CREATED if created else HTTPStatus.BAD_REQUEST)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error) or "CSV 数据无效"}, HTTPStatus.BAD_REQUEST)
            return
        if path != "/api/tasks":
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self.read_json()
            title = str(payload.get("title", "")).strip()
            course = str(payload.get("course", "")).strip()
            due_date = str(payload.get("due_date", ""))
            estimated_hours = float(payload.get("estimated_hours", 0))
            priority = int(payload.get("priority", 2))
            date.fromisoformat(due_date)
            if not title or estimated_hours <= 0 or priority not in (1, 2, 3):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            self.send_json({"error": "请填写有效的任务名称、截止日期、正数工时和 1-3 优先级"}, HTTPStatus.BAD_REQUEST)
            return
        connection = db()
        cursor = connection.execute(
            "INSERT INTO tasks(title, course, due_date, estimated_hours, priority) VALUES (?, ?, ?, ?, ?)",
            (title, course, due_date, estimated_hours, priority),
        )
        connection.commit()
        connection.close()
        self.send_json({"id": cursor.lastrowid}, HTTPStatus.CREATED)

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        path = path.rstrip("/") or "/"
        if path.startswith("/api/tasks/"):
            try:
                task_id = int(path.rsplit("/", 1)[1])
                payload = self.read_json()
            except (TypeError, ValueError, json.JSONDecodeError):
                self.send_json({"error": "任务编号或数据无效"}, HTTPStatus.BAD_REQUEST)
                return

            if "status" not in payload and ("spent_hours" in payload or "progress_percent" in payload):
                try:
                    result = update_task_progress(task_id, payload)
                except LookupError as error:
                    self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                    return
                except (TypeError, ValueError) as error:
                    self.send_json({"error": str(error) or "进度数据无效"}, HTTPStatus.BAD_REQUEST)
                    return
                self.send_json(result)
                return

            status = payload.get("status")
            if status not in ("todo", "done"):
                self.send_json({"error": "任务状态无效"}, HTTPStatus.BAD_REQUEST)
                return
            connection = db()
            existing = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if existing is None:
                connection.close()
                self.send_json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
                return
            if status == "done":
                actual_hours = float(payload.get("actual_hours", 0))
                if actual_hours <= 0:
                    connection.close()
                    self.send_json({"error": "完成任务时需要填写正数实际用时"}, HTTPStatus.BAD_REQUEST)
                    return
                now = datetime.now()
                threshold = float(settings()["early_threshold_hours"])
                completion_at = now.isoformat(timespec="minutes")
                completion_type = completion_type_for(existing["due_date"], completion_at, threshold)
                stress_level = payload.get("stress_level")
                if stress_level is not None:
                    stress_level = max(1, min(int(stress_level), 5))
                cursor = connection.execute(
                    "UPDATE tasks SET status=?, actual_hours=?, spent_hours=?, progress_percent=100, completed_at=?, completion_type=?, stress_level=?, is_historical=0, data_source='live', data_precision='exact' WHERE id=?",
                    (status, actual_hours, actual_hours, completion_at, completion_type, stress_level, task_id),
                )
            else:
                cursor = connection.execute(
                    "UPDATE tasks SET status=?, actual_hours=NULL, completed_at=NULL, completion_type=NULL, stress_level=NULL WHERE id=?",
                    (status, task_id),
                )
            connection.commit()
            connection.close()
            self.send_json({"id": task_id, "status": status, "completion_type": completion_type if status == "done" else None})
            return
        if path != "/api/settings":
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self.read_json()
            weekday = max(0.0, min(float(payload["weekday_hours"]), 24.0))
            weekend = max(0.0, min(float(payload["weekend_hours"]), 24.0))
            buffer_ratio = max(0.0, min(float(payload["buffer_ratio"]), 0.8))
            tracking_mode = int(bool(payload.get("tracking_mode", True)))
            tracking_window_days = max(7, min(int(payload.get("tracking_window_days", 30)), 365))
            early_threshold_hours = max(1.0, min(float(payload.get("early_threshold_hours", 24)), 168.0))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_json({"error": "时间设置无效"}, HTTPStatus.BAD_REQUEST)
            return
        connection = db()
        connection.execute(
            "UPDATE settings SET weekday_hours=?, weekend_hours=?, buffer_ratio=?, tracking_mode=?, tracking_window_days=?, early_threshold_hours=? WHERE id=1",
            (weekday, weekend, buffer_ratio, tracking_mode, tracking_window_days, early_threshold_hours),
        )
        connection.commit()
        connection.close()
        self.send_json(settings())

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/api/tasks/"):
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        try:
            task_id = int(path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json({"error": "任务编号无效"}, HTTPStatus.BAD_REQUEST)
            return
        connection = db()
        cursor = connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        connection.commit()
        connection.close()
        if cursor.rowcount == 0:
            self.send_json({"error": "任务不存在"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"deleted": task_id})

    def serve_static(self, path: str) -> None:
        filename = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        file_path = STATIC / filename
        if not file_path.is_file() or file_path.parent != STATIC:
            self.send_json({"error": "页面不存在"}, HTTPStatus.NOT_FOUND)
            return
        content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(file_path.suffix, "text/plain")
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run() -> None:
    init_db()
    port = configured_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    display_host = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    print(f"Academic Load Radar running at http://{display_host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
