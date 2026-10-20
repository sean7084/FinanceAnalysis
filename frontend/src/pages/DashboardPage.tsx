import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  fetchDashboardData,
  fetchDashboardStocks,
  type DashboardStockRowDto,
} from '../lib/api'
import {
  buildDashboardSearchParams,
  createDefaultDashboardCandidateFilters,
  dashboardCandidateLimit,
  dashboardPredictionHorizon,
  parseDashboardSearchParams,
  topNMetricHorizon,
  type CandidatePredictionSource,
  type DashboardCandidateFilters,
  type TopNMetric,
} from '../lib/dashboardCandidateFilters'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

type SortDirection = 'asc' | 'desc'
type DashboardSortField = keyof DashboardStockRowDto
type CandidateModelMetric = 'label' | 'up_probability' | 'confidence' | 'trade_score' | 'target_price' | 'stop_loss_price' | 'risk_reward_ratio' | 'suggested'

interface CandidateTableColumn {
  key: DashboardSortField
  label: string
  render: (row: DashboardStockRowDto) => string
  initialDirection?: SortDirection
}

function formatMaybeNumber(value: number | string | null | undefined, digits = 2): string {
  if (value == null || value === '') {
    return '--'
  }
  const numericValue = Number(value)
  return Number.isFinite(numericValue) ? numericValue.toFixed(digits) : String(value)
}

function formatMaybePercent(value: number | string | null | undefined): string {
  if (value == null || value === '') {
    return '--'
  }
  const numericValue = Number(value)
  return Number.isFinite(numericValue) ? `${(numericValue * 100).toFixed(1)}%` : String(value)
}

function modelField(source: CandidatePredictionSource, metric: CandidateModelMetric): DashboardSortField {
  return `${source}_${metric}` as DashboardSortField
}

function modelLabel(source: CandidatePredictionSource): string {
  if (source === 'lightgbm') return 'LightGBM'
  if (source === 'lstm') return 'LSTM'
  return 'Heuristic'
}

function rankingMetricLabel(filters: DashboardCandidateFilters, t: (key: string) => string): string {
  if (filters.candidateMode === 'trade_score' || filters.topNMetric === 'trade_score') {
    return t('trade.tradeScore')
  }
  if (filters.topNMetric === 'up_prob_3d') {
    return `${t('comparison.upProbability')} 3D`
  }
  if (filters.topNMetric === 'up_prob_30d') {
    return `${t('comparison.upProbability')} 30D`
  }
  return `${t('comparison.upProbability')} 7D`
}

function defaultCandidateSort(): { field: DashboardSortField; direction: SortDirection } {
  return { field: 'candidate_rank', direction: 'asc' }
}

function compareDashboardValues(leftValue: unknown, rightValue: unknown, direction: SortDirection): number {
  if (leftValue == null && rightValue == null) return 0
  if (leftValue == null) return 1
  if (rightValue == null) return -1
  if (typeof leftValue === 'string' && typeof rightValue === 'string') {
    return direction === 'asc' ? leftValue.localeCompare(rightValue) : rightValue.localeCompare(leftValue)
  }
  const leftNumber = Number(leftValue)
  const rightNumber = Number(rightValue)
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    return direction === 'asc' ? leftNumber - rightNumber : rightNumber - leftNumber
  }
  const leftText = String(leftValue)
  const rightText = String(rightValue)
  return direction === 'asc' ? leftText.localeCompare(rightText) : rightText.localeCompare(leftText)
}

type DashboardSummaryPayload = Awaited<ReturnType<typeof fetchDashboardData>>

// Module scope, so the "no rows yet" value keeps one identity. A fresh `[]` on
// every render makes any memo that lists it as a dependency recompute forever.
const EMPTY_DASHBOARD_ROWS: DashboardStockRowDto[] = []

export function DashboardPage() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const searchKey = searchParams.toString()
  const appliedBundle = useMemo(() => parseDashboardSearchParams(new URLSearchParams(searchKey)), [searchKey])
  const appliedFilters = appliedBundle.filters
  const appliedCandidateLimit = dashboardCandidateLimit(appliedFilters)
  const appliedPredictionHorizon = dashboardPredictionHorizon(appliedFilters)
  const activePredictionSource = appliedFilters.predictionSource
  const selectedSuggestedField = modelField(activePredictionSource, 'suggested')
  const selectedTradeScoreField = modelField(activePredictionSource, 'trade_score')
  const [draftState, setDraftState] = useState<{ key: string; filters: DashboardCandidateFilters }>({
    key: searchKey,
    filters: appliedFilters,
  })
  const initialSort = defaultCandidateSort()
  const [sortState, setSortState] = useState<{ key: string; field: DashboardSortField; direction: SortDirection }>({
    key: searchKey,
    field: initialSort.field,
    direction: initialSort.direction,
  })
  // Draft filters and sort order are stored together with the URL key they were
  // derived from, so a newly applied bundle (or a back/forward navigation) makes
  // them fall back to that bundle instead of needing an effect to re-sync.
  const draftFilters = draftState.key === searchKey ? draftState.filters : appliedFilters
  const setDraftFilters = (update: (current: DashboardCandidateFilters) => DashboardCandidateFilters) => {
    setDraftState({ key: searchKey, filters: update(draftFilters) })
  }
  const sortField = sortState.key === searchKey ? sortState.field : initialSort.field
  const sortDirection = sortState.key === searchKey ? sortState.direction : initialSort.direction

  // Two independent resources on purpose. The summary is a single cheap
  // endpoint and the candidate table is the slow one; sharing a `loading` flag
  // would hide the KPI cards behind a spinner until the slower call finished.
  const summaryResource = useApiResource<DashboardSummaryPayload>(
    'dashboard/summary',
    () => fetchDashboardData(),
  )
  const rowsResource = useApiResource<DashboardStockRowDto[]>(
    `dashboard/stocks?${appliedPredictionHorizon}/${appliedCandidateLimit}/${searchKey}`,
    () => fetchDashboardStocks({
      predictionHorizon: appliedPredictionHorizon,
      pageSize: appliedCandidateLimit,
      candidateFilters: appliedFilters,
    }),
    [appliedPredictionHorizon, appliedCandidateLimit, searchKey],
  )

  const dashboard = summaryResource.data ?? {
    macroPhase: 'N/A',
    hotConcepts: 'N/A',
    predictionSignals: 0,
    alertTriggers: 0,
    completedBacktests: 0,
    avgBottomProbability: 0,
  }
  const dashboardRows = rowsResource.data ?? EMPTY_DASHBOARD_ROWS
  const loadFailed = summaryResource.error !== null || rowsResource.error !== null

  const showHorizonSelector = draftFilters.candidateMode === 'trade_score' || draftFilters.topNMetric === 'trade_score'
  const effectiveHorizon = draftFilters.candidateMode === 'top_n'
    ? topNMetricHorizon(draftFilters.topNMetric, draftFilters.horizonDays)
    : draftFilters.horizonDays
  const reminderItems = [
    appliedBundle.reminders.sourceRunId || appliedBundle.reminders.sourceRunName
      ? {
          label: t('dash.sourceRun'),
          value: appliedBundle.reminders.sourceRunId != null
            ? `#${appliedBundle.reminders.sourceRunId} ${appliedBundle.reminders.sourceRunName ?? ''}`.trim()
            : (appliedBundle.reminders.sourceRunName ?? '--'),
        }
      : null,
    appliedBundle.reminders.entryWeekdays?.length
      ? { label: t('backtest.runnerWeekdays'), value: appliedBundle.reminders.entryWeekdays.join(', ') }
      : null,
    appliedBundle.reminders.holdingPeriodDays != null
      ? { label: t('backtest.runnerHoldingDays'), value: String(appliedBundle.reminders.holdingPeriodDays) }
      : null,
    appliedBundle.reminders.enableStopTargetExit != null
      ? {
          label: t('backtest.runnerUseStopTargetExit'),
          value: appliedBundle.reminders.enableStopTargetExit ? t('common.yes') : t('common.no'),
        }
      : null,
    appliedBundle.reminders.capitalFractionPerEntry != null
      ? {
          label: t('backtest.runnerCapitalFraction'),
          value: formatMaybeNumber(appliedBundle.reminders.capitalFractionPerEntry),
        }
      : null,
    appliedBundle.reminders.initialCapital
      ? { label: t('backtest.runnerInitialCapital'), value: formatMaybeNumber(appliedBundle.reminders.initialCapital) }
      : null,
  ].filter((item): item is { label: string; value: string } => item !== null)

  const translatedMetrics = [
    { label: t('dash.macro'), value: dashboard.macroPhase, pending: summaryResource.loading },
    { label: t('dash.hotConcepts'), value: dashboard.hotConcepts, pending: summaryResource.loading },
    { label: t('dash.predSignals'), value: `${dashboard.predictionSignals} ${t('dash.today')}`, pending: summaryResource.loading },
    { label: t('dash.alertTriggers'), value: `${dashboard.alertTriggers} ${t('dash.live')}`, pending: summaryResource.loading },
    { label: t('dash.completedBacktests'), value: String(dashboard.completedBacktests), pending: summaryResource.loading },
    { label: t('dash.avgBottomProb'), value: `${(dashboard.avgBottomProbability * 100).toFixed(1)}%`, pending: summaryResource.loading },
    {
      label: t('dash.suggestedSetups'),
      value: String(dashboardRows.filter((row) => Boolean(row[selectedSuggestedField])).length),
      pending: rowsResource.loading,
    },
    {
      label: t('dash.topTradeScore'),
      value: dashboardRows.length
        ? formatMaybeNumber(Math.max(...dashboardRows.map((row) => Number(row[selectedTradeScoreField] ?? 0))))
        : '--',
      pending: rowsResource.loading,
    },
  ]

  const candidateColumns = useMemo<CandidateTableColumn[]>(() => {
    const labelField = modelField(activePredictionSource, 'label')
    const upProbabilityField = modelField(activePredictionSource, 'up_probability')
    const riskRewardField = modelField(activePredictionSource, 'risk_reward_ratio')
    const tradeScoreField = modelField(activePredictionSource, 'trade_score')
    const targetPriceField = modelField(activePredictionSource, 'target_price')
    const stopLossField = modelField(activePredictionSource, 'stop_loss_price')
    const suggestedField = modelField(activePredictionSource, 'suggested')

    return [
      { key: 'candidate_rank', label: t('dash.rank'), initialDirection: 'asc', render: (row) => row.candidate_rank != null ? String(row.candidate_rank) : '--' },
      { key: 'asset_symbol', label: t('screener.symbol'), initialDirection: 'asc', render: (row) => row.asset_symbol },
      { key: 'asset_name', label: t('screener.name'), initialDirection: 'asc', render: (row) => row.asset_name },
      { key: 'candidate_rank_value', label: rankingMetricLabel(appliedFilters, t), render: (row) => formatMaybeNumber(row.candidate_rank_value, 4) },
      { key: labelField, label: modelLabel(activePredictionSource), initialDirection: 'asc', render: (row) => String(row[labelField] || '--') },
      { key: upProbabilityField, label: t('comparison.upProbability'), render: (row) => formatMaybePercent(row[upProbabilityField] as number | string | null | undefined) },
      { key: riskRewardField, label: t('trade.rr'), render: (row) => formatMaybeNumber(row[riskRewardField] as number | string | null | undefined) },
      { key: tradeScoreField, label: t('trade.tradeScore'), render: (row) => formatMaybeNumber(row[tradeScoreField] as number | string | null | undefined) },
      { key: targetPriceField, label: t('trade.targetPrice'), render: (row) => formatMaybeNumber(row[targetPriceField] as number | string | null | undefined) },
      { key: stopLossField, label: t('trade.stopLoss'), render: (row) => formatMaybeNumber(row[stopLossField] as number | string | null | undefined) },
      { key: suggestedField, label: t('trade.suggested'), initialDirection: 'desc', render: (row) => row[suggestedField] ? t('common.yes') : t('common.no') },
    ]
  }, [activePredictionSource, appliedFilters, t])

  const displayedRows = useMemo(() => (
    [...dashboardRows].sort((left, right) => compareDashboardValues(left[sortField], right[sortField], sortDirection))
  ), [dashboardRows, sortDirection, sortField])

  const handleSort = (column: CandidateTableColumn) => {
    if (sortField === column.key) {
      setSortState({ key: searchKey, field: column.key, direction: sortDirection === 'asc' ? 'desc' : 'asc' })
      return
    }
    setSortState({ key: searchKey, field: column.key, direction: column.initialDirection ?? 'desc' })
  }

  const applyFilters = () => {
    setSearchParams(buildDashboardSearchParams({
      filters: draftFilters,
      reminders: appliedBundle.reminders,
    }))
  }

  const resetFilters = () => {
    const defaults = createDefaultDashboardCandidateFilters()
    setDraftState({ key: searchKey, filters: defaults })
    setSearchParams(buildDashboardSearchParams({
      filters: defaults,
      reminders: {},
    }))
  }

  return (
    <section>
      <header className="page-header">
        <h2>{t('dash.title')}</h2>
        <p>{t('dash.desc')}</p>
      </header>

      <div className="metric-grid">
        {translatedMetrics.map((m) => (
          <article key={m.label} className="card metric-card">
            <span>{m.label}</span>
            <strong>{m.pending ? t('common.loading') : m.value}</strong>
          </article>
        ))}
      </div>

      {loadFailed && (
        <p className="status disconnected">
          {t('dash.loadError')}
        </p>
      )}

      <div className="card runner-card">
        <h3>{t('dash.filterTitle')}</h3>
        <p className="subtitle">{t('dash.filterDesc')}</p>
        <div className="runner-grid">
          <label>
            {t('backtest.predictionSource')}
            <select
              value={draftFilters.predictionSource}
              onChange={(event) => setDraftFilters((current) => ({
                ...current,
                predictionSource: event.target.value as DashboardCandidateFilters['predictionSource'],
              }))}
            >
              <option value="heuristic">heuristic</option>
              <option value="lightgbm">lightgbm</option>
              <option value="lstm">lstm</option>
            </select>
          </label>
          {showHorizonSelector && (
            <label>
              {t('backtest.runnerHorizon')}
              <select
                value={draftFilters.horizonDays}
                onChange={(event) => setDraftFilters((current) => ({
                  ...current,
                  horizonDays: Number(event.target.value) as DashboardCandidateFilters['horizonDays'],
                }))}
              >
                <option value={3}>3</option>
                <option value={7}>7</option>
                <option value={30}>30</option>
              </select>
            </label>
          )}
          <label>
            {t('backtest.runnerUpThreshold')}
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={draftFilters.upThreshold}
              onChange={(event) => setDraftFilters((current) => ({
                ...current,
                upThreshold: Number(event.target.value),
              }))}
            />
          </label>
          <label>
            {t('backtest.runnerCandidateMode')}
            <select
              value={draftFilters.candidateMode}
              onChange={(event) => setDraftFilters((current) => ({
                ...current,
                candidateMode: event.target.value as DashboardCandidateFilters['candidateMode'],
              }))}
            >
              <option value="top_n">{t('backtest.runnerCandidateModeTopN')}</option>
              <option value="trade_score">{t('backtest.runnerCandidateModeTradeScore')}</option>
            </select>
          </label>
          {draftFilters.candidateMode === 'top_n' && (
            <label>
              {t('backtest.runnerTopN')}
              <input
                type="number"
                min={1}
                value={draftFilters.topN}
                onChange={(event) => setDraftFilters((current) => ({
                  ...current,
                  topN: Number(event.target.value),
                }))}
              />
            </label>
          )}
          {draftFilters.candidateMode === 'top_n' && (
            <label>
              {t('backtest.runnerTopNMetric')}
              <select
                value={draftFilters.topNMetric}
                onChange={(event) => {
                  const metric = event.target.value as TopNMetric
                  setDraftFilters((current) => ({
                    ...current,
                    topNMetric: metric,
                    horizonDays: metric === 'trade_score' ? current.horizonDays : topNMetricHorizon(metric, current.horizonDays),
                  }))
                }}
              >
                <option value="trade_score">{t('backtest.runnerTopNMetricTradeScore')}</option>
                <option value="up_prob_3d">{t('backtest.runnerTopNMetricUpProb3d')}</option>
                <option value="up_prob_7d">{t('backtest.runnerTopNMetricUpProb7d')}</option>
                <option value="up_prob_30d">{t('backtest.runnerTopNMetricUpProb30d')}</option>
              </select>
            </label>
          )}
          {draftFilters.candidateMode === 'trade_score' && (
            <>
              <label>
                {t('backtest.runnerMaxPositions')}
                <input
                  type="number"
                  min={1}
                  value={draftFilters.maxPositions}
                  onChange={(event) => setDraftFilters((current) => ({
                    ...current,
                    maxPositions: Number(event.target.value),
                  }))}
                />
              </label>
              <label>
                {t('backtest.runnerTradeScoreScope')}
                <select
                  value={draftFilters.tradeScoreScope}
                  onChange={(event) => setDraftFilters((current) => ({
                    ...current,
                    tradeScoreScope: event.target.value as DashboardCandidateFilters['tradeScoreScope'],
                  }))}
                >
                  <option value="independent">{t('backtest.runnerTradeScoreScopeIndependent')}</option>
                  <option value="combined">{t('backtest.runnerTradeScoreScopeCombined')}</option>
                </select>
              </label>
              <label>
                {t('backtest.runnerTradeScoreThreshold')}
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={draftFilters.tradeScoreThreshold}
                  onChange={(event) => setDraftFilters((current) => ({
                    ...current,
                    tradeScoreThreshold: Number(event.target.value),
                  }))}
                />
              </label>
            </>
          )}
          <label>
            {t('backtest.runnerUseMacroContext')}
            <select
              value={draftFilters.useMacroContext ? 'true' : 'false'}
              onChange={(event) => setDraftFilters((current) => ({
                ...current,
                useMacroContext: event.target.value === 'true',
              }))}
            >
              <option value="true">{t('common.yes')}</option>
              <option value="false">{t('common.no')}</option>
            </select>
          </label>
          <label>
            {t('backtest.runnerConfig')}
            <input value={`${t('backtest.runnerCandidateMode')}: ${draftFilters.candidateMode} · ${t('backtest.runnerHorizon')}: ${effectiveHorizon}`} readOnly />
          </label>
        </div>
        <div className="runner-actions">
          <button type="button" onClick={applyFilters}>{t('dash.applyFilters')}</button>
          <button type="button" onClick={resetFilters}>{t('dash.resetFilters')}</button>
          <button type="button" onClick={() => navigate('/indicator-board')}>{t('dash.viewIndicatorBoard')}</button>
        </div>
      </div>

      {reminderItems.length > 0 && (
        <div className="card">
          <h3>{t('dash.appliedConfig')}</h3>
          <div className="metric-grid">
            {reminderItems.map((item) => (
              <article key={item.label} className="metric-card">
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </article>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h3>{t('dash.topCandidate')}</h3>
        {rowsResource.isRefreshing && <p className="status">{t('common.updating')}</p>}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                {candidateColumns.map((column) => (
                  <th key={column.key}>
                    <button type="button" className="table-sort-button" onClick={() => handleSort(column)}>
                      {column.label}
                      {sortField === column.key ? (sortDirection === 'asc' ? ' ↑' : ' ↓') : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayedRows.map((row) => (
                <tr key={row.asset_symbol}>
                  {candidateColumns.map((column) => (
                    <td key={`${row.asset_symbol}-${column.key}`}>{column.render(row)}</td>
                  ))}
                </tr>
              ))}
              {displayedRows.length === 0 && rowsResource.loading && (
                <tr>
                  <td colSpan={candidateColumns.length}>
                    <TableSkeleton
                      columns={candidateColumns.length}
                      rows={Math.min(appliedCandidateLimit, 8)}
                    />
                  </td>
                </tr>
              )}
              {displayedRows.length === 0 && !rowsResource.loading && (
                <tr>
                  <td colSpan={candidateColumns.length}>{t('common.noData')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
