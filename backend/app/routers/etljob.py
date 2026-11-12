"""ETL任务接口：任务级动作保留，并开放分区准入视图、分区归档与继续执行。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult, PartitionActionPayload
from app.services.etljob import EtljobService

router = APIRouter(prefix="/api/etljob", tags=["ETL任务"])

service = EtljobService()

LIST_FIELDS = ["任务编号", "输入源", "输出目标", "转换脚本", "依赖任务", "调度表达式", "数据分区", "失败重试次数"]
STATUSES = ["草稿", "待调度", "运行中", "已完成", "已失败"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="草稿、待调度、运行中、已完成、已失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤ETL任务列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/gate/overview")
def gate_overview() -> dict[str, Any]:
    """分区准入总览：每个分区采用的规则版本、阻断原因、警告与交付台账。"""
    return {"items": service.gate_overview()}


@router.get("/{entry_id}/gate")
def job_gate(entry_id: int) -> dict[str, Any]:
    """单个任务的分区准入详情，供 ETL 页还原每次交付依据。"""
    gate, message = service.job_gate(entry_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=message)
    return gate


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条加工任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"加工任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条加工任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="加工任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """任务级动作；其中“归档结果”受质量准入约束，阻断未通过的分区不能归档。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/partitions/complete", response_model=ActionResult)
def complete_partition(entry_id: int, payload: PartitionActionPayload) -> ActionResult:
    """把单个分区归档为已完成；阻断级未通过、重试用尽或未检查完都会被拒绝。"""
    result, message = service.complete_partition(entry_id, payload.partition)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=result["message"], entry=result)


@router.post("/{entry_id}/resume", response_model=ActionResult)
def resume_job(entry_id: int) -> ActionResult:
    """继续执行：跳过已交付分区，从第一个失败/未完成分区接着走；用尽次数则停止。"""
    result, message = service.resume_job(entry_id)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=result["message"], entry=result)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出ETL任务清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "etljob", "total": total, "items": items}
