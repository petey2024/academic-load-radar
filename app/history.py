"""Local keyword retrieval and explainable historical-task calibration."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from typing import Any


def normalize_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def title_tokens(value: Any) -> Counter[str]:
    text = normalize_text(value)
    for filler in ("完成", "提交", "进行", "开始", "我的"):
        text = text.replace(filler, " ")
    tokens = []
    for part in re.findall(r"[a-z][a-z0-9_]*|[\u4e00-\u9fff]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
            if len(part) == 1:
                tokens.append(part)
        else:
            tokens.append(part)
    return Counter(tokens)


def readable_keywords(title: Any, shared: set[str]) -> list[str]:
    """Join adjacent shared Chinese bigrams into readable common phrases."""
    phrases = []
    for part in re.findall(r"[a-z][a-z0-9_]*|[\u4e00-\u9fff]+", normalize_text(title)):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1 and part in shared:
                phrases.append(part)
            index = 0
            while index < len(part) - 1:
                if part[index:index + 2] not in shared:
                    index += 1
                    continue
                start = index
                while index < len(part) - 1 and part[index:index + 2] in shared:
                    index += 1
                phrases.append(part[start:index + 1])
        elif part in shared:
            phrases.append(part)
    return sorted(set(phrases), key=lambda phrase: (-len(phrase), phrase))[:6]


def search_tasks(tasks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Require every keyword in the title/course; rank title matches first."""
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalize_text(query))))
    if not terms:
        return list(tasks) if not query.strip() else []
    results = []
    for task in tasks:
        title = normalize_text(task.get("title"))
        course = normalize_text(task.get("course"))
        if all(term in title or term in course for term in terms):
            results.append({
                **task,
                "keyword_matches": terms,
                "search_score": sum(2 if term in title else 1 for term in terms),
            })
    return sorted(results, key=lambda task: (-task["search_score"], task.get("due_date", ""), str(task.get("id", ""))))


class HistoryMatcher:
    """TF-IDF cosine on title tokens plus an explicit same-course signal.

    Chinese bigrams keep retrieval dependency-free. Scores express lexical
    similarity, not a probability of equal difficulty. Build once per analysis.
    """

    def __init__(self, tasks: list[dict[str, Any]]):
        self.samples = [
            task for task in tasks
            if task.get("status") == "done"
            and task.get("completion_scope") != "partial"
            and float(task.get("actual_hours") or 0) > 0
            and float(task.get("estimated_hours") or 0) > 0
            and task.get("data_precision", "exact") != "rough"
        ]
        counts = [title_tokens(task.get("title")) for task in self.samples]
        frequency = Counter(token for count in counts for token in count)
        self.idf = {token: math.log((1 + len(counts)) / (1 + count)) + 1 for token, count in frequency.items()}
        self.unknown_idf = math.log(1 + len(counts)) + 1
        self.vectors = [self.vector(count) for count in counts]
        self.tokens = counts

    def vector(self, count: Counter[str]) -> dict[str, float]:
        raw = {token: (1 + math.log(amount)) * self.idf.get(token, self.unknown_idf) for token, amount in count.items()}
        norm = math.sqrt(sum(value * value for value in raw.values()))
        return {token: value / norm for token, value in raw.items()} if norm else {}

    def calibrate(self, task: dict[str, Any], global_ratio: float, global_count: int) -> dict[str, Any]:
        count = title_tokens(task.get("title"))
        vector = self.vector(count)
        course = normalize_text(task.get("course"))
        matches = []
        for historical, tokens, other in zip(self.samples, self.tokens, self.vectors):
            if task.get("id") is not None and task.get("id") == historical.get("id"):
                continue
            cosine = sum(value * other.get(token, 0) for token, value in vector.items())
            same_course = bool(course) and course == normalize_text(historical.get("course"))
            score = 0.65 * cosine + (0.35 if same_course else 0)
            if score < 0.4:
                continue
            matches.append({
                "task_id": historical.get("id"), "title": historical["title"],
                "course": historical.get("course", ""), "similarity": round(score, 3),
                "same_course": same_course, "_shared_tokens": count.keys() & tokens.keys(),
                "estimated_hours": float(historical["estimated_hours"]),
                "actual_hours": float(historical["actual_hours"]),
                "efficiency_ratio": float(historical["actual_hours"]) / float(historical["estimated_hours"]),
                "data_precision": historical.get("data_precision", "exact"),
            })
        matches.sort(key=lambda item: (-item["similarity"], str(item["task_id"])))
        matches = matches[:5]
        # Only the five displayed records need readable explanations.
        for item in matches:
            shared = readable_keywords(task.get("title"), item.pop("_shared_tokens"))
            item["matched_keywords"] = shared
            item["reason"] = ("同课程；" if item["same_course"] else "跨课程标题匹配；") + "共享关键词：" + "、".join(shared)
        if not matches:
            return {
                "efficiency_ratio": global_ratio, "history_samples": global_count,
                "similar_history": [], "history_calibration": "global" if global_count else "initial",
                "similar_history_weight": 0,
                "history_calibration_note": "未找到足够相似的可靠历史任务，使用总体历史效率。" if global_count else "没有可靠历史样本，使用初始估时。",
            }
        weighted = sorted(
            ((item["efficiency_ratio"], item["similarity"] * (0.5 if item["data_precision"] == "approx" else 1)) for item in matches),
            key=lambda pair: pair[0],
        )
        evidence = sum(weight for _, weight in weighted)
        accumulated = 0.0
        local_ratio = weighted[-1][0]
        for ratio, weight in weighted:
            accumulated += weight
            if accumulated >= evidence / 2:
                local_ratio = ratio
                break
        local_weight = evidence / (evidence + 2)
        blended = local_weight * local_ratio + (1 - local_weight) * global_ratio
        return {
            "efficiency_ratio": round(blended, 2),
            "history_samples": sum(item["similarity"] >= 0.5 for item in matches),
            "similar_history": matches, "history_calibration": "similar_blended",
            "similar_history_weight": round(local_weight, 3),
            "history_calibration_note": f"参考 {len(matches)} 项相似历史任务，局部效率权重 {local_weight:.0%}，其余使用总体效率，减轻少量样本的影响。",
        }
