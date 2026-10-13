"""Aggregate explicit official cutoffs without persisting candidate identities."""
from __future__ import annotations

from typing import Any


OFFICIAL_CUTOFF_HEADERS = (
    "最低面试分数", "最低入围面试分数", "进入面试最低分数", "面试入围分数线",
)
CANDIDATE_NUMBER_HEADERS = ("准考证号", "准考证号码", "考生编号", "考号", "报名序号")


class InterviewRoster:
    """One cutoff per business key; inconsistent/invalid groups are excluded."""

    def __init__(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        self.source_rows = 0

    def add(self, key: str, row: dict[str, Any], row_index: int,
            candidate_number: Any, invalid: bool) -> bool:
        self.source_rows += 1
        group = self.groups.setdefault(key, {
            "row": row, "indexes": [], "candidates": set(),
            "missing_candidate": False, "invalid": False, "conflict": False,
        })
        group["indexes"].append((str(row.get("source_sheet") or ""), row_index))
        group["invalid"] = group["invalid"] or invalid
        candidate = str(candidate_number or "").strip()
        if candidate:
            group["candidates"].add(candidate)
        else:
            group["missing_candidate"] = True
        conflict = group["row"].get("min_score") != row.get("min_score")
        new_conflict = conflict and not group["conflict"]
        group["conflict"] = group["conflict"] or conflict
        return new_conflict

    def finish(self) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
        rows: list[dict[str, Any]] = []
        rejected: set[tuple[str, int]] = set()
        for group in self.groups.values():
            if group["invalid"] or group["conflict"]:
                rejected.update(group["indexes"])
                continue
            row = group["row"]
            row["interview_count"] = None if group["missing_candidate"] else len(group["candidates"])
            rows.append(row)
        return rows, rejected
