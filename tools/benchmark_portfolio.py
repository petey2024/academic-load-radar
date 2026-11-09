"""Repeatable local benchmark for a 500-task portfolio analysis."""

from __future__ import annotations

import math
import statistics
import time
from datetime import date, timedelta

from app.risk import process_task_batch


def benchmark(label, tasks, settings, today):
    for _ in range(3):
        process_task_batch(tasks, settings, today=today)
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        result = process_task_batch(tasks, settings, today=today)
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    print(f"case={label} tasks={len(tasks)} method={result['tradeoff_plan']['method']}")
    print(f"median_ms={statistics.median(samples):.3f}")
    print(f"p95_ms={ordered[math.ceil(0.95 * len(ordered)) - 1]:.3f}")
    print(f"max_ms={max(samples):.3f}")


def main() -> None:
    today = date(2026, 10, 9)
    settings = {"weekday_hours": 2, "weekend_hours": 1, "buffer_ratio": 0.2}
    active = [
        {
            "id": index + 1,
            "title": f"Benchmark task {index + 1}",
            "due_date": (today + timedelta(days=1 + index % 30)).isoformat(),
            "estimated_hours": 1 + index % 5,
            "priority": 1 + index % 3,
            "status": "todo",
        }
        for index in range(500)
    ]
    benchmark("500_active", active, settings, today)
    mixed = [{**item, "title": f"编程实验报告{index % 5}", "course": f"课程{index % 5}"}
             for index, item in enumerate(active)]
    for index in range(250, 500):
        mixed[index].update(status="done", actual_hours=mixed[index]["estimated_hours"] * (1 + index % 4 / 10))
    benchmark("250_active_250_history", mixed, settings, today)


if __name__ == "__main__":
    main()
