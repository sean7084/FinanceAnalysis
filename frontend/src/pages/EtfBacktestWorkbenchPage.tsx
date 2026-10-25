import { useEffect, useRef, useState } from 'react'
import {
  ApiRequestError,
  createEtfBacktest,
  deleteEtfBacktest,
  fetchEtfBacktest,
  fetchEtfBacktests,
  fetchEtfComparisonCurve,
  fetchEtfDefensiveAssets,
  fmtPct,
  pauseEtfBacktest,
  restartEtfBacktest,
  resumeEtfBacktest,
  type EtfBacktestCreatePayload,
  type EtfBacktestDto,
  type EtfBacktestSort,
  type EtfComparisonPayloadDto,
  type EtfDefensiveAssetDto,
  type Paginated,
} from '../lib/api'
import { BacktestComparisonChart } from '../components/charts/BacktestComparisonChart'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

type EtfPendingControlAction = 'NONE' | 'PAUSE' | 'RESTART' | 'DELETE'
type EtfRunControl = 'pause' | 'resume' | 'restart' | 'remove'

const RUN_PAGE_SIZE_DEFAULT = 20
const RUN_PAGE_SIZES = [10, 20, 50, 100]

// The ETF engine re-reads `pending_control_action` and writes `runtime_state` at the same
// chunk boundary, so a 5 s poll is faster than the row can change but slow enough that a
// page left open overnight is not a load test on the detail endpoint.
const ETF_DETAIL_REFRESH_INTERVAL_MS = 5_000

const ETF_TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED'])
const ETF_STATUSES = ['PENDING', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED']
const ETF_FREQUENCIES = ['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'TRIGGER']
const ETF_WEIGHTINGS = ['equal', 'score']

// The server fixes one direction per column, so there is no second click to reverse it:
// alpha, return and Sharpe lead high, drawdown leads low, created leads newest.
const SORT_COLUMNS: Array<{ key: EtfBacktestSort; labelKey: string }> = [
  { key: 'created', labelKey: 'etf.wb.colCreated' },
  { key: 'alpha', labelKey: 'etf.wb.colAlpha' },
  { key: 'return', labelKey: 'etf.wb.colReturn' },
  { key: 'sharpe', labelKey: 'etf.wb.colSharpe' },
  { key: 'drawdown', labelKey: 'etf.wb.colDrawdown' },
]

const EMPTY_RUNS: EtfBacktestDto[] = []
const EMPTY_OVERLAY_IDS: number[] = []
const EMPTY_DEFENSIVE_ASSETS: EtfDefensiveAssetDto[] = []

// The sentinel option value; anything else in the select is a real `ts_code`.
const DEFENSIVE_CUSTOM = '__custom__'

/**
 * Notional turnover in 亿 yuan, the unit a Chinese operator reads a liquidity floor in.
 *
 * 511880.SH clears the floor by orders of magnitude and 511670.SH does not, and the raw
 * yuan figure (`27583000000`) is too many digits to compare at a glance -- so the option
 * label states the scaled figure next to the code.
 */
function fmtYi(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '--'
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed / 1e8).toFixed(2)}亿` : '--'
}

function defensiveAssetLabel(asset: EtfDefensiveAssetDto, t: (key: string) => string): string {
  const parts = [
    asset.ts_code,
    asset.name,
    `${t('etf.wb.defensiveTurnover')} ${fmtYi(asset.avg_amount_20d)}`,
  ]
  if (!asset.eligible) parts.push(t('etf.wb.defensiveIneligible'))
  return parts.join(' · ')
}

/**
 * Which option the menu should show for the code that is actually in the form.
 *
 * The menu is a picker, not the source of truth -- the input beside it is -- so it has to
 * stay truthful in the two cases a controlled select normally gets wrong: an explicit
 * 'Custom code' that has not been typed yet stays selected, and a code the endpoint does
 * not list (typed by hand, or loaded before the list arrived) reads as custom rather than
 * silently snapping back to 'Server default'.
 */
function defensiveValueOf(code: string, assets: EtfDefensiveAssetDto[], customMode: boolean): string {
  if (customMode) return DEFENSIVE_CUSTOM
  if (code === '') return ''
  return assets.some((asset) => asset.ts_code === code) ? code : DEFENSIVE_CUSTOM
}

function normalizeEtfControlAction(value: unknown): EtfPendingControlAction {
  return value === 'PAUSE' || value === 'RESTART' || value === 'DELETE' ? value : 'NONE'
}

function pendingControlMessage(action: EtfPendingControlAction, t: (key: string) => string): string {
  if (action === 'PAUSE') return t('etf.wb.pendingPause')
  if (action === 'RESTART') return t('etf.wb.pendingRestart')
  if (action === 'DELETE') return t('etf.wb.pendingDelete')
  return ''
}

function numberOr(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * `cursor/total` for a run that has not finished.
 *
 * `runtime_state` carries the cursor but no session total, and `status_note` is the only
 * place the denominator is written (`chunked run in progress: N/M sessions`). Both are
 * dropped by `clear_runtime_state()` on completion, so a finished row shows no progress --
 * which is the point, because a completed run has nothing left to advance.
 */
function formatEtfProgress(run: EtfBacktestDto): string | null {
  const report = run.report
  if (!report) return null
  const state = report['runtime_state']
  if (!state || typeof state !== 'object') return null
  if (typeof report['status_note'] !== 'string') return null
  return report['status_note'] as string
}

function formatEtfRunStatus(run: EtfBacktestDto, t: (key: string) => string): string {
  const label = statusLabel(run.status, t)
  const details: string[] = []
  const progress = formatEtfProgress(run)
  if (progress) details.push(progress)
  if (run.has_stale_task_owner) details.push(t('etf.wb.deadTaskHint'))
  const action = normalizeEtfControlAction(run.pending_control_action)
  if (action !== 'NONE') details.push(pendingControlMessage(action, t))
  if (!details.length) return label
  return `${label} (${details.join(' · ')})`
}

function statusLabel(status: string, t: (key: string) => string): string {
  const key = `etf.wb.status${status}`
  const translated = t(key)
  return translated === key ? status : translated
}

function actionErrorMessage(error: unknown, t: (key: string) => string): string {
  // The guardrails all answer with a sentence the operator needs verbatim -- the 429 names
  // the cap and the setting to raise, the 400 names the status the action needs -- so the
  // server's `detail` wins over localised copy whenever it is there.
  if (error instanceof ApiRequestError && error.detail) return error.detail
  if (error instanceof ApiRequestError) return `${t('etf.wb.actionFailed')} (${error.status})`
  return t('etf.wb.actionFailed')
}

function parseIdList(value: string): number[] {
  return [...new Set(value.split(',').map((part) => Number(part.trim())).filter((id) => Number.isInteger(id) && id > 0))]
}

function isoDaysAgo(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return date.toISOString().slice(0, 10)
}

function lifecycleFlags(run: EtfBacktestDto) {
  const action = normalizeEtfControlAction(run.pending_control_action)
  const hasPendingControl = action !== 'NONE'
  const hasStaleTaskOwner = Boolean(run.has_stale_task_owner)
  const blocked = hasPendingControl && !hasStaleTaskOwner
  return {
    hasPendingControl,
    hasStaleTaskOwner,
    canPause: (run.status === 'PENDING' || run.status === 'RUNNING') && !blocked,
    canResume: run.status === 'PAUSED' && !hasPendingControl,
    canRestart: !blocked,
    canRemove: action !== 'DELETE' || hasStaleTaskOwner,
  }
}

interface LifecycleActionsProps {
  run: EtfBacktestDto
  busy: boolean
  onControl: (run: EtfBacktestDto, action: EtfRunControl) => void
}

function LifecycleActions({ run, busy, onControl }: LifecycleActionsProps) {
  const { t } = useI18n()
  const flags = lifecycleFlags(run)

  return (
    <div className="runner-weekday-chips">
      <button
        type="button"
        className="chip"
        disabled={busy || !flags.canPause}
        onClick={() => onControl(run, 'pause')}
      >
        {t('etf.wb.actionPause')}
      </button>
      <button
        type="button"
        className="chip"
        disabled={busy || !flags.canResume}
        onClick={() => onControl(run, 'resume')}
      >
        {t('etf.wb.actionResume')}
      </button>
      <button
        type="button"
        className="chip"
        disabled={busy || !flags.canRestart}
        onClick={() => onControl(run, 'restart')}
      >
        {t('etf.wb.actionRestart')}
      </button>
      <button
        type="button"
        className="chip"
        disabled={busy || !flags.canRemove}
        onClick={() => onControl(run, 'remove')}
      >
        {t('etf.wb.actionRemove')}
      </button>
    </div>
  )
}

interface DetailPanelProps {
  runId: number
  actionBusyId: number | null
  onControl: (run: EtfBacktestDto, action: EtfRunControl) => void
  /** The panel hands its own revalidation to the page, so a row-level action repaints it. */
  refreshSinkRef: { current: (() => void) | null }
}

function DetailPanel({ runId, actionBusyId, onControl, refreshSinkRef }: DetailPanelProps) {
  const { t } = useI18n()
  const [overlayDraft, setOverlayDraft] = useState('')
  const [overlayIds, setOverlayIds] = useState<number[]>(EMPTY_OVERLAY_IDS)

  const detail = useApiResource<EtfBacktestDto>(`etf/backtests/${runId}`, () => fetchEtfBacktest(runId))
  const run = detail.data

  useEffect(() => {
    refreshSinkRef.current = detail.refresh
    return () => {
      refreshSinkRef.current = null
    }
  }, [detail.refresh, refreshSinkRef])

  const status = run?.status ?? ''
  const pollActive = status === '' || !ETF_TERMINAL_STATUSES.has(status)

  useEffect(() => {
    if (!pollActive) return
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      refreshSinkRef.current?.()
    }, ETF_DETAIL_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [pollActive, refreshSinkRef])

  const curve = useApiResource<EtfComparisonPayloadDto>(
    `etf/backtests/${runId}/curve?extra=${overlayIds.join(',')}`,
    () => fetchEtfComparisonCurve(runId, overlayIds),
  )

  if (detail.loading && !run) {
    return (
      <div className="card">
        <h3>{t('etf.wb.detailTitle')}</h3>
        <p className="status">{t('common.loading')}</p>
      </div>
    )
  }
  if (!run) {
    return (
      <div className="card">
        <h3>{t('etf.wb.detailTitle')}</h3>
        <p className="status disconnected">{t('etf.wb.loadError')}</p>
      </div>
    )
  }

  const flags = lifecycleFlags(run)
  const rowBusy = actionBusyId === run.id
  // Equal to the bare status exactly when there is nothing to say, so the one line that
  // carries the cursor also carries a pending intent or a dead owner without a second block.
  const runStatusDetail = formatEtfRunStatus(run, t)
  const metrics: Array<{ label: string; value: string }> = [
    { label: t('etf.wb.metricAlpha'), value: fmtPct(run.alpha_vs_benchmark) },
    { label: t('etf.wb.metricBenchmark'), value: fmtPct(run.benchmark_return) },
    { label: t('etf.wb.metricReturn'), value: fmtPct(run.total_return) },
    { label: t('etf.wb.metricAnnualized'), value: fmtPct(run.annualized_return) },
    { label: t('etf.wb.metricSharpe'), value: run.sharpe_ratio === null ? '—' : String(numberOr(run.sharpe_ratio)) },
    { label: t('etf.wb.metricDrawdown'), value: fmtPct(run.max_drawdown) },
    { label: t('etf.wb.metricBeta'), value: run.beta_vs_benchmark === null ? '—' : String(numberOr(run.beta_vs_benchmark)) },
    { label: t('etf.wb.metricWinRate'), value: fmtPct(run.win_rate) },
    { label: t('etf.wb.metricFees'), value: numberOr(run.total_fees) === null ? '—' : `¥${numberOr(run.total_fees)?.toFixed(0)}` },
  ]

  return (
    <div className="card">
      <h3>
        {t('etf.wb.detailTitle')} #{run.id}
      </h3>
      <p className="subtitle">{run.name}</p>

      <div className="metric-grid">
        <article className="metric-card">
          <span>{t('etf.wb.colStatus')}</span>
          <strong>{statusLabel(run.status, t)}</strong>
        </article>
        {run.status === 'RUNNING' ? (
          <article className="metric-card">
            <span>{t('etf.wb.metricTaskState')}</span>
            <strong>{run.task_state || '--'}</strong>
          </article>
        ) : null}
        <article className="metric-card">
          <span>{t('etf.wb.colWindow')}</span>
          <strong>
            {run.start_date} → {run.end_date}
          </strong>
        </article>
        <article className="metric-card">
          <span>{t('etf.wb.colConfig')}</span>
          <strong>
            {run.model_type} · {run.rebalance_frequency} · {run.weighting} · {run.max_positions} · {run.momentum_lookback}
          </strong>
        </article>
      </div>

      <div className="runner-weekday-chips">
        <LifecycleActions run={run} busy={rowBusy} onControl={onControl} />
        <button type="button" className="chip" onClick={detail.refresh}>
          {t('etf.wb.actionRefresh')}
        </button>
      </div>
      {runStatusDetail !== statusLabel(run.status, t) ? (
        <p className={flags.hasStaleTaskOwner ? 'status disconnected' : 'status'}>{runStatusDetail}</p>
      ) : null}
      <p className="status">{t('etf.wb.pauseCoarseHint')}</p>

      {run.error_message ? <p className="status disconnected">{run.error_message}</p> : null}

      <div className="metric-grid">
        {metrics.map((metric) => (
          <article key={metric.label} className="metric-card">
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
          </article>
        ))}
      </div>

      <div className="runner-weekday-chips">
        <label>
          {t('etf.wb.overlayLabel')}
          <input value={overlayDraft} onChange={(event) => setOverlayDraft(event.target.value)} placeholder="84,91" />
        </label>
        <button
          type="button"
          className="chip"
          onClick={() => setOverlayIds(parseIdList(overlayDraft))}
        >
          {t('etf.wb.overlayApply')}
        </button>
        <button type="button" className="chip" onClick={curve.refresh}>
          {t('etf.wb.curveReload')}
        </button>
      </div>

      <BacktestComparisonChart
        payload={curve.data}
        loading={curve.loading}
        error={curve.error}
        unavailableMessage={t('etf.wb.curveEmpty')}
      />
    </div>
  )
}

function CreateCard({ onCreated }: { onCreated: (run: EtfBacktestDto) => void }) {
  const { t } = useI18n()
  const [form, setForm] = useState({
    name: `ui-rule-${isoDaysAgo(0)}`,
    start_date: isoDaysAgo(365 * 3),
    end_date: isoDaysAgo(0),
    rebalance_frequency: 'MONTHLY',
    // Mirrors the backend default (``ETF_MAX_POSITIONS``). Both used to say 5, and the
    // backend moved to 3 with the reference row, so a form left at 5 would launch a p5
    // run the operator never chose.
    max_positions: '3',
    momentum_lookback: '60',
    weighting: 'score',
    absolute_momentum_filter: true,
    defensive_asset: '',
    initial_capital: '',
  })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // True only while the code in the input is one the endpoint did not offer, so the
  // select can show 'Custom code' without the operator losing what they typed.
  const [defensiveCustomMode, setDefensiveCustomMode] = useState(false)

  const defensiveResource = useApiResource<EtfDefensiveAssetDto[]>(
    'etf/defensive_assets',
    () => fetchEtfDefensiveAssets(),
  )
  const defensiveAssets = defensiveResource.data ?? EMPTY_DEFENSIVE_ASSETS
  const defensiveSelectValue = defensiveValueOf(form.defensive_asset.trim(), defensiveAssets, defensiveCustomMode)

  const maxPositions = Number(form.max_positions)
  const lookback = Number(form.momentum_lookback)
  const capital = form.initial_capital.trim() === '' ? null : Number(form.initial_capital)
  const valid =
    form.name.trim().length > 0 &&
    form.start_date.length === 10 &&
    form.end_date.length === 10 &&
    form.start_date < form.end_date &&
    Number.isInteger(maxPositions) &&
    maxPositions > 0 &&
    Number.isInteger(lookback) &&
    lookback > 0 &&
    (capital === null || (Number.isFinite(capital) && capital > 0))

  async function submit() {
    if (!valid || busy) return
    setBusy(true)
    setMessage(null)
    setError(null)
    const payload: EtfBacktestCreatePayload = {
      name: form.name.trim(),
      start_date: form.start_date,
      end_date: form.end_date,
      rebalance_frequency: form.rebalance_frequency as EtfBacktestCreatePayload['rebalance_frequency'],
      max_positions: maxPositions,
      momentum_lookback: lookback,
      absolute_momentum_filter: form.absolute_momentum_filter,
      weighting: form.weighting as EtfBacktestCreatePayload['weighting'],
    }
    if (form.defensive_asset.trim()) payload.defensive_asset = form.defensive_asset.trim()
    if (capital !== null) payload.initial_capital = capital
    try {
      const run = await createEtfBacktest(payload)
      // Neither "created" nor "already running" can be claimed here: the server answers 202
      // for a new row and 200 with the existing row for a duplicate fingerprint, and the
      // response body is the same serializer shape either way.
      setMessage(t('etf.wb.createAccepted'))
      onCreated(run)
    } catch (caught) {
      setError(actionErrorMessage(caught, t))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h3>{t('etf.wb.createTitle')}</h3>
      <p className="subtitle">{t('etf.wb.createDesc')}</p>
      <div className="stack-list">
        <label>
          {t('etf.wb.fieldName')}
          <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </label>
        <label>
          {t('etf.wb.fieldStart')}
          <input type="date" value={form.start_date} onChange={(event) => setForm({ ...form, start_date: event.target.value })} />
        </label>
        <label>
          {t('etf.wb.fieldEnd')}
          <input type="date" value={form.end_date} onChange={(event) => setForm({ ...form, end_date: event.target.value })} />
        </label>
        <label>
          {t('etf.wb.fieldFrequency')}
          <select
            value={form.rebalance_frequency}
            onChange={(event) => setForm({ ...form, rebalance_frequency: event.target.value })}
          >
            {ETF_FREQUENCIES.map((frequency) => (
              <option key={frequency} value={frequency}>
                {frequency}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('etf.wb.fieldWeighting')}
          <select value={form.weighting} onChange={(event) => setForm({ ...form, weighting: event.target.value })}>
            {ETF_WEIGHTINGS.map((weighting) => (
              <option key={weighting} value={weighting}>
                {weighting}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('etf.wb.fieldMaxPositions')}
          <input value={form.max_positions} onChange={(event) => setForm({ ...form, max_positions: event.target.value })} />
        </label>
        <label>
          {t('etf.wb.fieldLookback')}
          <input value={form.momentum_lookback} onChange={(event) => setForm({ ...form, momentum_lookback: event.target.value })} />
        </label>
        <label>
          {t('etf.wb.fieldAbsoluteMomentum')}
          <input
            type="checkbox"
            checked={form.absolute_momentum_filter}
            onChange={(event) => setForm({ ...form, absolute_momentum_filter: event.target.checked })}
          />
        </label>
        {/* The defensive sleeve parks in a money-market ETF, and the only reason an
            operator cannot find theirs in this list is that it is not rankable, so the
            endpoint offers every candidate with its 20-day turnover and the liquidity
            floor it did or did not clear. The free-text input below stays live: a code
            typed by hand wins over the menu. */}
        <label>
          {t('etf.wb.fieldDefensive')}
          <select
            value={defensiveSelectValue}
            onChange={(event) => {
              const next = event.target.value
              if (next === DEFENSIVE_CUSTOM) {
                setDefensiveCustomMode(true)
                return
              }
              setDefensiveCustomMode(false)
              setForm({ ...form, defensive_asset: next })
            }}
          >
            <option value="">{t('etf.wb.defensiveDefault')}</option>
            {defensiveAssets.map((asset) => (
              <option key={asset.ts_code} value={asset.ts_code}>
                {defensiveAssetLabel(asset, t)}
              </option>
            ))}
            <option value={DEFENSIVE_CUSTOM}>{t('etf.wb.defensiveCustomOption')}</option>
          </select>
        </label>
        <label>
          {t('etf.wb.fieldDefensiveCustom')}
          <input
            value={form.defensive_asset}
            placeholder="511880.SH"
            onChange={(event) => {
              const next = event.target.value
              setForm({ ...form, defensive_asset: next })
              setDefensiveCustomMode(next.trim() !== '' && !defensiveAssets.some((asset) => asset.ts_code === next.trim()))
            }}
          />
        </label>
        {defensiveResource.error ? <p className="status disconnected">{t('etf.wb.defensiveLoadError')}</p> : null}
        <label>
          {t('etf.wb.fieldInitialCapital')}
          <input
            value={form.initial_capital}
            placeholder="480000"
            onChange={(event) => setForm({ ...form, initial_capital: event.target.value })}
          />
        </label>
      </div>
      <div className="runner-weekday-chips">
        <button type="button" className="chip" disabled={busy || !valid} onClick={submit}>
          {busy ? t('common.loading') : t('etf.wb.createSubmit')}
        </button>
      </div>
      {!valid ? <p className="status">{t('etf.wb.createInvalid')}</p> : null}
      {message ? <p className="status">{message}</p> : null}
      {error ? <p className="status disconnected">{error}</p> : null}
    </div>
  )
}

export function EtfBacktestWorkbenchPage() {
  const { t } = useI18n()

  const [sort, setSort] = useState<EtfBacktestSort>('created')
  const [statusFilter, setStatusFilter] = useState('')
  const [weightingFilter, setWeightingFilter] = useState('')
  const [nameDraft, setNameDraft] = useState('')
  const [nameFilter, setNameFilter] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(RUN_PAGE_SIZE_DEFAULT)
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [actionBusyId, setActionBusyId] = useState<number | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const listKey =
    `etf/backtests?sort=${sort}&status=${statusFilter}&weighting=${weightingFilter}` +
    `&name=${nameFilter}&page=${page}&size=${pageSize}`
  const list = useApiResource<Paginated<EtfBacktestDto>>(listKey, () =>
    fetchEtfBacktests({
      sort,
      status: statusFilter || undefined,
      weighting: weightingFilter || undefined,
      name: nameFilter || undefined,
      page,
      limit: pageSize,
    }),
  )

  const runs = list.data?.results ?? EMPTY_RUNS
  const totalCount = list.data?.count ?? 0
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize))
  const clampedPage = Math.min(page, totalPages)

  const detailRefreshSink = useRef<(() => void) | null>(null)

  async function handleControl(run: EtfBacktestDto, action: EtfRunControl) {
    if (actionBusyId !== null) return
    setActionBusyId(run.id)
    setActionError(null)
    setActionMessage(null)
    try {
      const response =
        action === 'pause'
          ? await pauseEtfBacktest(run.id)
          : action === 'resume'
            ? await resumeEtfBacktest(run.id)
            : action === 'restart'
              ? await restartEtfBacktest(run.id)
              : await deleteEtfBacktest(run.id)
      // Every accepted action returns the server's own sentence, which is the only place
      // the chunk-boundary delay is stated -- surfacing it verbatim beats localising "Done".
      setActionMessage(response?.message ?? t('etf.wb.actionRemoved'))
      if (action === 'remove' && selectedRunId === run.id) {
        setSelectedRunId(null)
        detailRefreshSink.current = null
      }
      list.refresh()
      detailRefreshSink.current?.()
    } catch (caught) {
      setActionError(actionErrorMessage(caught, t))
    } finally {
      setActionBusyId(null)
    }
  }

  function changeSort(next: EtfBacktestSort) {
    setSort(next)
    setPage(1)
  }

  return (
    <section>
      <header className="page-header">
        <h2>{t('etf.wb.title')}</h2>
        <p>{t('etf.wb.desc')}</p>
      </header>

      <CreateCard
        onCreated={(run) => {
          setSelectedRunId(run.id)
          list.refresh()
        }}
      />

      <div className="card">
        <h3>{t('etf.wb.listTitle')}</h3>
        <div className="runner-weekday-chips">
          <label>
            {t('etf.wb.filterStatus')}
            <select
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value)
                setPage(1)
              }}
            >
              <option value="">{t('etf.wb.filterAny')}</option>
              {ETF_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {statusLabel(status, t)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t('etf.wb.filterWeighting')}
            <select
              value={weightingFilter}
              onChange={(event) => {
                setWeightingFilter(event.target.value)
                setPage(1)
              }}
            >
              <option value="">{t('etf.wb.filterAny')}</option>
              {ETF_WEIGHTINGS.map((weighting) => (
                <option key={weighting} value={weighting}>
                  {weighting}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t('etf.wb.filterName')}
            <input
              value={nameDraft}
              placeholder="grid-rule"
              onChange={(event) => setNameDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  setNameFilter(nameDraft.trim())
                  setPage(1)
                }
              }}
            />
          </label>
          <button
            type="button"
            className="chip"
            onClick={() => {
              setNameFilter(nameDraft.trim())
              setPage(1)
            }}
          >
            {t('etf.wb.filterApply')}
          </button>
          <button type="button" className="chip" onClick={list.refresh}>
            {t('etf.wb.actionRefresh')}
          </button>
        </div>

        {actionMessage ? <p className="status">{actionMessage}</p> : null}
        {actionError ? <p className="status disconnected">{actionError}</p> : null}
        {list.error && !list.data ? <p className="status disconnected">{t('etf.wb.loadError')}</p> : null}
        <p className="status">{t('etf.wb.sortHint')}</p>

        {list.loading && !list.data ? (
          <TableSkeleton columns={9} rows={6} />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('etf.wb.colId')}</th>
                  <th>{t('etf.wb.colName')}</th>
                  {SORT_COLUMNS.map((column) => (
                    <th key={column.key}>
                      <button
                        type="button"
                        className="table-sort-button"
                        onClick={() => changeSort(column.key)}
                      >
                        {t(column.labelKey)}
                        {sort === column.key ? ' ↓' : ''}
                      </button>
                    </th>
                  ))}
                  <th>{t('etf.wb.colConfig')}</th>
                  <th>{t('etf.wb.colStatus')}</th>
                  <th>{t('etf.wb.colActions')}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const rowBusy = actionBusyId !== null
                  return (
                    <tr
                      key={run.id}
                      className={selectedRunId === run.id ? 'row-selected' : ''}
                      onClick={() => setSelectedRunId(run.id)}
                    >
                      <td>#{run.id}</td>
                      <td>{run.name}</td>
                      <td>{run.created_at.slice(0, 10)}</td>
                      <td>{run.status === 'COMPLETED' ? fmtPct(run.alpha_vs_benchmark) : '--'}</td>
                      <td>{run.status === 'COMPLETED' ? fmtPct(run.total_return) : '--'}</td>
                      <td>{run.status === 'COMPLETED' ? String(numberOr(run.sharpe_ratio) ?? '--') : '--'}</td>
                      <td>{run.status === 'COMPLETED' ? fmtPct(run.max_drawdown) : '--'}</td>
                      <td>
                        {run.model_type} · {run.rebalance_frequency} · {run.weighting}
                      </td>
                      <td>{formatEtfRunStatus(run, t)}</td>
                      <td onClick={(event) => event.stopPropagation()}>
                        <LifecycleActions run={run} busy={rowBusy} onControl={handleControl} />
                      </td>
                    </tr>
                  )
                })}
                {!runs.length ? (
                  <tr>
                    <td colSpan={10}>{t('etf.wb.listEmpty')}</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}

        <div className="table-pagination">
          <label>
            {t('etf.wb.entriesPerPage')}
            <select
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value))
                setPage(1)
              }}
            >
              {RUN_PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="chip"
            disabled={clampedPage <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            {t('etf.wb.prev')}
          </button>
          <span className="status">
            {t('etf.wb.page')} {clampedPage}/{totalPages}
          </span>
          <button
            type="button"
            className="chip"
            disabled={clampedPage >= totalPages}
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
          >
            {t('etf.wb.next')}
          </button>
        </div>
      </div>

      {selectedRunId === null ? (
        <div className="card">
          <h3>{t('etf.wb.detailTitle')}</h3>
          <p className="status">{t('etf.wb.detailEmpty')}</p>
        </div>
      ) : (
        <DetailPanel
          key={selectedRunId}
          runId={selectedRunId}
          actionBusyId={actionBusyId}
          onControl={handleControl}
          refreshSinkRef={detailRefreshSink}
        />
      )}
    </section>
  )
}
