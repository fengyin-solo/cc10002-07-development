"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class CheckSubmitPayload(BaseModel):
    """质量页为指定加工分区提交一次检查结果。"""

    rule_id: int
    job_id: int
    partition: str
    result: str  # 通过 / 未通过
    request_id: str  # 幂等键：重复上报同一检查不产生第二份记录/交付
    measured_value: str | None = None
    evidence: str | None = None
    is_recheck: bool = False  # 同一分区重验必须显式选择
    base_seq: int | None = None  # 乐观并发：晚到的旧结果不能覆盖较新结论
    confirm_complete: bool = False  # 同事务确认结果并归档分区


class RuleAdjustPayload(BaseModel):
    """规则调整：生成草稿新版本，确认后才会被新分区采用。"""

    values: dict[str, Any] = Field(default_factory=dict)
    change_note: str | None = None


class PartitionActionPayload(BaseModel):
    """ETL 页针对单个分区的动作载荷。"""

    partition: str



class MetricdefEntry(BaseModel):
    """指标口径明细结构。"""

    field_0: str | None = None  # 指标编号
    field_1: str | None = None  # 指标名称
    field_2: str | None = None  # 计算公式
    field_3: str | None = None  # 统计粒度
    field_4: str | None = None  # 时间窗口类型
    field_5: str | None = None  # 筛选条件
    field_6: str | None = None  # 精度位数
    field_7: str | None = None  # 口径版本

class DimensionEntry(BaseModel):
    """分析维度明细结构。"""

    field_0: str | None = None  # 维度编号
    field_1: str | None = None  # 维度名称
    field_2: str | None = None  # 数据来源表
    field_3: str | None = None  # 维度键列
    field_4: str | None = None  # 层级关系
    field_5: str | None = None  # 历史拉链策略
    field_6: str | None = None  # 刷新周期
    field_7: str | None = None  # 默认取值

class DatasourceEntry(BaseModel):
    """数据源配置明细结构。"""

    field_0: str | None = None  # 配置编号
    field_1: str | None = None  # 连接类型
    field_2: str | None = None  # 接入地址
    field_3: str | None = None  # 认证方式
    field_4: str | None = None  # 连接池大小
    field_5: str | None = None  # 字符编码
    field_6: str | None = None  # 查询超时秒数
    field_7: str | None = None  # 重试上限

class AggregationEntry(BaseModel):
    """聚合规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 聚合函数
    field_2: str | None = None  # 目标指标
    field_3: str | None = None  # 分组维度
    field_4: str | None = None  # 时间范围
    field_5: str | None = None  # 筛选条件
    field_6: str | None = None  # 采样比例
    field_7: str | None = None  # 缓存秒数

class DashboardEntry(BaseModel):
    """看板配置明细结构。"""

    field_0: str | None = None  # 看板编号
    field_1: str | None = None  # 看板名称
    field_2: str | None = None  # 关联指标
    field_3: str | None = None  # 图表类型
    field_4: str | None = None  # 刷新间隔秒
    field_5: str | None = None  # 布局模板
    field_6: str | None = None  # 可见范围
    field_7: str | None = None  # 分享有效期

class CohortEntry(BaseModel):
    """用户分群明细结构。"""

    field_0: str | None = None  # 分群编号
    field_1: str | None = None  # 分群名称
    field_2: str | None = None  # 筛选条件
    field_3: str | None = None  # 基准日期
    field_4: str | None = None  # 观测周期天数
    field_5: str | None = None  # 行为事件
    field_6: str | None = None  # 聚合指标
    field_7: str | None = None  # 刷新频率

class AbtestEntry(BaseModel):
    """实验方案明细结构。"""

    field_0: str | None = None  # 方案编号
    field_1: str | None = None  # 实验名称
    field_2: str | None = None  # 对照组配置
    field_3: str | None = None  # 实验组配置
    field_4: str | None = None  # 流量分配比例
    field_5: str | None = None  # 核心指标
    field_6: str | None = None  # 最小样本量
    field_7: str | None = None  # 置信水平

class ReportEntry(BaseModel):
    """报告模板明细结构。"""

    field_0: str | None = None  # 模板编号
    field_1: str | None = None  # 模板名称
    field_2: str | None = None  # 包含指标
    field_3: str | None = None  # 报告周期
    field_4: str | None = None  # 图表模板
    field_5: str | None = None  # 导出格式
    field_6: str | None = None  # 推送渠道
    field_7: str | None = None  # 接收人列表

class AlertEntry(BaseModel):
    """预警规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 关联指标
    field_2: str | None = None  # 阈值上界
    field_3: str | None = None  # 阈值下界
    field_4: str | None = None  # 比较运算符
    field_5: str | None = None  # 告警级别
    field_6: str | None = None  # 静默分钟数
    field_7: str | None = None  # 通知渠道

class DatamineEntry(BaseModel):
    """挖掘任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 输入数据集
    field_2: str | None = None  # 算法类型
    field_3: str | None = None  # 特征字段
    field_4: str | None = None  # 目标字段
    field_5: str | None = None  # 参数配置
    field_6: str | None = None  # 评估指标
    field_7: str | None = None  # 输出路径

class ApiEntry(BaseModel):
    """API服务明细结构。"""

    field_0: str | None = None  # 服务编号
    field_1: str | None = None  # 输出数据集
    field_2: str | None = None  # 查询参数定义
    field_3: str | None = None  # 响应字段映射
    field_4: str | None = None  # 缓存策略秒数
    field_5: str | None = None  # 限流阈值
    field_6: str | None = None  # 认证方式
    field_7: str | None = None  # 版本号

class EtljobEntry(BaseModel):
    """加工任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 输入源
    field_2: str | None = None  # 输出目标
    field_3: str | None = None  # 转换脚本
    field_4: str | None = None  # 依赖任务
    field_5: str | None = None  # 调度表达式
    field_6: str | None = None  # 数据分区
    field_7: str | None = None  # 失败重试次数

class QualityEntry(BaseModel):
    """质量规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 校验数据集
    field_2: str | None = None  # 目标字段
    field_3: str | None = None  # 校验表达式
    field_4: str | None = None  # 阈值下界
    field_5: str | None = None  # 阈值上界
    field_6: str | None = None  # 采样比例
    field_7: str | None = None  # 阻断级别

class DatamodelEntry(BaseModel):
    """模型定义明细结构。"""

    field_0: str | None = None  # 模型编号
    field_1: str | None = None  # 模型名称
    field_2: str | None = None  # 事实表名
    field_3: str | None = None  # 关联维度
    field_4: str | None = None  # 度量列定义
    field_5: str | None = None  # 分区字段
    field_6: str | None = None  # 增量键
    field_7: str | None = None  # 刷新周期

class ExportEntry(BaseModel):
    """导出任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 查询SQL
    field_2: str | None = None  # 导出格式
    field_3: str | None = None  # 字段列表
    field_4: str | None = None  # 分隔符
    field_5: str | None = None  # 编码格式
    field_6: str | None = None  # 压缩方式
    field_7: str | None = None  # 目标路径

class SubscribeEntry(BaseModel):
    """订阅任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 数据源标识
    field_2: str | None = None  # 订阅频率
    field_3: str | None = None  # 推送渠道
    field_4: str | None = None  # 筛选项配置
    field_5: str | None = None  # 输出字段
    field_6: str | None = None  # 生效时间
    field_7: str | None = None  # 失效时间

class LineageEntry(BaseModel):
    """血缘关系明细结构。"""

    field_0: str | None = None  # 关系编号
    field_1: str | None = None  # 上游节点
    field_2: str | None = None  # 下游节点
    field_3: str | None = None  # 来源字段
    field_4: str | None = None  # 目标字段
    field_5: str | None = None  # 转换函数
    field_6: str | None = None  # 采集批次
    field_7: str | None = None  # 关系版本

class SegmentEntry(BaseModel):
    """圈选规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 规则名称
    field_2: str | None = None  # 筛选条件组合
    field_3: str | None = None  # 人群规模上限
    field_4: str | None = None  # 更新频率
    field_5: str | None = None  # 标签体系
    field_6: str | None = None  # 圈选精度
    field_7: str | None = None  # 有效期天数
