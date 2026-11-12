"""质量准入链路端到端验证（脚本自带断言，直接 python -I 运行）。

覆盖：首次失败 / 警告可继续 / 修正重验 / 并发重复上报 / 晚到旧结果 /
部分完成后继续执行 / 重试用尽留证停止 / 规则版本与历史留档 / 事务原子回滚。
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.quality_gate import GateError, gate_engine
from app.store import store

JOB_ID = 4
RULE_WARN = 4   # QUAL-W01 警告级
RULE_BLOCK = 5  # QUAL-B01 阻断级
P1 = "dt=2026-11-11"
P2 = "dt=2026-11-12"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -> {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def submit(rule_id: int, partition: str, result: str, request_id: str,
           job_id: int = JOB_ID, **kwargs):
    return gate_engine.submit_check({
        "rule_id": rule_id,
        "job_id": job_id,
        "partition": partition,
        "result": result,
        "request_id": request_id,
        **kwargs,
    })


def expect_gate_error(name: str, fn) -> str:
    try:
        fn()
    except GateError as exc:
        check(name, True, str(exc))
        return str(exc)
    check(name, False, "应当被准入规则拒绝")
    return ""


def part(partition: str) -> dict:
    return gate_engine._get_partition(JOB_ID, partition)


def checks(partition: str, rule_id: int) -> list[dict]:
    return [
        row for row in store.rows("quality_checks")
        if row["partition"] == partition and int(row["rule_id"]) == rule_id
    ]


# ---------------------------------------------------------------- A. 首次失败
print("\n=== A. 首次失败：阻断级未通过不能归档，警告级可继续，其他分区不连带 ===")
submit(RULE_WARN, P1, "通过", "req-a1", measured_value="0.01")
r = submit(RULE_BLOCK, P1, "未通过", "req-a2", measured_value="81234",
           evidence="分区行数 81234 < 100000，上游漏数")
check("A1 阻断失败后分区为已阻断", part(P1)["gate_status"] == "已阻断",
      part(P1)["gate_status"])
check("A2 失败尝试记 1 次重试", part(P1)["retry_used"] == 1)
check("A3 阻断原因已留痕", len(part(P1)["blocking_reasons"]) == 1,
      part(P1)["blocking_reasons"][0])
check("A4 另一分区不被连带改成失败", part(P2)["gate_status"] == "待检查",
      part(P2)["gate_status"])
expect_gate_error("A5 阻断分区不能归档为已完成",
                  lambda: gate_engine.complete_partition(JOB_ID, P1))
resume_r = gate_engine.resume_job(JOB_ID)
check("A6 继续执行停在阻断分区（此时尚无已交付分区）",
      resume_r["resumed_partition"] == P1 and resume_r["skipped_delivered"] == [],
      resume_r["message"])
# 任务级归档动作走 service 层
from app.services.etljob import EtljobService
etl_service = EtljobService()
_, msg = etl_service.run_action(JOB_ID, "归档结果")
check("A7 历史任务级归档入口被准入拦截", msg is not None and "不能整体归档" in msg, msg)

# 警告级未通过不阻断
submit(RULE_WARN, P2, "未通过", "req-a3", measured_value="0.035",
       evidence="空值率 3.5% 略高于 2%")
r = submit(RULE_BLOCK, P2, "通过", "req-a4", measured_value="126540")
check("A8 警告未通过但阻断通过 => 待交付", part(P2)["gate_status"] == "待交付",
      part(P2)["gate_status"])
check("A9 警告内容挂在分区上", len(part(P2)["warnings"]) == 1, part(P2)["warnings"][0])
r = gate_engine.complete_partition(JOB_ID, P2)
check("A10 警告级结果可继续并完成归档", part(P2)["gate_status"] == "已完成")
check("A11 交付台账固化警告与规则版本",
      r["delivery"]["warnings"] and r["delivery"]["rule_versions"]["QUAL-B01"]["version"] == 1)

# ------------------------------------------------- D（前置）. 部分完成后继续执行
print("\n=== D. 部分完成后继续执行：跳过已交付分区，从失败分区接着走 ===")
r = gate_engine.resume_job(JOB_ID)
check("D1 继续执行定位到失败分区", r["resumed_partition"] == P1, r["message"])
check("D2 已交付分区被跳过不重做", r["skipped_delivered"] == [P2], str(r["skipped_delivered"]))
check("D3 阻断未解除前不会自动交付其他未完成分区", r["delivered_now"] == [])

# ------------------------------------------------------- E（重试）. 用尽与留证
print("\n=== E. 重试继续失败：用尽次数保留证据并停止自动续做 ===")
r = submit(RULE_BLOCK, P1, "未通过", "req-e1", measured_value="76500",
           is_recheck=True, base_seq=1, evidence="重跑后仍只有 76500 行")
check("E1 第二次阻断失败升级为已失败", part(P1)["gate_status"] == "已失败",
      part(P1)["gate_status"])
check("E2 重试用量达到上限 2", part(P1)["retry_used"] == 2)
check("E3 两次失败证据都保留", len(checks(P1, RULE_BLOCK)) == 2
      and all(row["evidence"] for row in checks(P1, RULE_BLOCK)))
r = gate_engine.resume_job(JOB_ID)
check("E4 用尽后自动续做停止", r["stopped"] is True, r["message"])
expect_gate_error("E5 已失败分区仍不能归档",
                  lambda: gate_engine.complete_partition(JOB_ID, P1))

# ---------------------------------------------------------------- B. 修正重验
print("\n=== B. 修正重验：必须显式选择，前后结果都保留，人工修正可恢复交付 ===")
expect_gate_error("B1 未声明重验被拒绝",
                  lambda: submit(RULE_BLOCK, P1, "通过", "req-b1"))
expect_gate_error("B2 晚到的旧结果(base_seq 落后)不能覆盖新结论",
                  lambda: submit(RULE_BLOCK, P1, "通过", "req-b2",
                                 is_recheck=True, base_seq=1))
r = submit(RULE_BLOCK, P1, "通过", "req-b3", measured_value="118760",
           is_recheck=True, base_seq=2, evidence="补齐漏数后行数 118760")
check("B3 显式重验通过后回到待交付", part(P1)["gate_status"] == "待交付",
      part(P1)["gate_status"])
check("B4 重验前后三份结论全部留痕",
      [row["result"] for row in checks(P1, RULE_BLOCK)] == ["未通过", "未通过", "通过"])
r = gate_engine.complete_partition(JOB_ID, P1)
check("B5 修正后分区归档完成", part(P1)["gate_status"] == "已完成")
gate = gate_engine.job_gate(JOB_ID)
check("B6 两个分区都交付后任务整体已完成",
    gate["job"]["status"] == "已完成", gate["job"]["status"])
check("B7 交付台账共 2 份、各自独立", len(gate["deliveries"]) == 2)

# ---------------------------------------------------------------- C. 并发重复
print("\n=== C. 并发重复上报：同一 request_id 只产生一份检查、不产生第二份交付 ===")
barrier = threading.Barrier(5)
results: list[dict] = []
errors: list[str] = []


def worker(idx: int) -> None:
    barrier.wait()
    try:
        out = submit(RULE_WARN, P1, "通过", "req-c-dup",
                     measured_value="0.008", is_recheck=True, base_seq=1)
        results.append(out)
    except GateError as exc:
        errors.append(str(exc))


threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
dup_rows = [row for row in store.rows("quality_checks") if row["request_id"] == "req-c-dup"]
check("C1 同 request_id 并发只落 1 条检查", len(dup_rows) == 1, f"实际 {len(dup_rows)} 条")
check("C2 其余请求拿到幂等首次结论", len(results) == 5 and sum(1 for x in results if x["duplicated"]) == 4)
p1_deliveries = [d for d in store.rows("etl_partition_deliveries") if d["partition"] == P1]
check("C3 重复上报不产生第二份交付", len(p1_deliveries) == 1)

# 并发下的陈旧序号：当前 W01/P1 最新 seq=2，携带 base_seq=1 必须失败
expect_gate_error("C4 陈旧 base_seq 的晚到结果被拒绝",
                  lambda: submit(RULE_WARN, P1, "未通过", "req-c-stale",
                                 is_recheck=True, base_seq=1))
stale_rows = [row for row in store.rows("quality_checks") if row["request_id"] == "req-c-stale"]
check("C5 被拒结果没有落库", len(stale_rows) == 0)

# ---------------------------------------------------------------- F. 规则版本
print("\n=== F. 规则版本：历史交付按当时版本留档，新版本确认后才被采用 ===")
# 先存证：现有两份交付里 B01 全部固化为 v1
before = gate_engine.list_deliveries(job_id=JOB_ID)
check("F1 历史交付固化 v1 依据",
      all(d["rule_versions"]["QUAL-B01"]["version"] == 1 for d in before))
from app.services.quality import QualityService
quality_service = QualityService()
v, msg = quality_service.adjust_rule(RULE_BLOCK, {"阈值下界": "50000",
                                                  "校验表达式": "row_count >= 50000"},
                                     "下调行数门槛至 5 万")
check("F2 调整生成 v2 草稿", v is not None and v["version"] == 2 and v["status"] == "草稿", msg)
# 草稿未确认：新检查仍采用 v1
r = submit(RULE_BLOCK, P2, "通过", "req-f-draft", measured_value="126540",
           is_recheck=True, base_seq=1)
check("F3 草稿未确认前检查仍采用已确认 v1", r["check"]["rule_version"] == 1)
v, msg = quality_service.confirm_rule_version(RULE_BLOCK, 2)
check("F4 确认发布 v2", v is not None and v["status"] == "已确认", msg)
r = submit(RULE_BLOCK, P2, "通过", "req-f-v2", measured_value="126540",
           is_recheck=True, base_seq=2)
check("F5 确认后新检查采用 v2", r["check"]["rule_version"] == 2)
after = gate_engine.list_deliveries(job_id=JOB_ID)
check("F6 历史交付口径不被规则调整改写",
      all(d["rule_versions"]["QUAL-B01"]["version"] == 1 for d in after))
p2_b_checks = checks(P2, RULE_BLOCK)
check("F7 同一分区重验前后结果与版本同时保留",
      [(row["rule_version"], row["result"]) for row in p2_b_checks] == [(1, "通过"), (1, "通过"), (2, "通过")])

# ---------------------------------------------------------------- G. 事务原子性
print("\n=== G. 事务一致性：确认结果与完成分区必须同时成功，失败整体回滚 ===")
# 构造一个待交付分区：新任务走懒加载分区
job_rows = store.rows("etljob")
new_job = {
    "id": max(int(row["id"]) for row in job_rows) + 1,
    "任务编号": "ETLJ-G999", "输入源": "src_g", "输出目标": "dws_order_summary",
    "数据分区": "dt=2026-11-13", "失败重试次数": "1", "retry_max": 1,
    "status": "运行中", "pending": True, "abnormal": False,
}
job_rows.append(new_job)
gate_engine.ensure_partitions(new_job)
submit(RULE_WARN, "dt=2026-11-13", "通过", "req-g1", job_id=new_job["id"])
submit(RULE_BLOCK, "dt=2026-11-13", "通过", "req-g2", job_id=new_job["id"])
checks_before = len(store.rows("quality_checks"))
deliveries_before = len(store.rows("etl_partition_deliveries"))

original = gate_engine._append_delivery
def boom(_part):  # 模拟归档环节故障
    raise RuntimeError("模拟归档存储故障")
gate_engine._append_delivery = boom  # type: ignore[method-assign]
try:
    expect_g_error = False
    try:
        gate_engine.submit_check({
            "rule_id": RULE_WARN, "job_id": new_job["id"],
            "partition": "dt=2026-11-13", "result": "通过", "request_id": "req-g3",
            "is_recheck": True, "base_seq": 1, "confirm_complete": True,
        })
    except RuntimeError:
        expect_g_error = True
    check("G1 归档故障时整个事务抛出", expect_g_error)
finally:
    gate_engine._append_delivery = original  # type: ignore[method-assign]
check("G2 检查结果随事务一起回滚（未落库）",
      len(store.rows("quality_checks")) == checks_before)
check("G3 没有留下半成品交付",
      len(store.rows("etl_partition_deliveries")) == deliveries_before)
# 恢复后同参数重试可以一次成功（结果确认 + 归档同事务）
r = gate_engine.submit_check({
    "rule_id": RULE_WARN, "job_id": new_job["id"],
    "partition": "dt=2026-11-13", "result": "通过", "request_id": "req-g3",
    "is_recheck": True, "base_seq": 1, "confirm_complete": True,
})
check("G4 恢复后确认与归档同事务成功",
      r["delivery"] is not None and r["partition"]["gate_status"] == "已完成", r["message"])

print("\n========================================")
if failures:
    print(f"存在 {len(failures)} 个失败断言：{failures}")
    raise SystemExit(1)
print("全部质量准入场景验证通过")
