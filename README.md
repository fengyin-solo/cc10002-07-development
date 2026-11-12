# 数据指标分析平台

面向指标口径定义、多维度聚合分析、AB实验评估、数据服务发布与数据应用交付的数据指标分析平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metricdef` | 指标口径 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、数据来源表 |
| 数据源连接 | `datasource` | 数据源配置 | 配置编号、连接类型、接入地址 |
| 聚合查询 | `aggregation` | 聚合规则 | 规则编号、聚合函数、目标指标 |
| 分析看板 | `dashboard` | 看板配置 | 看板编号、看板名称、关联指标 |
| 分群分析 | `cohort` | 用户分群 | 分群编号、分群名称、筛选条件 |
| AB实验 | `abtest` | 实验方案 | 方案编号、实验名称、对照组配置 |
| 分析报告 | `report` | 报告模板 | 模板编号、模板名称、包含指标 |
| 指标预警 | `alert` | 预警规则 | 规则编号、关联指标、阈值上界 |
| 数据挖掘 | `datamine` | 挖掘任务 | 任务编号、输入数据集、算法类型 |
| 数据API | `api` | API服务 | 服务编号、输出数据集、查询参数定义 |
| ETL任务 | `etljob` | 加工任务 | 任务编号、输入源、输出目标 |
| 数据质量 | `quality` | 质量规则 | 规则编号、校验数据集、目标字段 |
| 数据建模 | `datamodel` | 模型定义 | 模型编号、模型名称、事实表名 |
| 数据导出 | `export` | 导出任务 | 任务编号、查询SQL、导出格式 |
| 数据订阅 | `subscribe` | 订阅任务 | 任务编号、数据源标识、订阅频率 |
| 数据血缘 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 人群圈选 | `segment` | 圈选规则 | 规则编号、规则名称、筛选条件组合 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 质量准入：数据质量检查跨模块控制 ETL 分区交付

数据质量页为指定加工分区提交检查结果，ETL 任务页按检查结论裁决分区能否归档为已完成。
核心实现集中在 `backend/app/services/quality_gate.py`（`QualityGateEngine`），
质量、ETL 两个服务都通过它操作，规则如下：

- **准入级别**：警告级未通过只告警、可继续交付；阻断级未通过则分区置为「已阻断」，
  不能归档，其他分区独立计算、不被连带改成失败。
- **事务一致性**：准入域写操作在全局锁内执行，配合整表深拷贝快照实现
  「确认检查结果」与「完成分区」同事务提交，任一步失败整体回滚，不留半成品。
- **幂等与并发**：检查上报必须带 `request_id`，重复上报返回首次结论，不产生第二份
  检查或交付；同一「规则 × 分区」按 `seq` 递增，重验必须显式 `is_recheck=true` 且
  `base_seq` 等于最新序号，晚到的旧结果拒绝覆盖。
- **重试与续做**：阻断失败累加自动重试，用尽后分区升级为「已失败」并保留全部失败
  证据、停止自动续做；「继续执行」跳过已交付分区，从第一个未完成分区接着走。
- **规则版本**：规则调整生成草稿新版本，确认发布后新分区才采用；历史检查与交付台账
  固化当时的规则版本快照，规则后续调整不改变历史交付口径；同一分区重验前后结果都保留。

### 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/quality/checks` | 为分区提交检查（支持幂等、重验、同事务归档 `confirm_complete`） |
| GET | `/api/quality/checks` | 检查结果留痕查询 |
| GET/POST | `/api/quality/{id}/versions` | 规则版本留档 / 调整生成草稿新版本 |
| POST | `/api/quality/{id}/versions/{v}/confirm` | 确认发布规则版本 |
| GET | `/api/etljob/gate/overview` | 全任务分区准入总览（规则版本、阻断原因、交付台账） |
| GET | `/api/etljob/{id}/gate` | 单任务分区准入详情 |
| POST | `/api/etljob/{id}/partitions/complete` | 分区归档（受准入裁决，重复归档幂等） |
| POST | `/api/etljob/{id}/resume` | 继续执行：跳过已交付分区，阻断/失败处停下 |

### 验证与演示

```bash
cd backend
.venv/bin/python -I e2e_gate.py    # 34 项断言：失败/重验/并发/续做/版本/事务回滚
.venv/bin/python -I demo_gate.py   # 带中文解说的完整业务演示
```

演示固定使用两个分区（`dt=2026-11-11`、`dt=2026-11-12`）、一条警告规则
（`QUAL-W01`）和一条阻断规则（`QUAL-B01`），覆盖首次失败、修正重验、
并发重复上报、部分完成后继续执行，两个业务页面均可还原每次交付依据。


领域：数据工程
子方向：数据分析与数据应用
