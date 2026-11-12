"""ETL任务业务规则：任务级状态流转保留，分区准入与交付由质量准入引擎裁决。"""
from __future__ import annotations

from typing import Any

from app.services.quality_gate import GateError, gate_engine
from app.store import store

MODULE = "etljob"
REQUIRED_FIELDS = ["任务编号", "输入源", "输出目标"]
STATUS_ORDER = ["草稿", "待调度", "运行中", "已完成", "已失败"]
ACTION_RULES = {"提交调度": "待调度", "启动任务": "运行中", "终止任务": "已失败", "归档结果": "已完成"}
NEGATIVE_ACTIONS = []


class EtljobService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"加工任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于ETL任务可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "归档结果":
            # 兼容历史入口：任务级归档改为逐分区准入裁决，阻断未通过则整体拒绝，
            # 但已经交付的分区保持已完成，不被回滚、不被连带改成失败。
            gate = gate_engine.job_gate(entry_id)
            blocked = [p for p in gate["partitions"] if p["gate_status"] in ("已阻断", "已失败", "待检查")]
            if blocked:
                names = "、".join(
                    f"{p['partition']}（{p['gate_status']}）" for p in blocked
                )
                return None, f"存在未通过质量准入的分区，不能整体归档：{names}"
            for part in gate["partitions"]:
                if part["gate_status"] != "已完成":
                    gate_engine.complete_partition(entry_id, part["partition"])
            entry = store.find(MODULE, entry_id)
            return entry, "全部分区通过质量准入，任务已归档为已完成"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"加工任务已{action}"

    # ------------------------------------------------------------------
    # 分区准入视图与动作
    # ------------------------------------------------------------------
    def gate_overview(self) -> list[dict[str, Any]]:
        return gate_engine.gate_overview()

    def job_gate(self, job_id: int):
        try:
            return gate_engine.job_gate(job_id), None
        except GateError as exc:
            return None, str(exc)

    def complete_partition(self, job_id: int, partition: str):
        try:
            return gate_engine.complete_partition(job_id, partition), None
        except GateError as exc:
            return None, str(exc)

    def resume_job(self, job_id: int):
        try:
            return gate_engine.resume_job(job_id), None
        except GateError as exc:
            return None, str(exc)
