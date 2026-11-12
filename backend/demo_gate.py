"""质量准入业务演示（带中文解说，python -I demo_gate.py 运行）。

场景：加工任务 ETLJ-1001 有两个分区 dt=2026-11-11 / dt=2026-11-12，
规则 QUAL-W01（警告级：空值率 <= 2%）与 QUAL-B01（阻断级：行数 >= 10 万）。
依次演示：首次失败 -> 修正重验 -> 并发重复上报 -> 部分完成后继续执行，
并附重试用尽留证、规则版本演进两个补充场景。每个步骤后打印两个业务页面
（数据质量页、ETL 任务页）能看到的状态。
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.quality_gate import GateError, gate_engine  # noqa: E402
from app.services.quality import QualityService  # noqa: E402

JOB_ID = 4
RULE_WARN = 4
RULE_BLOCK = 5
P1 = "dt=2026-11-11"
P2 = "dt=2026-11-12"

quality_service = QualityService()


def line(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def submit(rule_id: int, partition: str, result: str, request_id: str, **kwargs):
    return gate_engine.submit_check({
        "rule_id": rule_id, "job_id": JOB_ID, "partition": partition,
        "result": result, "request_id": request_id, **kwargs,
    })


def show_pages() -> None:
    """打印两个业务页面视角：ETL 页分区卡片 + 质量页检查留痕 + 交付台账。"""
    gate = gate_engine.job_gate(JOB_ID)
    print("\n-- ETL 任务页：分区准入状态 --")
    for part in gate["partitions"]:
        versions = "，".join(
            f"{code} v{info['version']}({info['result']})"
            for code, info in part["rule_versions"].items()
        )
        print(f"  {part['partition']} [{part['gate_status']}] "
              f"重试 {part['retry_used']}/{part['retry_max']} | 规则版本: {versions}")
        for reason in part["blocking_reasons"]:
            print(f"      阻断原因: {reason}")
        for warning in part["warnings"]:
            print(f"      警告(可继续): {warning}")
    print("\n-- 数据质量页：检查结果留痕（只追加）--")
    for row in sorted(gate["checks"], key=lambda item: item["id"]):
        print(f"  #{row['seq']} {row['规则编号']} v{row['rule_version']} {row['partition']} "
              f"{row['result']} 实测={row['measured_value'] or '-'} "
              f"重验={'是' if row['is_recheck'] else '否'} request_id={row['request_id']}")
    print("\n-- ETL 任务页：分区交付台账（只追加，固化交付依据）--")
    if not gate["deliveries"]:
        print("  （暂无交付）")
    for delivery in gate["deliveries"]:
        basis = "，".join(
            f"{code} v{info['version']}" for code, info in delivery["rule_versions"].items()
        )
        print(f"  {delivery['partition']} 交付于 {delivery['delivered_at']}，依据: {basis}")
        for item in delivery["evidence"]:
            print(f"      证据: {item['规则编号']} v{item['rule_version']} {item['result']}"
                  f" 实测={item['measured_value'] or '-'} {item['evidence'] or ''}")


# --------------------------------------------------------------- 场景一
line("场景一：首次失败——阻断级未通过不能归档，警告级可继续，其他分区不连带")
submit(RULE_WARN, P1, "通过", "demo-101", measured_value="0.01")
submit(RULE_BLOCK, P1, "未通过", "demo-102", measured_value="81234",
       evidence="上游漏数，仅 81234 行 < 10 万")
try:
    gate_engine.complete_partition(JOB_ID, P1)
except GateError as exc:
    print(f"  归档被拒：{exc}")

# P2：警告级未通过但阻断级通过，可以照常交付
submit(RULE_WARN, P2, "未通过", "demo-103", measured_value="0.035",
       evidence="空值率 3.5%，略超 2%，仅警告")
submit(RULE_BLOCK, P2, "通过", "demo-104", measured_value="126540")
gate_engine.complete_partition(JOB_ID, P2)
show_pages()

# --------------------------------------------------------------- 场景二
line("场景二：修正重验——必须显式选择并携带 base_seq，前后结果都保留")
print("  不声明重验直接上报 ->", end=" ")
try:
    submit(RULE_BLOCK, P1, "通过", "demo-201")
except GateError as exc:
    print(f"被拒：{exc}")
print("  先制造第 2 次失败（重验 base_seq=1），再演示旧结果晚到：")
submit(RULE_BLOCK, P1, "未通过", "demo-202", measured_value="76500",
       is_recheck=True, base_seq=1, evidence="重跑仍只有 76500 行")
try:
    submit(RULE_BLOCK, P1, "通过", "demo-203", is_recheck=True, base_seq=1)
except GateError as exc:
    print(f"  晚到旧结果被拒：{exc}")
submit(RULE_BLOCK, P1, "通过", "demo-204", measured_value="118760",
       is_recheck=True, base_seq=2, evidence="补齐漏数，行数 118760 >= 10 万")
gate_engine.complete_partition(JOB_ID, P1)
show_pages()

# --------------------------------------------------------------- 场景三
line("场景三：并发重复上报——同一 request_id 只产生一份检查、不产生第二份交付")
barrier = threading.Barrier(5)
payload_out: list[dict] = []


def worker(_idx: int) -> None:
    barrier.wait()
    payload_out.append(submit(
        RULE_WARN, P1, "通过", "demo-dup", measured_value="0.008",
        is_recheck=True, base_seq=1))


threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
dup_count = sum(1 for item in payload_out if item["duplicated"])
print(f"  5 路并发同 request_id：仅 1 路新建检查，{dup_count} 路幂等返回首次结论")
print(f"  P1 交付台账数量仍为：{len(gate_engine.list_deliveries(job_id=JOB_ID))}（无第二份交付）")

# --------------------------------------------------------------- 场景四
line("场景四：部分完成后继续执行——跳过已交付分区，从失败分区接着走")
# 造一个新的两分区任务，先让 P1 阻断、P2 交付，再修复 P1 后“继续执行”
new_job = {
    "id": 6, "任务编号": "ETLJ-1002", "输入源": "ods_payment",
    "输出目标": "dws_order_summary", "数据分区": "dt=2026-11-13,dt=2026-11-14",
    "失败重试次数": "2", "retry_max": 2, "status": "运行中",
    "pending": True, "abnormal": False,
}
from app.store import store  # noqa: E402
store.rows("etljob").append(new_job)
gate_engine.ensure_partitions(new_job)
Q1, Q2 = "dt=2026-11-13", "dt=2026-11-14"
submit(RULE_WARN, Q1, "通过", "demo-401", job_id=6)
submit(RULE_BLOCK, Q1, "未通过", "demo-402", job_id=6, measured_value="52000",
       evidence="Q1 首次行数不足")
submit(RULE_WARN, Q2, "通过", "demo-403", job_id=6)
submit(RULE_BLOCK, Q2, "通过", "demo-404", job_id=6, measured_value="130210")
gate_engine.complete_partition(6, Q2)
print("  修复前继续执行 ->")
print("   ", gate_engine.resume_job(6)["message"])
submit(RULE_BLOCK, Q1, "通过", "demo-405", job_id=6, measured_value="109880",
       is_recheck=True, base_seq=1, evidence="补数后 Q1 达标")
resume = gate_engine.resume_job(6)
print(f"  修复后继续执行 -> {resume['message']}")
print(f"  跳过已交付: {resume['skipped_delivered']}，本次新交付: {resume['delivered_now']}")
gate_engine.job_gate(6)
for part in gate_engine.job_gate(6)["partitions"]:
    print(f"  {part['partition']} -> {part['gate_status']}")

# --------------------------------------------------------------- 补充一
line("补充一：重试用尽——保留全部失败证据并停止自动续做")
store.rows("etljob").append({
    "id": 7, "任务编号": "ETLJ-1003", "输入源": "ods_refund",
    "输出目标": "dws_order_summary", "数据分区": "dt=2026-11-15",
    "失败重试次数": "1", "retry_max": 1, "status": "运行中",
    "pending": True, "abnormal": False,
})
gate_engine.ensure_partitions(store.find("etljob", 7))
submit(RULE_WARN, "dt=2026-11-15", "通过", "demo-501", job_id=7)
submit(RULE_BLOCK, "dt=2026-11-15", "未通过", "demo-502", job_id=7,
       measured_value="40000", evidence="首次失败证据")
submit(RULE_BLOCK, "dt=2026-11-15", "未通过", "demo-503", job_id=7,
       measured_value="43000", is_recheck=True, base_seq=1, evidence="重试仍失败证据")
part = gate_engine._get_partition(7, "dt=2026-11-15")
print(f"  分区状态: {part['gate_status']}，重试 {part['retry_used']}/{part['retry_max']}")
print(f"  继续执行 -> {gate_engine.resume_job(7)['message']}")
print(f"  失败证据条数: {len([c for c in gate_engine.list_checks(job_id=7) if not c['passed']])}")

# --------------------------------------------------------------- 补充二
line("补充二：规则版本演进——历史交付按当时版本留档，新分区采用确认后的新版本")
version, _ = quality_service.adjust_rule(
    RULE_BLOCK, {"阈值下界": "50000", "校验表达式": "row_count >= 50000"},
    "下调行数门槛到 5 万")
print(f"  调整生成 v{version['version']} 草稿（历史交付不受影响）")
check_draft = submit(RULE_BLOCK, Q2, "通过", "demo-601", job_id=6,
                     is_recheck=True, base_seq=1)
print(f"  草稿未确认时新检查仍采用 v{check_draft['check']['rule_version']}")
quality_service.confirm_rule_version(RULE_BLOCK, 2)
check_v2 = submit(RULE_BLOCK, Q2, "通过", "demo-602", job_id=6,
                  is_recheck=True, base_seq=2)
print(f"  确认发布后新检查采用 v{check_v2['check']['rule_version']}")
old_delivery = next(d for d in gate_engine.list_deliveries(job_id=JOB_ID)
                    if d["partition"] == P1)
print(f"  历史交付 {P1} 仍固化为 "
      f"QUAL-B01 v{old_delivery['rule_versions']['QUAL-B01']['version']} 依据")

line("演示结束：两个业务页面均可还原每次交付依据（规则版本 + 检查证据 + 警告/阻断原因）")
