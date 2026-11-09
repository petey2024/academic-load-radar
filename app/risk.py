"""Explainable workload prediction, EDF scheduling and deadline risk."""

from __future__ import annotations

import heapq
import math
import statistics
from datetime import date, timedelta
from typing import Any

from .history import HistoryMatcher


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
        and task.get("completion_scope") != "partial"
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
        and task.get("completion_scope") != "partial"
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
        partial = task.get("completion_scope") == "partial"
        explanation = (f"任务以部分交付结束，保留完成比例{float(task.get('progress_percent') or 0):g}%；"
                       "不再占用后续排程，也不作为完整完成的工时校准样本。" if partial else "任务已完整完成。")
        return {
            **task,
            "remaining_hours": 0,
            "prediction_method": "partial_delivery" if partial else "completed",
            "prediction_confidence": "not_applicable" if partial else "high",
            "confidence_reason": "部分交付已结束，不再预测剩余工时" if partial else "已有实际完成记录",
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
            "schedule_status": "closed_partial" if partial else "completed",
            "schedule_note": explanation,
            "explanation": explanation,
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
    matcher = HistoryMatcher(tasks)
    predictions = {}
    for index, task in indexed_active:
        evidence = matcher.calibrate(task, efficiency_ratio, history_samples)
        prediction = predict_remaining_hours(
            task,
            efficiency_ratio=evidence.pop("efficiency_ratio"),
            history_samples=evidence.pop("history_samples"),
        )
        predictions[index] = {**prediction, **evidence}
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


def minimum_retained_units(task: dict[str, Any]) -> int:
    """Explicit delivery floors, in hundredths of a predicted remaining hour.

    A percentage is an absolute delivery target, not a percentage of remaining
    work. Mapping scope to effort is linear and provisional, never a guarantee.
    Unknown core scope has no invented floor and must be confirmed by the user.
    """
    hours = round(float(task["remaining_hours"]) * 100)
    if task.get("must_complete"):
        return hours
    minimum = task.get("minimum_delivery_percent")
    if minimum is None:
        return 0
    progress = min(100.0, max(0.0, float(task.get("progress_percent") or 0)))
    fraction = max(0.0, min(1.0, (float(minimum) - progress) / max(100 - progress, 0.01)))
    return min(hours, math.ceil(hours * fraction - 1e-9))


def adjustment_info(task: dict[str, Any], index: int, reduction: int, reason: str) -> dict[str, Any]:
    hours = round(float(task["remaining_hours"]) * 100)
    return {
        "task_id": task.get("id"), "task_index": index, "title": task["title"],
        "priority": int(task["priority"]), "due_date": task["due_date"],
        "remaining_hours": hours / 100, "suggested_reduction_hours": reduction / 100,
        "retained_hours": (hours - reduction) / 100,
        "minimum_retained_hours": minimum_retained_units(task) / 100,
        "minimum_delivery_percent": task.get("minimum_delivery_percent"),
        "must_complete": bool(task.get("must_complete")),
        "core_unconfirmed": task.get("minimum_delivery_percent") is None,
        "requires_confirmation": True,
        "proposal": "原期限外工时需协商延期；不可直接舍弃任务" if reduction >= hours else
                    "仅评估范围调整或部分延期，先确认核心内容",
        "reason": reason, "prediction_confidence": task.get("prediction_confidence", "low"),
    }


def suggest_tradeoffs(
    assessed: list[dict[str, Any]], settings: dict[str, Any], *, today: date
) -> dict[str, Any]:
    """Deadline-prefix relief, minimizing lost high/medium/low-priority hours.

    Walk deadlines from early to late. At each deficit remove only adjustable
    effort, lowest importance first. Explicit core/full-delivery floors are hard
    constraints. Equal-priority ties favor less-progressed, later-due work and
    concentrate adjustments. No task count can override priority-hour protection.
    """
    active = [(index, task) for index, task in enumerate(assessed) if task.get("status") != "done"]
    ordered = sorted(active, key=lambda item: (item[1]["due_date"], -int(item[1]["priority"]), item[0]))
    due_dates = sorted({task["due_date"] for _, task in ordered})
    hours = {index: round(float(task["remaining_hours"]) * 100) for index, task in ordered}
    floors = {index: minimum_retained_units(task) for index, task in ordered}
    reductions = {index: 0 for index, _ in ordered}
    checkpoints, unresolved, high_shortfalls = [], [], []
    for due in due_dates:
        prefix = [(index, task) for index, task in ordered if task["due_date"] <= due]
        capacity = round(100 * available_hours_until(
            date.fromisoformat(due), weekday_hours=float(settings["weekday_hours"]),
            weekend_hours=float(settings["weekend_hours"]), buffer_ratio=float(settings["buffer_ratio"]), today=today,
        ))
        deficit = max(0, sum(hours[index] for index, _ in prefix) - capacity)
        if deficit:
            checkpoints.append({"due_date": due, "shortage_hours": deficit / 100})
        high_required = sum(hours[index] if int(task["priority"]) == 3 else floors[index] for index, task in prefix)
        high_hours = sum(hours[index] for index, task in prefix if int(task["priority"]) == 3)
        if high_hours and high_required > capacity:
            high_shortfalls.append({"due_date": due, "shortage_hours": min(high_hours, high_required - capacity) / 100})
        shortage = sum(hours[index] - reductions[index] for index, _ in prefix) - capacity
        candidates = sorted(prefix, key=lambda item: (
            int(item[1]["priority"]), -date.fromisoformat(item[1]["due_date"]).toordinal(),
            float(item[1].get("progress_percent") or 0),
            -(hours[item[0]] - floors[item[0]] - reductions[item[0]]), item[0],
        ))
        for index, _ in candidates:
            if shortage <= 0:
                break
            relief = min(shortage, hours[index] - floors[index] - reductions[index])
            reductions[index] += relief
            shortage -= relief
        if shortage > 0:
            unresolved.append({
                "due_date": due, "shortage_hours": shortage / 100,
                "minimum_required_hours": sum(floors[index] for index, _ in prefix) / 100,
                "available_hours": capacity / 100,
                "protected_task_ids": [task.get("id") for index, task in prefix if floors[index] > 0],
            })
    if not checkpoints:
        return {"status": "comfortable", "suggestions": [], "checkpoints": []}

    suggestions = []
    for index, task in sorted(ordered, key=lambda item: (int(item[1]["priority"]), item[1]["due_date"], item[0])):
        if not reductions[index]:
            continue
        if int(task["priority"]) == 3:
            reason = "已优先让出同期低、中重要度任务的可调整工时，重要任务仍有不可避免的期限缺口；"
        elif int(task["priority"]) == 2:
            reason = "同期低重要度任务的可调整工时不足以补齐缺口，才考虑中重要度任务；"
        else:
            reason = "先调整低重要度任务，为较重要任务保留期限前工时；"
        reason += f"本项原期限内预计保留 {(hours[index]-reductions[index])/100:g} 小时，另有 {reductions[index]/100:g} 小时需协商处理。"
        if task.get("minimum_delivery_percent") is None:
            reason += "核心交付尚未确认，不能据此认定内容可以删减。"
        suggestions.append(adjustment_info(task, index, reductions[index], reason))
    losses = {
        label: round(sum(reductions[index] for index, task in ordered if int(task["priority"]) == priority) / 100, 2)
        for label, priority in (("high", 3), ("medium", 2), ("low", 1))
    }
    warnings = []
    if high_shortfalls:
        warnings.append(f"即使先让出低、中重要度任务的可调整工时，重要任务仍至少有 {max(item['shortage_hours'] for item in high_shortfalls):g} 小时无法按原期限安排；先核对估时、增加可用时间或协商延期。")
    if unresolved:
        warnings.append("必须完整完成或最低交付底线已超过期限容量；当前取舍不能解决全部缺口，不会自动突破底线。")
    if any(item["core_unconfirmed"] for item in suggestions):
        warnings.append("部分任务未确认核心交付；工时调整仅为待协商情景，不表示老师同意缩减或延期。")
    return {
        "status": "overloaded", "critical_due_date": checkpoints[0]["due_date"],
        "critical_shortage_hours": checkpoints[0]["shortage_hours"],
        "total_reduction_hours": round(sum(reductions.values()) / 100, 2),
        "method": "priority_hours_deadline_relief", "checkpoints": checkpoints, "suggestions": suggestions,
        "priority_loss_hours": losses, "constraints_feasible": not unresolved,
        "unresolved_checkpoints": unresolved, "high_priority_shortfalls": high_shortfalls,
        "high_priority_shortfall_hours": max((item["shortage_hours"] for item in high_shortfalls), default=0),
        "warnings": warnings,
        "note": "先保护交付底线，再按高、中、低重要度依次尽量少损失工时。工时不等于内容价值；最低交付比例按线性工时近似换算，内容、缩减与延期须本人确认，系统不修改任务。",
    }
def compare_strategies(
    assessed: list[dict[str, Any]], settings: dict[str, Any], plan: dict[str, Any], *, today: date
) -> dict[str, Any]:
    """Compare extra capacity, importance preservation and maximum completion count."""
    if plan["status"] != "overloaded":
        return {"strategies": [], "recommended_strategy": None, "recommendation_reason": "当前容量足够，按原计划执行即可。"}
    indexed = [(index, task) for index, task in enumerate(assessed) if task.get("status") != "done"]
    ordered = sorted(indexed, key=lambda item: (item[1]["due_date"], -int(item[1]["priority"]), item[0]))

    def info(index: int, task: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": task.get("id"), "task_index": index, "title": task["title"],
                "priority": int(task["priority"]), "due_date": task["due_date"],
                "remaining_hours": float(task["remaining_hours"]),
                "must_complete": bool(task.get("must_complete")),
                "minimum_delivery_percent": task.get("minimum_delivery_percent")}

    def capacity(due: str) -> int:
        return round(100 * available_hours_until(
            date.fromisoformat(due), weekday_hours=float(settings["weekday_hours"]),
            weekend_hours=float(settings["weekend_hours"]), buffer_ratio=float(settings["buffer_ratio"]), today=today,
        ))

    adjustments = {item["task_index"]: item for item in plan["suggestions"]}
    important_kept = [info(index, task) for index, task in ordered if index not in adjustments]
    important = {
        "id": "protect_important", "name": "优先保住重要交付", "feasible": plan["constraints_feasible"],
        "full_completion_count": len(important_kept), "kept_tasks": important_kept,
        "adjusted_tasks": plan["suggestions"], "additional_daily_hours": 0,
        "released_hours": plan["total_reduction_hours"],
        "summary": f"保留 {len(important_kept)} 项原范围任务；另有 {len(adjustments)} 项需协商。"
                   f"高重要度原期限外工时 {plan['priority_loss_hours']['high']:g} 小时；先让出低、中重要度可调整工时，不以少调整任务数量为目标。"
                   + ("当前交付底线仍有冲突，此方案不能解决全部缺口。" if not plan["constraints_feasible"] else ""),
        "suitable_when": "优先保护重要交付；允许多调整几个较不重要任务，但缩减或延期须确认。",
        "method": plan["method"],
        "priority_loss_hours": plan["priority_loss_hours"], "requires_confirmation": bool(adjustments),
    }

    # Reserve all delivery floors first. Select full completions using only
    # residual hours/capacity; protected effort can never be popped from the heap.
    heap = []
    selected = set()
    total = 0
    floors = {index: minimum_retained_units(task) for index, task in ordered}
    for index, task in ordered:
        duration = round(float(task["remaining_hours"]) * 100) - floors[index]
        selected.add(index)
        if duration > 0:
            heapq.heappush(heap, (-duration, int(task["priority"]), float(task.get("progress_percent") or 0), index))
            total += duration
        residual_capacity = capacity(task["due_date"]) - sum(floors[other] for other, item in ordered if item["due_date"] <= task["due_date"])
        while heap and total > residual_capacity:
            negative_duration, _, _, rejected = heapq.heappop(heap)
            selected.remove(rejected)
            total += negative_duration
    count_kept = [info(index, task) for index, task in ordered if index in selected]
    count_adjusted = [
        adjustment_info(task, index, round(float(task["remaining_hours"]) * 100) - floors[index],
                        "先为所有任务预留已设交付底线，再用其余容量争取更多完整完成项；不能按原范围排入的部分须协商延期或范围调整。")
        for index, task in ordered if index not in selected
    ]
    count_plan = {
        "id": "finish_more", "name": "优先完成更多任务", "feasible": plan["constraints_feasible"],
        "full_completion_count": len(count_kept), "kept_tasks": count_kept, "adjusted_tasks": count_adjusted,
        "additional_daily_hours": 0,
        "released_hours": round(sum(item["suggested_reduction_hours"] for item in count_adjusted), 2),
        "summary": f"预留已设核心底线后，选择 {len(count_kept)} 项完整任务；{len(count_adjusted)} 项另有工时需协商。短工时不代表难度低。"
                   + ("交付底线超出容量，目前不可行。" if not plan["constraints_feasible"] else ""),
        "suitable_when": "有较多独立的小任务，希望减少积压，且被推迟的任务可以接受延期后果。",
        "method": "moore_hodgson_with_delivery_floors", "requires_confirmation": bool(count_adjusted),
    }

    buffer = max(0.0, min(float(settings["buffer_ratio"]), 0.8))
    overdue = any(date.fromisoformat(task["due_date"]) < today for _, task in ordered)
    raw_daily_needed = max(
        (checkpoint["shortage_hours"] / ((date.fromisoformat(checkpoint["due_date"]) - today).days + 1) / (1 - buffer)
         for checkpoint in plan["checkpoints"] if date.fromisoformat(checkpoint["due_date"]) >= today),
        default=0,
    )
    extra = math.ceil(raw_daily_needed * 100 - 1e-9) / 100
    final_due = max(date.fromisoformat(task["due_date"]) for _, task in ordered)
    days = max((final_due - today).days + 1, 0)
    calendar_headroom = 24.0
    for offset in range(days):
        day = today + timedelta(days=offset)
        base = float(settings["weekend_hours"] if day.weekday() >= 5 else settings["weekday_hours"])
        calendar_headroom = min(calendar_headroom, max(0, 24 - base))
    extra_limit = max(0.0, min(float(settings.get("max_extra_daily_hours", 0)), calendar_headroom))
    extra_possible = not overdue and extra <= extra_limit + 1e-9
    extra_plan = {
        "id": "extra_effort", "name": "增加投入，尽量全部完成", "feasible": extra_possible,
        "full_completion_count": len(ordered) if not overdue else None,
        "kept_tasks": [info(index, task) for index, task in ordered], "adjusted_tasks": [],
        "additional_daily_hours": extra if not overdue else None,
        "extra_daily_limit": round(extra_limit, 2),
        "extra_hours_until_last_deadline": round(extra * days, 2) if not overdue else None,
        "released_hours": 0,
        "summary": "已有逾期任务，增加未来工时无法恢复原期限，需先重设交付安排。" if overdue else
            f"从今天至最后截止日每天额外投入至少 {extra:g} 小时（仍保留缓冲）。当前可接受上限为 {extra_limit:g} 小时。",
        "suitable_when": "缺口能由你确认的额外可用时间补齐，且任务均需按原范围完成。",
        "method": "deadline_prefix_capacity_uplift",
    }

    high_important = sum(item["priority"] == 3 for item in important_kept)
    high_count = sum(item["priority"] == 3 for item in count_kept)
    count_high_loss = round(sum(item["suggested_reduction_hours"] for item in count_adjusted if item["priority"] == 3), 2)
    count_plan["priority_loss_hours"] = {label: round(sum(item["suggested_reduction_hours"] for item in count_adjusted if item["priority"] == priority), 2)
                                          for label, priority in (("high", 3), ("medium", 2), ("low", 1))}
    if extra_possible:
        recommended = "extra_effort"
        reason = f"每日增加 {extra:g} 小时即可补齐所有期限缺口，未超过你设置的额外投入上限，可以保留全部任务。"
    elif not plan["constraints_feasible"]:
        recommended = None
        reason = "现有时间无法满足必须完整完成或最低交付底线，且额外投入不足（逾期任务也无法追溯补时）；当前没有满足约束的方案。请先核实估时、增加可用时间或与相关方协商期限，系统不会自动降低底线。"
    elif len(count_kept) > len(important_kept) and high_count >= high_important and count_high_loss <= plan["priority_loss_hours"]["high"] + 1e-9:
        recommended = "finish_more"
        reason = f"额外投入方案不满足当前条件；多完成方案不增加高重要度工时损失、不减少高重要度完整完成数，还能多完整完成 {len(count_kept) - len(important_kept)} 项。"
    else:
        recommended = "protect_important"
        reason = "额外投入方案不满足当前条件；优先保住重要交付能先保护交付底线，并尽量少损失高重要度工时。不能只为增加完整完成数量而大幅压缩重要任务。"
    if any(task.get("prediction_confidence") == "low" for _, task in ordered):
        reason += " 部分估时可信度低，选择前先核对这些任务的剩余工时。"
    return {
        "strategies": [extra_plan, important, count_plan],
        "recommended_strategy": recommended, "recommendation_reason": reason,
        "assumptions": "任务均可从今天开始、互不依赖且能分段执行；所有方案保留原缓冲。交付比例到工时按线性近似；未填写核心比例不代表没有核心内容，缩减和延期均需你确认。已设置底线冲突时不宣称可行。",
    }


def process_task_batch(
    tasks: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Process all entered tasks together and return a portfolio summary."""
    today = today or date.today()
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
    tradeoff_plan = suggest_tradeoffs(assessed, settings, today=today)
    tradeoff_plan.update(compare_strategies(assessed, settings, tradeoff_plan, today=today))
    return {
        "processed_count": len(assessed),
        "active_count": len(active),
        "completed_count": sum(task.get("status") == "done" and task.get("completion_scope") != "partial" for task in assessed),
        "partial_count": sum(task.get("status") == "done" and task.get("completion_scope") == "partial" for task in assessed),
        "closed_count": len(assessed) - len(active),
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
            "tradeoff": "交付底线硬约束下，按高/中/低重要度逐级最小化原期限外工时",
            "strategy_comparison": "额外容量补齐 / 分级取舍 / Moore-Hodgson 完成数选择",
            "history_matching": "标题 TF-IDF 余弦相似度与课程匹配；局部效率向总体效率收缩",
            "buffer_applied": True,
        },
        "tradeoff_plan": tradeoff_plan,
        "next_actions": next_actions,
        "tasks": assessed,
    }
