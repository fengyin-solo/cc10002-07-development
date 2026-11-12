import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/etljob'
const columns = [
  "任务编号",
  "输入源",
  "输出目标",
  "转换脚本",
  "依赖任务",
  "调度表达式",
  "数据分区",
  "失败重试次数"
]
const actions = [
  "提交调度",
  "启动任务",
  "终止任务",
  "归档结果"
]

type Row = Record<string, string | number | null>
type RuleVersionInfo = {
  version: number
  seq: number
  level: string
  result: string
  measured_value?: string
  checked_at?: string | null
}
type PartRow = {
  id: number
  job_id: number
  任务编号: string
  partition: string
  output_target: string
  gate_status: string
  retry_used: number
  retry_max: number
  blocking_reasons: string[]
  warnings: string[]
  rule_versions: Record<string, RuleVersionInfo>
}
type Delivery = {
  id: number
  partition: string
  delivered_at: string
  rule_versions: Record<string, RuleVersionInfo>
  warnings: string[]
  evidence: Array<Record<string, string | number | null>>
}
type GateJob = {
  job_id: number
  任务编号: string
  输出目标: string
  status: string
  partitions: PartRow[]
  deliveries: Delivery[]
}

function Badge({ text }: { text: string }) {
  return <span className={`badge badge-${text}`}>{text}</span>
}

export default function EtljobView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [tab, setTab] = useState<'gate' | 'jobs'>('gate')
  const [gateJobs, setGateJobs] = useState<GateJob[]>([])
  const [openJob, setOpenJob] = useState<number | null>(null)
  const [busy, setBusy] = useState<number | string | null>(null)

  const reloadJobs = useCallback(async () => {
    const query = new URLSearchParams(filters).toString()
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('加工任务列表读取失败')
    const payload = await response.json()
    setRows(payload.items ?? [])
    setTotal(payload.total ?? 0)
  }, [filters])

  const reloadGate = useCallback(async () => {
    const response = await request(`${ENDPOINT}/gate/overview`)
    if (!response.ok) throw new Error('分区准入总览读取失败')
    const payload = await response.json()
    setGateJobs(payload.items ?? [])
  }, [])

  const reloadAll = useCallback(async () => {
    setErrorMessage('')
    try {
      await Promise.all([reloadJobs(), reloadGate()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'ETL任务页面读取失败')
    }
  }, [reloadJobs, reloadGate])

  useEffect(() => {
    void reloadAll()
  }, [reloadAll])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setBusy(Number(row.id))
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || 'ETL任务动作未生效')
      setInfoMessage(payload.message)
      await reloadAll()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'ETL任务操作失败')
    } finally {
      setBusy(null)
    }
  }

  async function completePartition(jobId: number, partition: string) {
    setErrorMessage('')
    setBusy(`${jobId}-${partition}`)
    try {
      const response = await request(`${ENDPOINT}/${jobId}/partitions/complete`, {
        method: 'POST',
        body: JSON.stringify({ partition }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '分区归档失败')
      setInfoMessage(payload.message)
      await reloadAll()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分区归档失败')
    } finally {
      setBusy(null)
    }
  }

  async function resume(jobId: number) {
    setErrorMessage('')
    setBusy(`resume-${jobId}`)
    try {
      const response = await request(`${ENDPOINT}/${jobId}/resume`, { method: 'POST' })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '继续执行失败')
      setInfoMessage(payload.message)
      await reloadAll()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '继续执行失败')
    } finally {
      setBusy(null)
    }
  }

  const summary = useMemo(() => {
    const parts = gateJobs.flatMap((job) => job.partitions)
    return {
      total: parts.length,
      done: parts.filter((part) => part.gate_status === '已完成').length,
      blocked: parts.filter((part) => part.gate_status === '已阻断').length,
      failed: parts.filter((part) => part.gate_status === '已失败').length,
    }
  }, [gateJobs])

  return (
    <section className="page" data-module="etljob">
      <header className="page-head">
        <div>
          <h2>ETL任务管理 · 分区质量准入</h2>
          <p className="page-desc">
            每个分区展示采用的规则版本与阻断原因：警告级可继续，阻断级未通过不能归档；
            继续执行从失败分区接着走、跳过已交付分区，重试耗尽保留证据并停止自动续做。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出ETL任务清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">分区总数</span><strong className="stat-value">{summary.total}</strong></article>
        <article className="stat-card"><span className="stat-label">已完成交付</span><strong className="stat-value">{summary.done}</strong></article>
        <article className="stat-card"><span className="stat-label">阻断中</span><strong className="stat-value">{summary.blocked}</strong></article>
        <article className="stat-card"><span className="stat-label">已失败（停做）</span><strong className="stat-value">{summary.failed}</strong></article>
      </div>

      <div className="tabs">
        <button className={`tab ${tab === 'gate' ? 'active' : ''}`} type="button" onClick={() => setTab('gate')}>
          分区准入与交付台账
        </button>
        <button className={`tab ${tab === 'jobs' ? 'active' : ''}`} type="button" onClick={() => setTab('jobs')}>
          加工任务清单
        </button>
      </div>

      {infoMessage && <p className="ok-text">{infoMessage}</p>}
      {errorMessage && <p className="error-text">{errorMessage}</p>}

      {tab === 'gate' && (
        <div className="gate-section">
          {gateJobs.map((job) => (
            <div key={job.job_id} style={{ marginBottom: 18 }}>
              <h3 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>
                  {job.任务编号} · 输出 {job.输出目标} · 任务状态 <Badge text={job.status} />
                </span>
                <span>
                  <button className="btn" type="button" disabled={busy === `resume-${job.job_id}`}
                    onClick={() => void resume(job.job_id)}>
                    继续执行（跳过已交付分区）
                  </button>
                  <button className="btn ghost" type="button" style={{ marginLeft: 8 }}
                    onClick={() => setOpenJob(openJob === job.job_id ? null : job.job_id)}>
                    {openJob === job.job_id ? '收起交付台账' : `查看交付台账（${job.deliveries.length}）`}
                  </button>
                </span>
              </h3>
              <div className="gate-grid">
                {job.partitions.map((part) => (
                  <article key={part.id} className="partition-card">
                    <h4>
                      <span>{part.partition}</span>
                      <Badge text={part.gate_status} />
                    </h4>
                    <dl>
                      <dt>重试用量</dt>
                      <dd>{part.retry_used} / {part.retry_max}{part.gate_status === '已失败' ? '（已用尽，停止自动续做）' : ''}</dd>
                      <dt>采用规则</dt>
                      <dd>
                        {Object.entries(part.rule_versions).map(([code, info]) => (
                          <div key={code}>
                            {code} <strong>v{info.version}</strong>（#{info.seq}）
                            <Badge text={info.level} /> <Badge text={info.result} />
                            {info.measured_value ? <span className="muted-text"> 实测 {info.measured_value}</span> : null}
                          </div>
                        ))}
                      </dd>
                    </dl>
                    {part.blocking_reasons.length > 0 && (
                      <ul className="reason-list">
                        {part.blocking_reasons.map((reason) => <li key={reason}>阻断原因：{reason}</li>)}
                      </ul>
                    )}
                    {part.warnings.length > 0 && (
                      <ul className="warn-list">
                        {part.warnings.map((reason) => <li key={reason}>{reason}（不阻断交付）</li>)}
                      </ul>
                    )}
                    <div className="row-actions" style={{ marginTop: 8 }}>
                      <button
                        className="btn primary"
                        type="button"
                        disabled={busy === `${job.job_id}-${part.partition}` || part.gate_status === '已完成'}
                        onClick={() => void completePartition(job.job_id, part.partition)}
                      >
                        {part.gate_status === '已完成' ? '已归档' : '归档为已完成'}
                      </button>
                      <span className="muted-text">
                        去「数据质量」页为该分区上报/重验检查
                      </span>
                    </div>
                  </article>
                ))}
              </div>

              {openJob === job.job_id && (
                <div style={{ marginTop: 10 }}>
                  <h4 className="muted-text">交付台账（只追加，固化每次交付依据）</h4>
                  <table className="mini-table">
                    <thead>
                      <tr>
                        <th>分区</th>
                        <th>交付时间</th>
                        <th>采用的规则版本与结论</th>
                        <th>警告</th>
                        <th>检查证据</th>
                      </tr>
                    </thead>
                    <tbody>
                      {job.deliveries.map((delivery) => (
                        <tr key={delivery.id}>
                          <td>{delivery.partition}</td>
                          <td>{delivery.delivered_at}</td>
                          <td>
                            {Object.entries(delivery.rule_versions).map(([code, info]) => (
                              <div key={code}>{code} v{info.version} <Badge text={info.result} /></div>
                            ))}
                          </td>
                          <td>{delivery.warnings.length ? delivery.warnings.join('；') : '—'}</td>
                          <td>
                            {delivery.evidence.map((item) => (
                              <div key={String(item.check_id)}>
                                {String(item.规则编号)} v{String(item.rule_version)}
                                ：{String(item.result)}（实测 {String(item.measured_value ?? '—')}）
                                {item.evidence ? ` — ${String(item.evidence)}` : ''}
                              </div>
                            ))}
                          </td>
                        </tr>
                      ))}
                      {job.deliveries.length === 0 && (
                        <tr><td className="empty-state" colSpan={5}>该任务尚无分区交付</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {tab === 'jobs' && (
        <>
          <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reloadJobs() }}>
            <label className="filter-item">
              <span>任务编号</span>
              <input
                value={filters.任务编号 ?? ''}
                placeholder="按任务编号检索"
                onChange={(event) => setFilters((prev) => ({ ...prev, 任务编号: event.target.value }))}
              />
            </label>
            <button className="btn" type="submit">查询</button>
            <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
          </form>
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((column) => <th key={column}>{column}</th>)}
                <th>可执行动作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row.id)}>
                  {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                  <td className="row-actions">
                    {actions.map((action) => (
                      <button key={action} className="link" type="button" disabled={busy === Number(row.id)}
                        onClick={() => void runAction(action, row)}>
                        {action}
                      </button>
                    ))}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td className="empty-state" colSpan={columns.length + 1}>暂无ETL任务数据</td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      <footer className="page-foot">
        <span>共 {total} 条加工任务</span>
      </footer>
    </section>
  )
}
