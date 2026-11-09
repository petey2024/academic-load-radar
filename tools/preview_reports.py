"""Run the report UI on disposable CSV evaluation data; never touch personal DB."""
import csv
import tempfile
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import main


def seed_evaluation():
    folder = main.ROOT / "examples" / "evaluation"
    offset = date.today() - date(2026, 10, 16)
    for filename, insert in (("历史任务-评测.csv", main.insert_historical), ("当前任务-评测.csv", main.insert_ongoing)):
        with (folder/filename).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                for key in ("due_date", "started_at", "completed_at"):
                    if row.get(key):
                        row[key] = (date.fromisoformat(row[key][:10])+offset).isoformat()+row[key][10:]
                insert(row)
    connection = main.db()
    connection.execute("UPDATE settings SET weekday_hours=2,weekend_hours=4,buffer_ratio=0.2,max_extra_daily_hours=1 WHERE id=1")
    connection.commit()
    connection.close()


def run():
    with tempfile.TemporaryDirectory(prefix="radar-report-preview-") as directory:
        main.DATA_DIR = Path(directory)
        main.DB_PATH = main.DATA_DIR/"preview.sqlite3"
        main.init_db()
        seed_evaluation()
        server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        print(f"Disposable report preview: http://127.0.0.1:{server.server_port}", flush=True)
        print("Synthetic CSV dates shifted to today. Personal task DB is not used.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    run()
