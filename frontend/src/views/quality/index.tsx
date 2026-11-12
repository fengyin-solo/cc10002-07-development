import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/quality'
const columns = [
  "规则编号",
  "校验数据集",
  "目标字段",
  "校验表达式",
  "阈值下界",
  "阈值上界",
  "采样比例",
  "阻断级别"
]
const actions = [
  "提交校验",
  "启用规则",
  "停用规则"
]

type Row = Record<string, string | number | null>
type CheckRow = Record<string, string | number | boolean | null>
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
  rule_versions: Record<string, { version: number; seq: number; level: string; result: string; measured_value?: string }>
}
type GateJob = {
  job_id: number
  任务编号: string
  输出目标: string
  status: string
  partitions: PartRow[]
}
type VersionRow = Record<string, string | number | null>

function Badge({ text }: { text: string }) {
  return <span className={`badge badge-${text}`}>{text}</span>
}

export default function QualityView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [infoMessage, setInfoMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [tab, setTab] = useState<'gate' | 'rules' | 'checks'>('gate')

  const [gateJobs, setGateJobs] = useState<GateJob[]>([])
  const [checks, setChecks] = useState<CheckRow[]>([])
  const [drawer, setDrawer] = useState<null | { rule?: Row; jobId?: number; partition?: string }>(null)
  const [versionDrawer, setVersionDrawer] = useState<null | { rule: Row; versions: VersionRow[] }>(null)

  const reloadRules = useCallback(async () => {
    const query = new URLSearchParams(filters).toString()
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('质量规则列表读取失败')
    const payload = await response.json()
    setRows(payload.items ?? [])
    setTotal(payload.total ?? 0)
  }, [filters])

  const reloadGate = useCallback(async () => {
    const response = await request(`${ENDPOINT}/gate/jobs`)
    if (!response.ok) throw new Error('分区准入视图读取失败')
    const payload = await response.json()
    setGateJobs(payload.items ?? [])
  }, [])

  const reloadChecks = useCallback(async () => {
    const response = await request(`${ENDPOINT}/checks`)
    if (!response.ok) throw new Error('检查留痕读取失败')
    const payload = await response.json()
    setChecks(payload.items ?? [])
  }, [])

  const reloadAll = useCallback(async () => {
    setErrorMessage('')
    try {
      await Promise.all([reloadRules(), reloadGate(), reloadChecks()])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据质量页面读取失败')
    }
  }, [reloadRules, reloadGate, reloadChecks])

  useEffect(() => {
    void reloadAll()
  }, [reloadAll])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '数据质量动作未生效')
      setInfoMessage(payload.message)
      await reloadAll()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据质量操作失败')
    }
  }

  async function openVersions(rule: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${rule.id}/versions`)
      if (!response.ok) throw new Error('规则版本读取失败')
      const payload = await response.json()
      setVersionDrawer({ rule, versions: payload.items ?? [] })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '规则版本读取失败')
    }
  }

  const enabledRules = useMemo(
    () => rows.filter((row) => row.status === '已启用'),
    [rows],
  )

  return (
    <section className="page" data-module="quality">
      <header className="page-head">
        <div>
          <h2>数据质量管理 · 质量准入</h2>
          <p className="page-desc">
            为指定加工分区提交检查结果：警告级可继续交付，阻断级未通过则分区不能归档；
            检查确认与分区归档在同一事务提交，重复上报幂等，重验需显式选择并保留前后结论。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据质量清单
          </button>
        </div>
      </header>

      <div className="tabs">
        <button className={`tab ${tab === 'gate' ? 'active' : ''}`} type="button" onClick={() => setTab('gate')}>
          分区准入与检查上报
        </button>
        <button className={`tab ${tab === 'rules' ? 'active' : ''}`} type="button" onClick={() => setTab('rules')}>
          质量规则与版本
        </button>
        <button className={`tab ${tab === 'checks' ? 'active' : ''}`} type="button" onClick={() => setTab('checks')}>
          检查结果留痕（{checks.length}）
        </button>
      </div>

      {infoMessage && <p className="ok-text">{infoMessage}</p>}
      {errorMessage && <p className="error-text">{errorMessage}</p>}

      {tab === 'gate' && (
        <div className="gate-section">
          <h3>加工分区质量准入</h3>
          {gateJobs.map((job) => (
            <div key={job.job_id} style={{ marginBottom: 16 }}>
              <p className="muted-text">
                {job.任务编号} · 输出目标 {job.输出目标} · 任务状态 {job.status}
              </p>
              <div className="gate-grid">
                {job.partitions.map((part) => (
                  <article key={part.id} className="partition-card">
                    <h4>
                      <span>{part.partition}</span>
                      <Badge text={part.gate_status} />
                    </h4>
                    <dl>
                      <dt>重试用量</dt>
                      <dd>{part.retry_used} / {part.retry_max}</dd>
                      <dt>规则版本</dt>
                      <dd>
                        {Object.entries(part.rule_versions).map(([code, info]) => (
                          <span key={code} style={{ marginRight: 8 }}>
                            {code} v{info.version} <Badge text={info.result} />
                          </span>
                        ))}
                      </dd>
                    </dl>
                    {part.blocking_reasons.length > 0 && (
                      <ul className="reason-list">
                        {part.blocking_reasons.map((reason) => <li key={reason}>{reason}</li>)}
                      </ul>
                    )}
                    {part.warnings.length > 0 && (
                      <ul className="warn-list">
                        {part.warnings.map((reason) => <li key={reason}>警告：{reason}</li>)}
                      </ul>
                    )}
                    <div className="row-actions" style={{ marginTop: 8 }}>
                      {enabledRules.map((rule) => (
                        <button
                          key={String(rule.id)}
                          className="link"
                          type="button"
                          onClick={() => setDrawer({ rule, jobId: job.job_id, partition: part.partition })}
                        >
                          上报 {String(rule.规则编号)}
                        </button>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ))}
          {gateJobs.length === 0 && <p className="muted-text">暂无加工任务</p>}
        </div>
      )}

      {tab === 'rules' && (
        <>
          <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reloadRules() }}>
            <label className="filter-item">
              <span>规则编号</span>
              <input
                value={filters.规则编号 ?? ''}
                placeholder="按规则编号检索"
                onChange={(event) => setFilters((prev) => ({ ...prev, 规则编号: event.target.value }))}
              />
            </label>
            <button className="btn" type="submit">查询</button>
            <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
          </form>
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((column) => <th key={column}>{column}</th>)}
                <th>当前版本</th>
                <th>可执行动作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row.id)}>
                  {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                  <td>v{String(row.current_version ?? 0)}</td>
                  <td className="row-actions">
                    {actions.map((action) => (
                      <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                        {action}
                      </button>
                    ))}
                    <button className="link" type="button" onClick={() => void openVersions(row)}>
                      版本管理
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td className="empty-state" colSpan={columns.length + 2}>暂无数据质量数据</td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}

      {tab === 'checks' && (
        <table className="data-table">
          <thead>
            <tr>
              <th>检查时间</th>
              <th>规则</th>
              <th>版本</th>
              <th>任务分区</th>
              <th>序号</th>
              <th>级别</th>
              <th>结果</th>
              <th>实测值</th>
              <th>重验</th>
              <th>证据</th>
              <th>request_id</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((row) => (
              <tr key={String(row.id)}>
                <td>{String(row.checked_at ?? '')}</td>
                <td>{String(row.规则编号 ?? '')}</td>
                <td>v{String(row.rule_version ?? '')}</td>
                <td>{String(row.任务编号 ?? '')} / {String(row.partition ?? '')}</td>
                <td>#{String(row.seq ?? '')}</td>
                <td><Badge text={String(row.level ?? '')} /></td>
                <td><Badge text={String(row.result ?? '')} /></td>
                <td>{String(row.measured_value ?? '—')}</td>
                <td>{row.is_recheck ? '是' : '首次'}</td>
                <td>{String(row.evidence ?? '—')}</td>
                <td className="muted-text">{String(row.request_id ?? '')}</td>
              </tr>
            ))}
            {checks.length === 0 && (
              <tr><td className="empty-state" colSpan={11}>暂无检查结果，可在“分区准入”页为分区上报</td></tr>
            )}
          </tbody>
        </table>
      )}

      <footer className="page-foot">
        <span>共 {total} 条质量规则、{checks.length} 条检查留痕</span>
      </footer>

      {drawer && (
        <CheckDrawer
          rule={drawer.rule as Row}
          defaultJobId={drawer.jobId as number}
          defaultPartition={drawer.partition as string}
          gateJobs={gateJobs}
          onClose={() => setDrawer(null)}
          onDone={async (message: string) => {
            setDrawer(null)
            setInfoMessage(message)
            await reloadAll()
          }}
          onError={setErrorMessage}
        />
      )}

      {versionDrawer && (
        <VersionDrawer
          rule={versionDrawer.rule}
          versions={versionDrawer.versions}
          onClose={() => setVersionDrawer(null)}
          onChanged={async () => {
            setVersionDrawer(null)
            await reloadAll()
          }}
          onError={setErrorMessage}
        />
      )}
    </section>
  )
}

function CheckDrawer(props: {
  rule: Row
  defaultJobId: number
  defaultPartition: string
  gateJobs: GateJob[]
  onClose: () => void
  onDone: (message: string) => void
  onError: (message: string) => void
}) {
  const { rule, defaultJobId, defaultPartition, gateJobs } = props
  const [result, setResult] = useState('通过')
  const [measuredValue, setMeasuredValue] = useState('')
  const [evidence, setEvidence] = useState('')
  const [isRecheck, setIsRecheck] = useState(false)
  const [confirmComplete, setConfirmComplete] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  // 默认 base_seq 取该规则在目标分区的最新序号，避免手工填错导致晚到结果被拒
  const currentPart = useMemo(() => {
    const job = gateJobs.find((item) => item.job_id === defaultJobId)
    return job?.partitions.find((item) => item.partition === defaultPartition)
  }, [gateJobs, defaultJobId, defaultPartition])
  const latestSeq = useMemo(() => {
    const info = currentPart?.rule_versions[String(rule.规则编号)]
    return info?.seq ?? 0
  }, [currentPart, rule])
  const [baseSeq, setBaseSeq] = useState(latestSeq)
  const requestId = useMemo(
    () => `req-${Date.now()}-${Math.floor(Math.random() * 10000)}`,
    [],
  )

  async function submit() {
    setSubmitting(true)
    props.onError('')
    try {
      const response = await request(`${ENDPOINT}/checks`, {
        method: 'POST',
        body: JSON.stringify({
          rule_id: Number(rule.id),
          job_id: defaultJobId,
          partition: defaultPartition,
          result,
          measured_value: measuredValue,
          evidence,
          request_id: requestId,
          is_recheck: isRecheck,
          base_seq: isRecheck ? baseSeq : null,
          confirm_complete: confirmComplete,
        }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '检查结果提交失败')
      await props.onDone(payload.message)
    } catch (error) {
      props.onError(error instanceof Error ? error.message : '检查结果提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="drawer-mask" onClick={props.onClose}>
      <div className="drawer" onClick={(event) => event.stopPropagation()}>
        <h3>提交检查结果 · {String(rule.规则编号)}</h3>
        <p className="muted-text">
          {String(rule.校验表达式)} · 级别 <Badge text={String(rule.阻断级别 ?? '')} />
          ｜目标 {defaultPartition}（{currentPart?.gate_status}，当前最新序号 #{latestSeq}）
        </p>
        <label>
          检查结果
          <select value={result} onChange={(event) => setResult(event.target.value)}>
            <option value="通过">通过</option>
            <option value="未通过">未通过</option>
          </select>
        </label>
        <label>
          实测值
          <input value={measuredValue} placeholder="如 81234 / 0.035" onChange={(event) => setMeasuredValue(event.target.value)} />
        </label>
        <label>
          证据说明
          <textarea value={evidence} placeholder="失败原因、修复说明等，失败证据会随分区留档" onChange={(event) => setEvidence(event.target.value)} />
        </label>
        <label className="checkbox-line">
          <input type="checkbox" checked={isRecheck} onChange={(event) => setIsRecheck(event.target.checked)} />
          这是同一分区的重验（首次之后的再次上报必须勾选）
        </label>
        {isRecheck && (
          <label>
            重验基准序号 base_seq（晚于该序号的结论会拒绝覆盖）
            <input type="number" value={baseSeq} onChange={(event) => setBaseSeq(Number(event.target.value))} />
          </label>
        )}
        <label className="checkbox-line">
          <input type="checkbox" checked={confirmComplete} onChange={(event) => setConfirmComplete(event.target.checked)} />
          同事务确认结果并归档分区（阻断未通过时自动拒绝归档）
        </label>
        <p className="muted-text">幂等键 request_id：{requestId}</p>
        <div className="drawer-actions">
          <button className="btn primary" type="button" disabled={submitting} onClick={() => void submit()}>
            {submitting ? '提交中…' : '确认提交'}
          </button>
          <button className="btn ghost" type="button" onClick={props.onClose}>取消</button>
        </div>
      </div>
    </div>
  )
}

function VersionDrawer(props: {
  rule: Row
  versions: VersionRow[]
  onClose: () => void
  onError: (message: string) => void
  onChanged: () => Promise<void>
}) {
  const { rule, versions } = props
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<Record<string, string>>({
    校验表达式: String(rule.校验表达式 ?? ''),
    阈值下界: String(rule.阈值下界 ?? ''),
    阈值上界: String(rule.阈值上界 ?? ''),
    阻断级别: String(rule.阻断级别 ?? '警告'),
  })
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  async function adjust() {
    setBusy(true)
    props.onError('')
    try {
      const response = await request(`${ENDPOINT}/${rule.id}/versions`, {
        method: 'POST',
        body: JSON.stringify({ values: form, change_note: note }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '规则调整失败')
      setEditing(false)
      await props.onChanged()
    } catch (error) {
      props.onError(error instanceof Error ? error.message : '规则调整失败')
    } finally {
      setBusy(false)
    }
  }

  async function confirm(version: number) {
    setBusy(true)
    props.onError('')
    try {
      const response = await request(`${ENDPOINT}/${rule.id}/versions/${version}/confirm`, { method: 'POST' })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '版本确认失败')
      await props.onChanged()
    } catch (error) {
      props.onError(error instanceof Error ? error.message : '版本确认失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="drawer-mask" onClick={props.onClose}>
      <div className="drawer" onClick={(event) => event.stopPropagation()}>
        <h3>版本管理 · {String(rule.规则编号)}</h3>
        <p className="muted-text">历史版本永久留档；调整先产生草稿，确认发布后新分区才采用新版本。</p>
        <table className="mini-table">
          <thead>
            <tr><th>版本</th><th>状态</th><th>表达式</th><th>级别</th><th>确认时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            {versions.map((version) => (
              <tr key={String(version.id)}>
                <td>v{String(version.version)}</td>
                <td><Badge text={String(version.status)} /></td>
                <td>{String(version.校验表达式)}</td>
                <td>{String(version.阻断级别)}</td>
                <td>{String(version.confirmed_at ?? '—')}</td>
                <td>
                  {version.status === '草稿' && (
                    <button className="link" type="button" disabled={busy} onClick={() => void confirm(Number(version.version))}>
                      确认发布
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!editing ? (
          <div className="drawer-actions">
            <button className="btn primary" type="button" onClick={() => setEditing(true)}>调整规则（生成新版本）</button>
            <button className="btn ghost" type="button" onClick={props.onClose}>关闭</button>
          </div>
        ) : (
          <div style={{ marginTop: 14 }}>
            {(['校验表达式', '阈值下界', '阈值上界', '阻断级别'] as const).map((field) => (
              <label key={field}>
                {field}
                {field === '阻断级别' ? (
                  <select value={form[field]} onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}>
                    <option value="警告">警告</option>
                    <option value="阻断">阻断</option>
                  </select>
                ) : (
                  <input value={form[field]} onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))} />
                )}
              </label>
            ))}
            <label>
              变更说明
              <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="如：下调行数门槛" />
            </label>
            <div className="drawer-actions">
              <button className="btn primary" type="button" disabled={busy} onClick={() => void adjust()}>保存为草稿版本</button>
              <button className="btn ghost" type="button" onClick={() => setEditing(false)}>取消</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
