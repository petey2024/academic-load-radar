"""Run a disposable, seeded preview without changing the user's task database."""

import tempfile
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import main


def run():
    with tempfile.TemporaryDirectory(prefix="radar-planning-preview-") as directory:
        main.DATA_DIR = Path(directory)
        main.DB_PATH = main.DATA_DIR / "preview.sqlite3"
        main.init_db()
        today = date.today()
        past = (today - timedelta(days=7)).isoformat()
        connection = main.db()
        connection.execute("UPDATE settings SET weekday_hours=10, weekend_hours=10, buffer_ratio=0.2 WHERE id=1")
        connection.executemany(
            "INSERT INTO tasks(title,course,due_date,estimated_hours,actual_hours,status,completed_at,is_historical) VALUES (?,?,?,?,?,'done',?,1)",
            [("数据库实验报告一", "数据库", past, 2, 2.4, past + "T20:00"),
             ("数据库实验报告二", "数据库", past, 2, 2.8, past + "T20:00"),
             ("英语阅读笔记一", "英语", past, 4, 4, past + "T20:00")],
        )
        connection.executemany(
            "INSERT INTO tasks(title,course,due_date,estimated_hours,priority) VALUES (?,?,?,?,?)",
            [("数据库实验报告三", "数据库", today.isoformat(), 2, 3),
             ("英语阅读笔记二", "英语", today.isoformat(), 4, 2),
             ("复习计算机网络", "计算机网络", today.isoformat(), 2, 1),
             ("整理数学错题", "高等数学", today.isoformat(), 2, 1),
             ("制作社团海报", "社团", today.isoformat(), 2, 1)],
        )
        connection.commit()
        connection.close()
        server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        print(f"Disposable preview: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    run()
