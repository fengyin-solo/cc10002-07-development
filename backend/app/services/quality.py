"""数据质量业务规则：规则维护沿用状态流转，检查准入统一委托给质量准入引擎。"""
from __future__ import annotations

from typing import Any

from app.services.quality_gate import GateError, gate_engine
from app.store import store

MODULE = "quality"
REQUIRED_FIELDS = ["规则编号", "校验数据集", "目标字段"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]


class QualityService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
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
        # 新规则登记即生成 v1 草稿，确认发布后才能参与检查
        level = values.get("阻断级别") or "警告"
        entry.update({
            "校验表达式": values.get("校验表达式"),
            "阈值下界": values.get("阈值下界"),
            "阈值上界": values.get("阈值上界"),
            "采样比例": values.get("采样比例") or "1.0",
            "阻断级别": level,
            "current_version": 0,
        })
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"质量规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据质量可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "启用规则":
            # 没有已确认版本的规则不能参与检查，避免准入计算时无版本可用
            versions = gate_engine.list_rule_versions(entry_id)
            if not any(row.get("status") == "已确认" for row in versions):
                return None, "规则还没有已确认的版本，请先在版本管理中确认发布"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"质量规则已{action}"

    # ------------------------------------------------------------------
    # 质量准入：规则版本、检查结果
    # ------------------------------------------------------------------
    def list_rule_versions(self, rule_id: int) -> list[dict[str, Any]]:
        return gate_engine.list_rule_versions(rule_id)

    def adjust_rule(self, rule_id: int, values: dict[str, Any], change_note: str | None):
        try:
            return gate_engine.adjust_rule(rule_id, values, change_note), None
        except GateError as exc:
            return None, str(exc)

    def confirm_rule_version(self, rule_id: int, version: int):
        try:
            return gate_engine.confirm_rule_version(rule_id, version), None
        except GateError as exc:
            return None, str(exc)

    def submit_check(self, payload: dict[str, Any]):
        try:
            return gate_engine.submit_check(payload), None
        except GateError as exc:
            return None, str(exc)

    def list_checks(self, **filters: Any) -> list[dict[str, Any]]:
        return gate_engine.list_checks(**filters)

    def gate_jobs(self) -> list[dict[str, Any]]:
        """质量页选择分区时需要的加工任务/分区清单。"""
        return gate_engine.gate_overview()
