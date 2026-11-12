"""质量准入引擎：把数据质量检查结论与 ETL 分区交付绑成一条可审计的链路。

设计要点
--------
1. 单进程内存库 + 全局锁：所有准入域写操作串行化，配合深拷贝快照实现“要么全成、
   要么全回滚”的事务语义，确认检查结果与完成分区在同一把锁、同一事务内提交。
2. 检查结果只追加：每次上报落一条 ``quality_checks``，内联当时的规则版本快照；
   重复 ``request_id`` 直接返回首次结论，不产生第二份记录/交付。
3. 乐观版本号：同一“规则 × 分区”的检查按 ``seq`` 递增，重验必须显式声明并携带
   ``base_seq``；晚到的旧结果（base_seq 落后）拒绝覆盖较新结论。
4. 阻断级别决定准入：警告级未通过只告警、可继续交付；阻断级未通过则分区置为
   已阻断/已失败，不能归档；其他分区独立计算，不被连带。
5. 交付台账只追加：分区归档写 ``etl_partition_deliveries``，固化规则版本与检查
   依据；规则后续调整产生新版本，历史交付仍按当时版本留档。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any

from app.store import GATE_TABLES, store

LEVEL_BLOCK = "阻断"
LEVEL_WARN = "警告"

GATE_PENDING_CHECK = "待检查"
GATE_BLOCKED = "已阻断"
GATE_READY = "待交付"
GATE_DONE = "已完成"
GATE_FAILED = "已失败"

RESULT_PASS = "通过"
RESULT_FAIL = "未通过"
RESULT_WAIT = "待检查"

VERSION_CONFIRMED = "已确认"
VERSION_DRAFT = "草稿"


class GateError(Exception):
    """准入规则不满足时抛出的可读错误，路由层转成 ok=False 的动作结果。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class QualityGateEngine:
    """质量准入域的唯一写入口，quality / etljob 服务都通过它操作。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 事务原语
    # ------------------------------------------------------------------
    def _transaction(self, mutator):
        """在全局锁内执行 mutator；异常时用深拷贝快照整体回滚。

        内存库没有真正的数据库事务，这里用“整表快照 + 异常恢复”对齐语义：
        mutator 内任何一步抛错，GATE_TABLES 涉及的表全部恢复到进入时的样子，
        不会留下“检查已落库但分区没归档”这类半成品。
        """
        with self._lock:
            snapshot = {
                name: copy.deepcopy(store.rows(name)) for name in GATE_TABLES
            }
            try:
                return mutator()
            except Exception:
                for name, rows in snapshot.items():
                    store.replace_table(name, rows)
                raise

    # ------------------------------------------------------------------
    # 通用查询
    # ------------------------------------------------------------------
    def _get_rule(self, rule_id: int) -> dict[str, Any]:
        rule = store.find("quality", rule_id)
        if rule is None:
            raise GateError(f"质量规则 {rule_id} 不存在或已归档")
        return rule

    def _get_job(self, job_id: int) -> dict[str, Any]:
        job = store.find("etljob", job_id)
        if job is None:
            raise GateError(f"加工任务 {job_id} 不存在或已归档")
        return job

    def _latest_confirmed_version(self, rule_id: int) -> dict[str, Any]:
        versions = [
            row
            for row in store.rows("quality_rule_versions")
            if int(row["rule_id"]) == rule_id and row["status"] == VERSION_CONFIRMED
        ]
        if not versions:
            raise GateError("该规则还没有已确认的版本，不能提交检查")
        return max(versions, key=lambda row: int(row["version"]))

    def _applicable_rules(self, output_target: str) -> list[dict[str, Any]]:
        """适用规则：校验数据集与任务输出目标一致、已启用、且有已确认版本。"""
        confirmed_rule_ids = {
            int(row["rule_id"])
            for row in store.rows("quality_rule_versions")
            if row["status"] == VERSION_CONFIRMED
        }
        return [
            row
            for row in store.rows("quality")
            if row.get("校验数据集") == output_target
            and row.get("status") == "已启用"
            and int(row["id"]) in confirmed_rule_ids
        ]

    def _checks_of(self, job_id: int, partition: str) -> list[dict[str, Any]]:
        return [
            row
            for row in store.rows("quality_checks")
            if int(row["job_id"]) == job_id and row["partition"] == partition
        ]

    def _latest_check(self, rule_id: int, job_id: int, partition: str) -> dict[str, Any] | None:
        rows = [
            row
            for row in self._checks_of(job_id, partition)
            if int(row["rule_id"]) == rule_id
        ]
        return max(rows, key=lambda row: int(row["seq"]), default=None)

    def _delivery(self, job_id: int, partition: str) -> dict[str, Any] | None:
        for row in store.rows("etl_partition_deliveries"):
            if int(row["job_id"]) == job_id and row["partition"] == partition:
                return row
        return None

    def _next_id(self, table: str) -> int:
        return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1

    # ------------------------------------------------------------------
    # 分区装配：兼容没有分区行的历史任务，按任务上的“数据分区”懒加载
    # ------------------------------------------------------------------
    def ensure_partitions(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        job_id = int(job["id"])
        rows = [
            row
            for row in store.rows("etl_partitions")
            if int(row["job_id"]) == job_id
        ]
        if rows:
            return sorted(rows, key=lambda row: int(row["id"]))
        raw = str(job.get("数据分区") or "").strip()
        partitions = [item.strip() for item in raw.split(",") if item.strip()]
        if not partitions:
            partitions = ["default"]
        try:
            retry_max = int(job.get("retry_max") or job.get("失败重试次数") or 3)
        except (TypeError, ValueError):
            retry_max = 3
        for partition in partitions:
            store.rows("etl_partitions").append({
                "id": self._next_id("etl_partitions"),
                "job_id": job_id,
                "任务编号": job.get("任务编号"),
                "partition": partition,
                "output_target": job.get("输出目标"),
                "gate_status": GATE_PENDING_CHECK,
                "retry_used": 0,
                "retry_max": retry_max,
                "blocking_reasons": [],
                "warnings": [],
                "rule_versions": {},
                "latest_seq": 0,
                "updated_at": _now(),
            })
        return self.ensure_partitions(job)

    def _get_partition(self, job_id: int, partition: str) -> dict[str, Any]:
        for row in store.rows("etl_partitions"):
            if int(row["job_id"]) == job_id and row["partition"] == partition:
                return row
        raise GateError(f"分区 {partition} 不属于该加工任务，或任务未登记该分区")

    # ------------------------------------------------------------------
    # 分区准入状态重算：结论只取每条规则“最新一次”检查
    # ------------------------------------------------------------------
    def _recompute_partition(self, part: dict[str, Any]) -> dict[str, Any]:
        if self._delivery(int(part["job_id"]), part["partition"]) is not None:
            part["gate_status"] = GATE_DONE
            part["updated_at"] = _now()
            return part

        rules = self._applicable_rules(str(part["output_target"]))
        blocking_reasons: list[str] = []
        warnings: list[str] = []
        rule_versions: dict[str, Any] = {}
        blocking_missing = False
        blocking_failed = False

        for rule in rules:
            latest = self._latest_check(int(rule["id"]), int(part["job_id"]), part["partition"])
            code = str(rule["规则编号"])
            if latest is None:
                # 还没检查过：先挂当前已确认版本，便于 ETL 页展示“将要采用的版本”
                version = self._latest_confirmed_version(int(rule["id"]))
                rule_versions[code] = {
                    "version": int(version["version"]),
                    "seq": 0,
                    "level": rule.get("阻断级别"),
                    "result": RESULT_WAIT,
                    "checked_at": None,
                }
                if rule.get("阻断级别") == LEVEL_BLOCK:
                    blocking_missing = True
                continue

            rule_versions[code] = {
                "version": int(latest["rule_version"]),
                "seq": int(latest["seq"]),
                "level": latest["level"],
                "result": latest["result"],
                "measured_value": latest.get("measured_value"),
                "checked_at": latest.get("checked_at"),
            }
            if not bool(latest["passed"]):
                phrase = (
                    f"{code} v{latest['rule_version']}：{latest['校验表达式']} 未通过"
                    f"（实测 {latest.get('measured_value') or '未提供'}）"
                )
                if latest["level"] == LEVEL_BLOCK:
                    blocking_failed = True
                    blocking_reasons.append(phrase)
                else:
                    warnings.append(phrase)

        part["blocking_reasons"] = blocking_reasons
        part["warnings"] = warnings
        part["rule_versions"] = rule_versions

        if blocking_failed:
            # 重试已用尽：保留失败证据并停止自动续做，状态升级为已失败
            if int(part["retry_used"]) >= int(part["retry_max"]):
                part["gate_status"] = GATE_FAILED
            else:
                part["gate_status"] = GATE_BLOCKED
        elif blocking_missing:
            part["gate_status"] = GATE_PENDING_CHECK
        else:
            # 全部阻断规则已通过（或该分区本就没有阻断规则）：警告不影响交付
            part["gate_status"] = GATE_READY
        part["updated_at"] = _now()
        return part

    def _recompute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        parts = self.ensure_partitions(job)
        statuses = [part["gate_status"] for part in parts]
        if statuses and all(status == GATE_DONE for status in statuses):
            job["status"] = "已完成"
            job["pending"] = False
            job["abnormal"] = False
        elif any(status == GATE_FAILED for status in statuses):
            # 已交付分区保持已完成，只有失败分区挂起，任务整体标记已失败
            job["status"] = "已失败"
            job["pending"] = True
            job["abnormal"] = True
        elif any(status in (GATE_BLOCKED, GATE_PENDING_CHECK) for status in statuses):
            job["status"] = "运行中"
            job["pending"] = True
            job["abnormal"] = any(status == GATE_BLOCKED for status in statuses)
        else:
            job["status"] = "运行中"
            job["pending"] = True
            job["abnormal"] = False
        return job

    # ------------------------------------------------------------------
    # 规则版本：调整只产生草稿新版本，确认后才成为检查采用的版本
    # ------------------------------------------------------------------
    def list_rule_versions(self, rule_id: int) -> list[dict[str, Any]]:
        self._get_rule(rule_id)
        with self._lock:
            return [
                dict(row)
                for row in store.rows("quality_rule_versions")
                if int(row["rule_id"]) == rule_id
            ]

    def adjust_rule(self, rule_id: int, values: dict[str, Any], change_note: str | None) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            rule = self._get_rule(rule_id)
            editable = ["校验数据集", "目标字段", "校验表达式", "阈值下界", "阈值上界", "采样比例", "阻断级别"]
            updates = {key: values.get(key) for key in editable if values.get(key) is not None}
            if not updates:
                raise GateError("没有可调整的规则字段")
            if updates.get("阻断级别") not in (None, LEVEL_WARN, LEVEL_BLOCK):
                raise GateError("阻断级别只允许填写“警告”或“阻断”")
            versions = [
                row
                for row in store.rows("quality_rule_versions")
                if int(row["rule_id"]) == rule_id
            ]
            next_version = max((int(row["version"]) for row in versions), default=0) + 1
            snapshot = {key: rule.get(key) for key in editable}
            snapshot.update(updates)
            version_row = {
                "id": self._next_id("quality_rule_versions"),
                "rule_id": rule_id,
                "规则编号": rule.get("规则编号"),
                "version": next_version,
                "status": VERSION_DRAFT,
                "变更说明": change_note or f"第 {next_version} 版调整，待确认",
                "created_at": _now(),
                "confirmed_at": None,
                **snapshot,
            }
            store.rows("quality_rule_versions").append(version_row)
            # 规则行先反映草稿内容，但 current_version 仍指向已确认版本
            rule.update(updates)
            rule["pending"] = True
            return dict(version_row)

        return self._transaction(work)

    def confirm_rule_version(self, rule_id: int, version: int) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            rule = self._get_rule(rule_id)
            target = next(
                (
                    row
                    for row in store.rows("quality_rule_versions")
                    if int(row["rule_id"]) == rule_id and int(row["version"]) == version
                ),
                None,
            )
            if target is None:
                raise GateError(f"规则 {rule_id} 不存在 v{version}，无法确认")
            if target["status"] == VERSION_CONFIRMED:
                raise GateError(f"v{version} 已确认，重复确认不产生新版本")
            target["status"] = VERSION_CONFIRMED
            target["confirmed_at"] = _now()
            # 规则行与新版本对齐；历史版本原样保留，旧检查/旧交付不受影响
            for key in ["校验数据集", "目标字段", "校验表达式", "阈值下界", "阈值上界", "采样比例", "阻断级别"]:
                rule[key] = target.get(key)
            rule["current_version"] = version
            rule["status"] = "已启用"
            rule["pending"] = False
            return dict(target)

        return self._transaction(work)

    # ------------------------------------------------------------------
    # 检查结果上报：幂等 + 乐观并发 + 同事务可选交付
    # ------------------------------------------------------------------
    def submit_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            rule_id = int(payload["rule_id"])
            job_id = int(payload["job_id"])
            partition = str(payload["partition"]).strip()
            result = str(payload.get("result") or "").strip()
            request_id = str(payload.get("request_id") or "").strip()
            is_recheck = bool(payload.get("is_recheck", False))
            base_seq = payload.get("base_seq")
            confirm_complete = bool(payload.get("confirm_complete", False))

            if not request_id:
                raise GateError("request_id 必填，用于重复上报的幂等识别")
            if result not in (RESULT_PASS, RESULT_FAIL):
                raise GateError("检查结果只允许填写“通过”或“未通过”")

            rule = self._get_rule(rule_id)
            job = self._get_job(job_id)
            part = self._get_partition(job_id, partition)
            if rule.get("校验数据集") != job.get("输出目标"):
                raise GateError(
                    f"规则 {rule.get('规则编号')} 的校验数据集与任务输出目标不一致，不能对该分区上报"
                )

            # 1) 幂等：同一 request_id 重复上报，原样返回首次结论，不新增检查、不新增交付
            duplicate = next(
                (
                    row
                    for row in store.rows("quality_checks")
                    if row.get("request_id") == request_id
                ),
                None,
            )
            if duplicate is not None:
                return {
                    "duplicated": True,
                    "check": dict(duplicate),
                    "partition": dict(self._recompute_partition(part)),
                    "delivery": self._delivery(job_id, partition),
                    "message": "相同 request_id 的检查已受理过，未产生第二份记录",
                }

            # 2) 乐观并发：重验必须显式选择，且 base_seq 必须等于当前最新序号
            history = [
                row
                for row in self._checks_of(job_id, partition)
                if int(row["rule_id"]) == rule_id
            ]
            current_seq = max((int(row["seq"]) for row in history), default=0)
            if history and not is_recheck:
                raise GateError(
                    f"规则 {rule.get('规则编号')} 在该分区已有 {current_seq} 次检查结论，"
                    "再次上报必须显式选择“重验”"
                )
            if history:
                if base_seq is None:
                    raise GateError("重验必须携带 base_seq（当前最新检查序号）")
                if int(base_seq) != current_seq:
                    raise GateError(
                        f"晚到的旧结果：提交基于第 {base_seq} 次结论，当前已是第 {current_seq} 次，"
                        "旧结果不能覆盖较新结论"
                    )

            version = self._latest_confirmed_version(rule_id)
            passed = result == RESULT_PASS
            check = {
                "id": self._next_id("quality_checks"),
                "request_id": request_id,
                "rule_id": rule_id,
                "规则编号": rule.get("规则编号"),
                "job_id": job_id,
                "任务编号": job.get("任务编号"),
                "partition": partition,
                "seq": current_seq + 1,
                "base_seq": current_seq,
                "result": result,
                "passed": passed,
                "level": version.get("阻断级别"),
                "measured_value": str(payload.get("measured_value") or ""),
                "evidence": str(payload.get("evidence") or ""),
                # 规则版本快照：规则后续调整不影响本次结论的解释口径
                "rule_version": int(version["version"]),
                "校验数据集": version.get("校验数据集"),
                "目标字段": version.get("目标字段"),
                "校验表达式": version.get("校验表达式"),
                "阈值下界": version.get("阈值下界"),
                "阈值上界": version.get("阈值上界"),
                "阻断级别": version.get("阻断级别"),
                "is_recheck": is_recheck,
                "checked_at": _now(),
            }
            store.rows("quality_checks").append(check)
            part["latest_seq"] = max(int(part.get("latest_seq", 0)), check["seq"])

            # 阻断级未通过记一次自动重试消耗；预算用尽后的人工重验不再累加，
            # 但检查证据照常追加留痕
            if not passed and version.get("阻断级别") == LEVEL_BLOCK:
                if int(part.get("retry_used", 0)) < int(part.get("retry_max", 0)):
                    part["retry_used"] = int(part.get("retry_used", 0)) + 1

            self._recompute_partition(part)

            # 3) 同事务交付：确认结果与完成分区要么一起成功，要么一起回滚
            delivery = None
            message = "检查结果已确认"
            if confirm_complete:
                if part["gate_status"] == GATE_DONE:
                    delivery = self._delivery(job_id, partition)
                    message = "分区此前已归档，未产生第二份交付"
                elif part["gate_status"] == GATE_READY:
                    delivery = self._append_delivery(part)
                    message = "检查确认与分区归档已在同一事务完成"
                elif part["gate_status"] == GATE_BLOCKED:
                    message = "阻断级规则未通过，分区保持已阻断，未执行归档，失败证据已留痕"
                elif part["gate_status"] == GATE_FAILED:
                    message = "重试次数已用尽，停止自动续做，失败证据已留痕"
                else:
                    message = "仍有阻断规则尚未检查，分区保持待检查，未执行归档"

            self._recompute_job(job)
            return {
                "duplicated": False,
                "check": dict(check),
                "partition": dict(part),
                "delivery": dict(delivery) if delivery else None,
                "message": message,
            }

        return self._transaction(work)

    def _append_delivery(self, part: dict[str, Any]) -> dict[str, Any]:
        job_id = int(part["job_id"])
        # 双保险：台账只追加，已交付分区绝不产生第二份
        existed = self._delivery(job_id, part["partition"])
        if existed is not None:
            return existed
        evidence = []
        for rule in self._applicable_rules(str(part["output_target"])):
            latest = self._latest_check(int(rule["id"]), job_id, part["partition"])
            if latest is None:
                continue
            evidence.append({
                "check_id": int(latest["id"]),
                "规则编号": latest["规则编号"],
                "rule_version": int(latest["rule_version"]),
                "level": latest["level"],
                "result": latest["result"],
                "measured_value": latest.get("measured_value"),
                "校验表达式": latest.get("校验表达式"),
                "evidence": latest.get("evidence"),
                "checked_at": latest.get("checked_at"),
            })
        delivery = {
            "id": self._next_id("etl_partition_deliveries"),
            "job_id": job_id,
            "任务编号": part.get("任务编号"),
            "partition": part["partition"],
            "output_target": part.get("output_target"),
            "delivered_at": _now(),
            # 交付依据固化：采用的规则版本、警告、检查证据随台账永久留档
            "rule_versions": copy.deepcopy(part["rule_versions"]),
            "warnings": copy.deepcopy(part["warnings"]),
            "blocking_reasons": copy.deepcopy(part["blocking_reasons"]),
            "evidence": evidence,
        }
        store.rows("etl_partition_deliveries").append(delivery)
        part["gate_status"] = GATE_DONE
        part["updated_at"] = _now()
        return delivery

    # ------------------------------------------------------------------
    # 分区归档（ETL 页触发）：准入不满足直接拒绝，失败不写任何半成品
    # ------------------------------------------------------------------
    def complete_partition(self, job_id: int, partition: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            job = self._get_job(job_id)
            part = self._get_partition(job_id, partition)
            self._recompute_partition(part)
            existed = self._delivery(job_id, partition)
            if existed is not None:
                return {
                    "duplicated": True,
                    "partition": dict(part),
                    "delivery": dict(existed),
                    "message": "分区已归档，重复归档不产生第二份交付",
                }
            if part["gate_status"] == GATE_BLOCKED:
                raise GateError(
                    "阻断级检查未通过，不能归档：" + "；".join(part["blocking_reasons"])
                )
            if part["gate_status"] == GATE_FAILED:
                raise GateError(
                    "重试次数已用尽并停止自动续做，不能归档："
                    + "；".join(part["blocking_reasons"])
                )
            if part["gate_status"] == GATE_PENDING_CHECK:
                raise GateError("仍有阻断规则未完成检查，不能归档为已完成")
            delivery = self._append_delivery(part)
            self._recompute_job(job)
            return {
                "duplicated": False,
                "partition": dict(part),
                "delivery": dict(delivery),
                "message": "分区已归档为已完成，交付依据已留档",
            }

        return self._transaction(work)

    # ------------------------------------------------------------------
    # 继续执行：从第一个未交付分区接着走，已交付分区不重做
    # ------------------------------------------------------------------
    def resume_job(self, job_id: int) -> dict[str, Any]:
        """继续执行：已交付分区一律跳过；已通过准入（待交付）的分区自动归档；
        遇到阻断/失败/待检查分区立即停下，并说明停止原因。"""
        def work() -> dict[str, Any]:
            job = self._get_job(job_id)
            parts = self.ensure_partitions(job)
            for part in parts:
                self._recompute_partition(part)
            done = [part["partition"] for part in parts if part["gate_status"] == GATE_DONE]
            delivered_now: list[str] = []
            for part in parts:
                if part["gate_status"] == GATE_DONE:
                    continue
                if part["gate_status"] == GATE_READY:
                    # 已通过质量准入：接着把这个分区交付掉，不重做前面的分区
                    self._append_delivery(part)
                    delivered_now.append(part["partition"])
                    continue
                if part["gate_status"] == GATE_FAILED:
                    self._recompute_job(job)
                    return {
                        "resumed_partition": part["partition"],
                        "stopped": True,
                        "skipped_delivered": done,
                        "delivered_now": delivered_now,
                        "partition": dict(part),
                        "message": (
                            f"分区 {part['partition']} 重试已用尽，停止自动续做；"
                            "已交付分区保持已完成，失败证据已留档"
                        ),
                    }
                if part["gate_status"] == GATE_BLOCKED:
                    self._recompute_job(job)
                    return {
                        "resumed_partition": part["partition"],
                        "stopped": True,
                        "skipped_delivered": done,
                        "delivered_now": delivered_now,
                        "partition": dict(part),
                        "message": (
                            f"在阻断分区 {part['partition']} 处停下："
                            + "；".join(part["blocking_reasons"])
                        ),
                    }
                self._recompute_job(job)
                return {
                    "resumed_partition": part["partition"],
                    "stopped": True,
                    "skipped_delivered": done,
                    "delivered_now": delivered_now,
                    "partition": dict(part),
                    "message": f"分区 {part['partition']} 仍有阻断规则未检查，等待质量结果",
                }
            self._recompute_job(job)
            return {
                "resumed_partition": None,
                "stopped": False,
                "skipped_delivered": done,
                "delivered_now": delivered_now,
                "partition": None,
                "message": (
                    "继续执行完成：跳过已交付分区 "
                    + (", ".join(done) or "无")
                    + ("，本次新交付 " + ", ".join(delivered_now) if delivered_now else "")
                ),
            }

        return self._transaction(work)

    # ------------------------------------------------------------------
    # 读模型：给两个业务页面还原每次交付依据
    # ------------------------------------------------------------------
    def list_checks(self, *, job_id: int | None = None, partition: str | None = None,
                    rule_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = store.rows("quality_checks")
            if job_id is not None:
                rows = [row for row in rows if int(row["job_id"]) == job_id]
            if partition:
                rows = [row for row in rows if row["partition"] == partition]
            if rule_id is not None:
                rows = [row for row in rows if int(row["rule_id"]) == rule_id]
            return [dict(row) for row in rows]

    def list_deliveries(self, *, job_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = store.rows("etl_partition_deliveries")
            if job_id is not None:
                rows = [row for row in rows if int(row["job_id"]) == job_id]
            return [dict(row) for row in rows]

    def job_gate(self, job_id: int) -> dict[str, Any]:
        with self._lock:
            job = self._get_job(job_id)
            parts = self.ensure_partitions(job)
            for part in parts:
                self._recompute_partition(part)
            return {
                "job": dict(job),
                "partitions": [dict(part) for part in parts],
                "deliveries": self.list_deliveries(job_id=job_id),
                "checks": self.list_checks(job_id=job_id),
            }

    def gate_overview(self) -> list[dict[str, Any]]:
        """所有带分区的任务一览，供 ETL 页展示规则版本与阻断原因。"""
        with self._lock:
            result = []
            for job in store.rows("etljob"):
                parts = self.ensure_partitions(job)
                for part in parts:
                    self._recompute_partition(part)
                result.append({
                    "job_id": int(job["id"]),
                    "任务编号": job.get("任务编号"),
                    "输出目标": job.get("输出目标"),
                    "status": job.get("status"),
                    "partitions": [dict(part) for part in parts],
                    "deliveries": [
                        dict(row)
                        for row in store.rows("etl_partition_deliveries")
                        if int(row["job_id"]) == int(job["id"])
                    ],
                })
                self._recompute_job(job)
            return result


gate_engine = QualityGateEngine()
