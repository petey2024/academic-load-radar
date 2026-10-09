"""Explainable workload prediction, EDF scheduling and deadline risk."""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any


def available_hours_until(
    due_date: date,
    *,
    weekday_hours: float,
    weekend_hours: float,
    buffer_ratio: float,
    today: date | None = None,
) -> float:
    """Return usable hours through the due date, including a safety buffer."""
    today = today or date.today()
    days = max((due_date - today).days + 1, 0)
    capacity = 0.0
    for offset in range(days):
        current = today + timedelta(days=offset)
        capacity += weekend_hours if current.weekday() >= 5 else weekday_hours
    return round(capacity * (1 - max(0.0, min(buffer_ratio, 0.8))), 2)


def historical_efficiency(tasks: list[dict[str, Any]]) -> float:
    """Return the robust actual/estimated ratio from completed reliable tasks."""
    ratios = [
        float(task["actual_hours"]) / float(task["estimated_hours"])
        for task in tasks
        if task.get("status") == "done"
        and task.get("actual_hours")
        and float(task["estimated_hours"]) > 0
        and task.get("data_precision", "exact") != "rough"
    ]
    return round(statistics.median(ratios), 2) if ratios else 1.0


def historical_sample_count(tasks: list[dict[str, Any]]) -> int:
    """Count completed samples that are reliable enough for forecasting."""
    return sum(
        1
        for task in tasks
        if task.get("status") == "done"
        and task.get("actual_hours")
        and float(task.get("estimated_hours") or 0) > 0
        and task.get("data_precision", "exact") != "rough"
    )


def prediction_confidence(
    task: dict[str, Any],
    history_samples: int = 0,
    *,
    inconsistent_progress: bool = False,
) -> dict[str, str]:
    """Rate how much evidence supports a remaining-time prediction."""
    spent = float(task.get("spent_hours") or 0)
    progress = float(task.get("progress_percent") or 0)
    precision = task.get("data_precision", "exact")
    score = 0
    evidence = []
    if spent > 0 and progress > 0:
        score += 2
        evidence.append("已有实际进度")
    if progress >= 50:
        score += 1
        evidence.append("任务已过半")
    if history_samples >= 5:
        score += 2
        evidence.append("历史样本充足")
    elif history_samples >= 2:
        score += 1
        evidence.append("已有少量历史样本")
    if precision == "exact":
        score += 1
    elif precision == "rough":
        score -= 1
    if inconsistent_progress:
        score -= 2
        evidence.append("投入工时与完成比例明显不一致")

    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    if not evidence:
        evidence.append("主要依赖初始估时")
    return {"level": level, "reason": "、".join(evidence)}


def predict_remaining_hours(
    task: dict[str, Any],
    *,
    efficiency_ratio: float = 1.0,
    history_samples: int = 0,
) -> dict[str, Any]:
    """Blend observed progress with robust historical estimation accuracy."""
    estimated = max(float(task.get("estimated_hours") or 0), 0.1)
    spent = max(float(task.get("spent_hours") or 0), 0.0)
    progress = max(0.0, min(float(task.get("progress_percent") or 0), 99.0)) / 100
    history_total = estimated * max(float(efficiency_ratio or 1.0), 0.1)
    history_remaining = max(history_total - spent, 0.1)
    progress_total = spent / progress if spent > 0 and progress > 0 else None
    consistency_ratio = (progress_total / history_total) if progress_total is not None else 1.0
    inconsistent_progress = progress_total is not None and (
        consistency_ratio < 0.4 or consistency_ratio > 2.5
    )

    if spent > 0 and progress > 0:
        progress_total = spent / progress
        progress_remaining = max(progress_total - spent, 0.1)
        progress_weight = min(0.75, max(0.4, progress))
        remaining = progress_weight * progress_remaining + (1 - progress_weight) * history_remaining
        method = "progress_and_history"
    elif spent > 0:
        remaining = history_remaining
        method = "history_after_spent_time"
    else:
        remaining = history_total
        method = "historical_efficiency" if efficiency_ratio != 1.0 else "initial_estimate"

    confidence = prediction_confidence(
        task,
        history_samples,
        inconsistent_progress=inconsistent_progress,
    )
    warning = None
    if inconsistent_progress:
        warning = (
            "当前投入工时与完成比例推算出的总工时差异较大，"
            "请核对进度或预计工时；系统保留两类信号但降低预测可信度。"
        )
    return {
        "remaining_hours": round(max(remaining, 0.1), 2),
        "predicted_total_hours": round(spent + max(remaining, 0.1), 2),
        "prediction_method": method,
        "historical_efficiency_ratio": round(float(efficiency_ratio or 1.0), 2),
        "prediction_confidence": confidence["level"],
        "confidence_reason": confidence["reason"],
        "prediction_warning": warning,
    }


def _schedule_horizon(today: date, remaining: dict[int, float], settings: dict[str, Any]) -> date:
    """Choose a bounded horizon that expands for unusually large portfolios."""
    buffer_ratio = max(0.0, min(float(settings["buffer_ratio"]), 0.8))
    weekday_capacity = max(float(settings["weekday_hours"]) * (1 - buffer_ratio), 0.0)
    weekend_capacity = max(float(settings["weekend_hours"]) * (1 - buffer_ratio), 0.0)
    weekly_capacity = 5 * weekday_capacity + 2 * weekend_capacity
    if weekly_capacity <= 0:
        return today + timedelta(days=730)
    total_remaining = sum(max(value, 0.0) for value in remaining.values())
    estimated_days = int(total_remaining / (weekly_capacity / 7)) + 30
    return today + timedelta(days=min(max(730, estimated_days), 3650))


def _daily_capacity(day: date, settings: dict[str, Any]) -> float:
    raw = float(settings["weekend_hours"] if day.weekday() >= 5 else settings["weekday_hours"])
    buffer_ratio = max(0.0, min(float(settings["buffer_ratio"]), 0.8))
    return max(raw * (1 - buffer_ratio), 0.0)


def simulate_edf_schedule(
    indexed_tasks: list[tuple[int, dict[str, Any]]],
    predictions: dict[int, dict[str, Any]],
    settings: dict[str, Any],
    *,
    today: date,
) -> dict[int, dict[str, Any]]:
    """Simulate work allocation by earliest deadline, then priority.

    EDF is a suitable baseline for deadline-oriented personal planning. The
    simulation makes the plan verifiable: each task receives a projected finish
    date and any hours that cannot be scheduled before its deadline.
    """
    ordered = sorted(
        indexed_tasks,
        key=lambda item: (
            date.fromisoformat(item[1]["due_date"]),
            -int(item[1].get("priority", 2)),
            item[0],
        ),
    )
    schedules: dict[int, dict[str, Any]] = {}
    remaining = {index: float(predictions[index]["remaining_hours"]) for index, _ in ordered}
    for rank, (index, task) in enumerate(ordered, start=1):
        due = date.fromisoformat(task["due_date"])
        schedules[index] = {
            "recommended_rank": rank,
            "projected_finish_date": None,
            "shortfall_hours": round(remaining[index], 2) if due < today else 0.0,
            "schedule_status": "late" if due < today else "on_track",
        }

    current = today
    horizon = _schedule_horizon(today, remaining, settings)
    while current <= horizon and any(value > 0.005 for value in remaining.values()):
        capacity = _daily_capacity(current, settings)
        for index, task in ordered:
            if remaining[index] <= 0.005:
                continue
            worked = min(capacity, remaining[index])
            remaining[index] -= worked
            capacity -= worked
            due = date.fromisoformat(task["due_date"])
            if remaining[index] <= 0.005:
                schedules[index]["projected_finish_date"] = current.isoformat()
                if current > due:
                    schedules[index]["schedule_status"] = "late"
            if capacity <= 0.005:
                break
        # Snapshot every unfinished task at its deadline, including tasks that
        # received no time because an earlier EDF item used the day's capacity.
        for index, task in ordered:
            if current == date.fromisoformat(task["due_date"]) and remaining[index] > 0.005:
                schedules[index]["shortfall_hours"] = round(remaining[index], 2)
                schedules[index]["schedule_status"] = "late"
        current += timedelta(days=1)

    for index, task in ordered:
        schedule = schedules[index]
        due = date.fromisoformat(task["due_date"])
        if schedule["projected_finish_date"] is None:
            schedule["schedule_status"] = "late"
            if schedule["shortfall_hours"] <= 0:
                schedule["shortfall_hours"] = round(remaining[index], 2)
        finish = schedule["projected_finish_date"]
        if schedule["schedule_status"] == "late":
            schedule["schedule_note"] = (
                f"按当前容量，截止日前仍缺 {schedule['shortfall_hours']:g} 小时；"
                + (f"预计 {finish} 完成。" if finish else "预测周期内无法排完。")
            )
        else:
            schedule["schedule_note"] = f"按当前容量预计 {finish} 完成，不晚于 {due.isoformat()}。"
    return schedules


def _assess_one(
    task: dict[str, Any],
    settings: dict[str, Any],
    *,
    today: date,
    efficiency_ratio: float,
    prediction: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    history_samples: int = 0,
    cumulative_remaining: float | None = None,
    cumulative_available: float | None = None,
) -> dict[str, Any]:
    due = date.fromisoformat(task["due_date"])
    if task.get("status") == "done":
        return {
            **task,
            "remaining_hours": 0,
            "prediction_method": "completed",
            "prediction_confidence": "high",
            "confidence_reason": "已有实际完成记录",
            "historical_efficiency_ratio": efficiency_ratio,
            "risk": "low",
            "risk_score": 0,
            "available_hours": 0,
            "cumulative_remaining_hours": 0,
            "cumulative_available_hours": 0,
            "slack_hours": 0,
            "required_daily_hours": 0,
            "recommended_rank": None,
            "projected_finish_date": task.get("completed_at", "")[:10] or None,
            "shortfall_hours": 0,
            "schedule_status": "completed",
            "schedule_note": "任务已完成。",
            "explanation": "任务已完成。",
        }

    prediction = prediction or predict_remaining_hours(
        task,
        efficiency_ratio=efficiency_ratio,
        history_samples=history_samples,
    )
    schedule = schedule or {}
    remaining = prediction["remaining_hours"]
    available = available_hours_until(
        due,
        weekday_hours=float(settings["weekday_hours"]),
        weekend_hours=float(settings["weekend_hours"]),
        buffer_ratio=float(settings["buffer_ratio"]),
        today=today,
    )
    cumulative_remaining = round(cumulative_remaining if cumulative_remaining is not None else remaining, 2)
    cumulative_available = round(cumulative_available if cumulative_available is not None else available, 2)
    ratio = cumulative_remaining / max(cumulative_available, 0.1)
    days_left = (due - today).days
    score = min(65.0, ratio * 50.0)
    score += 20.0 if days_left <= 2 else 10.0 if days_left <= 5 else 0.0
    score += 15.0 if int(task["priority"]) >= 3 else 7.0 if int(task["priority"]) == 2 else 0.0
    score = round(min(score, 100.0))

    if due < today or ratio > 1 or schedule.get("schedule_status") == "late":
        risk = "high"
    elif ratio >= 0.7 or days_left <= 2:
        risk = "medium"
    else:
        risk = "low"

    if due < today:
        explanation = "任务已经逾期，需要立即重新安排。"
    elif cumulative_available <= 0:
        explanation = "截止日期前没有可用时间。"
    elif ratio > 1:
        explanation = (
            f"截至 {due.isoformat()}，任务组合预计还需 {cumulative_remaining:g} 小时，"
            f"但缓冲后只有 {cumulative_available:g} 小时可用；本任务预计还需 {remaining:g} 小时。"
        )
    elif days_left <= 2:
        explanation = f"距离截止日期只剩 {max(days_left, 0)} 天；本任务预计还需 {remaining:g} 小时。"
    else:
        explanation = (
            f"截至该截止日期，累计负荷约占可用时间的 {ratio:.0%}；"
            f"本任务预计还需 {remaining:g} 小时。"
        )

    return {
        **task,
        **prediction,
        **schedule,
        "available_hours": cumulative_available,
        "cumulative_remaining_hours": cumulative_remaining,
        "cumulative_available_hours": cumulative_available,
        "slack_hours": round(cumulative_available - cumulative_remaining, 2),
        "required_daily_hours": round(cumulative_remaining / max(days_left + 1, 1), 2),
        "days_left": days_left,
        "risk": risk,
        "risk_score": score,
        "workload_ratio": round(ratio, 2),
        "explanation": explanation,
    }


def assess_tasks(
    tasks: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Assess all tasks using cumulative workload and an EDF simulation."""
    today = today or date.today()
    efficiency_ratio = historical_efficiency(tasks)
    history_samples = historical_sample_count(tasks)
    indexed_active = [(index, task) for index, task in enumerate(tasks) if task.get("status") != "done"]
    predictions = {
        index: predict_remaining_hours(
            task,
            efficiency_ratio=efficiency_ratio,
            history_samples=history_samples,
        )
        for index, task in indexed_active
    }
    schedules = simulate_edf_schedule(indexed_active, predictions, settings, today=today)

    assessed = []
    for index, task in enumerate(tasks):
        if task.get("status") == "done":
            assessed.append(
                _assess_one(
                    task,
                    settings,
                    today=today,
                    efficiency_ratio=efficiency_ratio,
                    history_samples=history_samples,
                )
            )
            continue
        due = date.fromisoformat(task["due_date"])
        cumulative_remaining = sum(
            predictions[other_index]["remaining_hours"]
            for other_index, other in indexed_active
            if date.fromisoformat(other["due_date"]) <= due
        )
        cumulative_available = available_hours_until(
            due,
            weekday_hours=float(settings["weekday_hours"]),
            weekend_hours=float(settings["weekend_hours"]),
            buffer_ratio=float(settings["buffer_ratio"]),
            today=today,
        )
        assessed.append(
            _assess_one(
                task,
                settings,
                today=today,
                efficiency_ratio=efficiency_ratio,
                prediction=predictions[index],
                schedule=schedules[index],
                history_samples=history_samples,
                cumulative_remaining=cumulative_remaining,
                cumulative_available=cumulative_available,
            )
        )
    return assessed


def assess_task(task: dict[str, Any], settings: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Assess one task, retained as a backwards-compatible helper."""
    return assess_tasks([task], settings, today=today)[0]


def process_task_batch(
    tasks: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Process all entered tasks together and return a portfolio summary."""
    assessed = assess_tasks(tasks, settings, today=today)
    active = [task for task in assessed if task.get("status") != "done"]
    risk_counts = {
        "high": sum(task.get("risk") == "high" for task in active),
        "medium": sum(task.get("risk") == "medium" for task in active),
        "low": sum(task.get("risk") == "low" for task in active),
    }
    overloaded = [task for task in active if float(task.get("workload_ratio") or 0) > 1]
    missed = [task for task in active if task.get("schedule_status") == "late"]
    earliest_overload = min((task["due_date"] for task in overloaded), default=None)
    highest_risk = max(
        active,
        key=lambda task: (float(task.get("risk_score") or 0), task.get("due_date", "")),
        default=None,
    )
    next_actions = sorted(active, key=lambda task: task.get("recommended_rank") or 999999)[:3]
    return {
        "processed_count": len(assessed),
        "active_count": len(active),
        "completed_count": len(assessed) - len(active),
        "remaining_hours": round(sum(float(task.get("remaining_hours") or 0) for task in active), 2),
        "risk_counts": risk_counts,
        "overloaded_count": len(overloaded),
        "schedule_missed_count": len(missed),
        "earliest_overload_due_date": earliest_overload,
        "highest_risk_task": {
            "id": highest_risk.get("id"),
            "title": highest_risk.get("title"),
            "risk_score": highest_risk.get("risk_score"),
        } if highest_risk else None,
        "algorithm": {
            "scheduling": "EDF（最早截止期优先，同截止期按优先级）",
            "prediction": "进度外推与历史估时偏差加权",
            "buffer_applied": True,
        },
        "next_actions": next_actions,
        "tasks": assessed,
    }
