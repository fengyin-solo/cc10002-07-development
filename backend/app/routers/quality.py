"""数据质量接口：维护质量规则、规则版本，并为加工分区提交检查结果。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    CheckSubmitPayload,
    EntryPayload,
    PageResult,
    RuleAdjustPayload,
)
from app.services.quality import QualityService

router = APIRouter(prefix="/api/quality", tags=["数据质量"])

service = QualityService()

LIST_FIELDS = ["规则编号", "校验数据集", "目标字段", "校验表达式", "阈值下界", "阈值上界", "采样比例", "阻断级别"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤数据质量列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/gate/jobs")
def gate_jobs() -> dict[str, Any]:
    """质量页选择加工任务/分区时的候选清单，含各分区当前准入状态。"""
    return {"items": service.gate_jobs()}


@router.get("/checks")
def list_checks(
    job_id: int | None = None,
    partition: str | None = None,
    rule_id: int | None = None,
) -> dict[str, Any]:
    """检查结果留痕查询：可按任务、分区、规则过滤，按时间倒序返回。"""
    items = service.list_checks(job_id=job_id, partition=partition, rule_id=rule_id)
    items = sorted(items, key=lambda row: int(row["id"]), reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条质量规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"质量规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条质量规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="质量规则已登记（v1 草稿，确认发布后生效）", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条质量规则执行提交校验、启用规则、停用规则；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/versions")
def list_rule_versions(entry_id: int) -> dict[str, Any]:
    """规则版本留档：历史版本不随调整消失，便于解释每次交付采用的口径。"""
    items = service.list_rule_versions(entry_id)
    return {"rule_id": entry_id, "items": items, "total": len(items)}


@router.post("/{entry_id}/versions", response_model=ActionResult)
def adjust_rule(entry_id: int, payload: RuleAdjustPayload) -> ActionResult:
    """调整规则：生成草稿新版本，不影响历史交付；确认发布后新分区才采用。"""
    version, message = service.adjust_rule(entry_id, payload.values, payload.change_note)
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="规则新版本已保存为草稿，确认发布后生效", entry=version)


@router.post("/{entry_id}/versions/{version}/confirm", response_model=ActionResult)
def confirm_rule_version(entry_id: int, version: int) -> ActionResult:
    """确认发布某个草稿版本：此后新上报的检查按该版本执行。"""
    row, message = service.confirm_rule_version(entry_id, version)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=f"规则 v{version} 已确认发布", entry=row)


@router.post("/checks", response_model=ActionResult)
def submit_check(payload: CheckSubmitPayload) -> ActionResult:
    """为指定加工分区提交检查结果。

    - 同一 request_id 重复上报：幂等返回首次结论，不产生第二份检查/交付；
    - 重验必须显式 is_recheck=true 且 base_seq 与最新序号一致，旧结果拒绝覆盖；
    - confirm_complete=true 时，检查确认与分区归档在同一事务内提交。
    """
    result, message = service.submit_check(payload.model_dump())
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=result["message"], entry=result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据质量清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "quality", "total": total, "items": items}
