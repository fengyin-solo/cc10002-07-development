"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

质量准入相关的新表（规则版本、检查结果、分区准入状态、分区交付台账）也登记在这里，
由 ``app.services.quality_gate`` 里的准入引擎统一加锁、按事务改写。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS

# 质量准入域使用的表名：引擎做事务快照时按这份清单整体备份/回滚。
GATE_TABLES = [
    "quality",
    "etljob",
    "quality_rule_versions",
    "quality_checks",
    "etl_partitions",
    "etl_partition_deliveries",
]


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 准入域新表允许种子里没有，先兜底为空列表。
        for name in GATE_TABLES:
            self._tables.setdefault(name, [])

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def replace_table(self, module: str, rows: list[dict[str, Any]]) -> None:
        """事务回滚时整表替换，保证调用方拿到的始终是同一份最新数据。"""
        self._tables[module] = rows

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
