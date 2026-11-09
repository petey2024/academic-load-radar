"""Preview the October 30 acceptance CSVs without touching the personal database."""
import csv
import argparse
import tempfile
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from app import main


def run(extra_hours=0):
    folder = main.ROOT / 'examples' / 'acceptance'
    with tempfile.TemporaryDirectory(prefix='radar-acceptance-preview-') as temp:
        main.DATA_DIR=Path(temp);main.DB_PATH=Path(temp)/'demo.sqlite3';main.init_db()
        for filename,insert in [('历史任务-10月30日验收.csv',main.insert_historical),('当前任务-10月30日验收.csv',main.insert_ongoing)]:
            with (folder/filename).open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):insert(row)
        connection=main.db()
        connection.execute('UPDATE settings SET weekday_hours=2,weekend_hours=4,buffer_ratio=0.2,max_extra_daily_hours=? WHERE id=1', (extra_hours,))
        connection.commit();connection.close()
        server=ThreadingHTTPServer(('127.0.0.1',0),main.Handler)
        print(f'Acceptance demo: http://127.0.0.1:{server.server_port}',flush=True)
        print(f'Synthetic data anchored to 2026-10-30. Application date: {date.today()}. Personal DB not used.',flush=True)
        print(f'Extra daily limit: {extra_hours:g}h. Stop and restart to reset the sample.',flush=True)
        if date.today() != date(2026, 10, 30):
            print('The sample dates are fixed; results will differ from the October 30 presentation.', flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extra-hours', type=float, default=0,
                        help='Extra daily limit, from 0 to 8 hours (default: 0).')
    arguments = parser.parse_args()
    if not 0 <= arguments.extra_hours <= 8:
        parser.error('--extra-hours must be between 0 and 8')
    run(arguments.extra_hours)
