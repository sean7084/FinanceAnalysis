import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  createBacktestRun,
  fetchBacktestRuns,
  fetchBacktestTrades,
  hasAnyAuthCredential,
  type BacktestCreatePayload,
  type BacktestRunDto,
  type BacktestTradeDto,
} from '../lib/api'
import {
  buildDashboardFilterBundleFromRunnerConfig,
  buildDashboardSearchParams,
  isoWeekdayToLabel,
  WEEKDAY_LABELS,
  type PredictionSource,
  type TopNMetric,
} from '../lib/dashboardCandidateFilters'
import { useI18n } from '../i18n'

type WeekdayLabel = (typeof WEEKDAY_LABELS)[number]
const RUN_HISTORY_PAGE_SIZE_DEFAULT = 5
const TRADE_HISTORY_PAGE_SIZE = 10

function storedWeekdayToLabel(value: unknown): WeekdayLabel | null {
  if (typeof value === 'string') {
    const normalized = value.trim().toUpperCase()
    if ((WEEKDAY_LABELS as readonly string[]).includes(normalized)) {
      return normalized as WeekdayLabel
    }
    if (/^\d+$/.test(normalized)) {
      return storedWeekdayToLabel(Number(normalized))
    }
    return null
  }

  if (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < WEEKDAY_LABELS.length) {
    return WEEKDAY_LABELS[value]
  }

  return null
}

function formatStoredWeekdays(values: unknown): string {
  if (!Array.isArray(values)) {
    return '--'
  }

  const labels = values
    .map((value) => storedWeekdayToLabel(value))
    .filter((value): value is WeekdayLabel => value !== null)

  return labels.length ? labels.join(', ') : '--'
}

function addDays(isoDate: string, days: number): string {
  const dt = new Date(`${isoDate}T00:00:00Z`)
  dt.setUTCDate(dt.getUTCDate() + days)
  return dt.toISOString().slice(0, 10)
}

function formatLocalIsoDate(dt: Date): string {
  const year = dt.getFullYear()
  const month = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function getLatestFridayIsoDate(reference = new Date()): string {
  const dt = new Date(reference)
  const friday = 5
  const daysSinceFriday = (dt.getDay() - friday + 7) % 7
  dt.setDate(dt.getDate() - daysSinceFriday)
  return formatLocalIsoDate(dt)
}

function fmtMaybeNumber(value: unknown, digits = 2): string {
  if (typeof value === 'number') return value.toFixed(digits)
  if (typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))) {
    return Number(value).toFixed(digits)
  }
  return '--'
}

export function BacktestWorkbenchPage() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const defaultEndDate = getLatestFridayIsoDate()
  const defaultStartDate = addDays(defaultEndDate, -364)
  const [runs, setRuns] = useState<BacktestRunDto[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [trades, setTrades] = useState<BacktestTradeDto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tradeLoading, setTradeLoading] = useState(false)
  const [runPage, setRunPage] = useState(1)
  const [runPageSize, setRunPageSize] = useState(RUN_HISTORY_PAGE_SIZE_DEFAULT)
  const [tradePage, setTradePage] = useState(1)
  const [runnerBusy, setRunnerBusy] = useState(false)
  const [runnerMessage, setRunnerMessage] = useState<string>('')
  const [reuseRunId, setReuseRunId] = useState<string>('')
  const [runnerForm, setRunnerForm] = useState({
    mode: 'single' as 'single' | 'batch',
    namePrefix: 'Validation',
    predictionSource: 'all' as PredictionSource,
    startDate: defaultStartDate,
    endDate: defaultEndDate,
    horizonDays: 7 as 3 | 7 | 30,
    topN: 8,
    topNMetric: 'up_prob_7d' as TopNMetric,
    upThreshold: 0.45,
    candidateMode: 'top_n' as 'top_n' | 'trade_score',
    tradeScoreScope: 'independent' as 'independent' | 'combined',
    tradeScoreThreshold: 1,
    maxPositions: 5,
    useMacroContext: true,
    enableStopTargetExit: true,
    entryWeekdays: [2, 4] as number[],
    holdingPeriodDays: 14,
    capitalFractionPerEntry: 0.2,
    initialCapital: '200000.00',
    windowDays: 180,
    stepDays: 30,
  })

  const applyRunConfig = (run: BacktestRunDto) => {
    const params = (run.parameters ?? {}) as Record<string, unknown>
    const predictionSource = String(params.prediction_source ?? 'heuristic').toLowerCase() as Exclude<PredictionSource, 'all'>
    const topNMetric = String(params.top_n_metric ?? 'up_prob_7d').toLowerCase() as TopNMetric
    const entryWeekdays = Array.isArray(params.entry_weekdays)
      ? (params.entry_weekdays as unknown[])
          .map((value) => String(value).toUpperCase())
          .map((value) => WEEKDAY_LABELS.indexOf(value as (typeof WEEKDAY_LABELS)[number]) + 1)
          .filter((value): value is number => value >= 1 && value <= 5)
      : [2, 4]

    setRunnerForm((current) => ({
      ...current,
      mode: 'single',
      namePrefix: `rerun#${run.id}`,
      predictionSource,
      startDate: run.start_date,
      endDate: run.end_date,
      horizonDays: Number(params.horizon_days ?? current.horizonDays) as 3 | 7 | 30,
      topN: Number(params.top_n ?? current.topN),
      topNMetric,
      upThreshold: Number(params.up_threshold ?? current.upThreshold),
      candidateMode: String(params.candidate_mode ?? 'top_n').toLowerCase() === 'trade_score' ? 'trade_score' : 'top_n',
      tradeScoreScope: String(params.trade_score_scope ?? 'independent').toLowerCase() === 'combined' ? 'combined' : 'independent',
      tradeScoreThreshold: Number(params.trade_score_threshold ?? current.tradeScoreThreshold),
      maxPositions: Number(params.max_positions ?? params.top_n ?? current.maxPositions),
      useMacroContext: Boolean(params.use_macro_context ?? current.useMacroContext),
      enableStopTargetExit: Boolean(params.enable_stop_target_exit ?? current.enableStopTargetExit),
      entryWeekdays: entryWeekdays.length ? entryWeekdays : current.entryWeekdays,
      holdingPeriodDays: Number(params.holding_period_days ?? current.holdingPeriodDays),
      capitalFractionPerEntry: Number(params.capital_fraction_per_entry ?? current.capitalFractionPerEntry),
      initialCapital: String(run.initial_capital),
    }))
    setRunnerMessage(`Loaded config from run #${run.id}`)
  }

  const loadRuns = async () => {
    setLoading(true)
    const data = await fetchBacktestRuns(100)
    setRuns(data)
    setRunPage(1)
    setSelectedRunId((current) => current ?? data[0]?.id ?? null)
    setError(null)
    setLoading(false)
  }

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const data = await fetchBacktestRuns(100)
        if (alive) {
          setRuns(data)
          setRunPage(1)
          setSelectedRunId((current) => current ?? data[0]?.id ?? null)
          setError(null)
        }
      } catch {
        if (alive) {
          setError(hasAnyAuthCredential() ? t('backtest.loadError') : `${t('settings.desc')} (${t('nav.settings')})`)
        }
      } finally {
        if (alive) {
          setLoading(false)
        }
      }
    })()

    return () => {
      alive = false
    }
  }, [])

  const toggleWeekday = (day: number) => {
    setRunnerForm((current) => {
      if (current.entryWeekdays.includes(day)) {
        return {
          ...current,
          entryWeekdays: current.entryWeekdays.filter((value) => value !== day),
        }
      }
      return {
        ...current,
        entryWeekdays: [...current.entryWeekdays, day].sort((a, b) => a - b),
      }
    })
  }

  const applyQuickRange = (days: number) => {
    const endDate = runnerForm.endDate
    const startDate = addDays(endDate, -(days - 1))
    setRunnerForm((current) => ({ ...current, startDate }))
  }

  const selectedSources = (): Array<Exclude<PredictionSource, 'all'>> => {
    if (runnerForm.predictionSource === 'all') {
      return ['heuristic', 'lightgbm', 'lstm']
    }
    return [runnerForm.predictionSource]
  }

  const toRunPayload = (
    name: string,
    startDate: string,
    endDate: string,
    predictionSource: Exclude<PredictionSource, 'all'>,
  ): BacktestCreatePayload => {
    const weekdayLabels = runnerForm.entryWeekdays
      .map((day) => isoWeekdayToLabel(day))
      .filter((value): value is 'MON' | 'TUE' | 'WED' | 'THU' | 'FRI' => value !== null)
    return {
      name,
      strategy_type: 'PREDICTION_THRESHOLD',
      start_date: startDate,
      end_date: endDate,
      initial_capital: runnerForm.initialCapital,
      parameters: {
        prediction_source: predictionSource,
        top_n: runnerForm.topN,
        top_n_metric: runnerForm.topNMetric,
        horizon_days: runnerForm.horizonDays,
        up_threshold: runnerForm.upThreshold,
        candidate_mode: runnerForm.candidateMode,
        trade_score_scope: runnerForm.tradeScoreScope,
        trade_score_threshold: runnerForm.tradeScoreThreshold,
        max_positions: runnerForm.maxPositions,
        use_macro_context: runnerForm.useMacroContext,
        enable_stop_target_exit: runnerForm.enableStopTargetExit,
        entry_weekdays: weekdayLabels,
        holding_period_days: runnerForm.holdingPeriodDays,
        capital_fraction_per_entry: runnerForm.capitalFractionPerEntry,
      },
    }
  }

  const submitSingleRun = async () => {
    for (const source of selectedSources()) {
      const runName = `${runnerForm.namePrefix}-${source}-${runnerForm.startDate}-${runnerForm.endDate}`
      await createBacktestRun(toRunPayload(runName, runnerForm.startDate, runnerForm.endDate, source))
    }
    await loadRuns()
    setRunnerMessage(t('backtest.runnerSingleSuccess'))
  }

  const submitBatchRuns = async () => {
    let created = 0
    let cursor = runnerForm.startDate
    while (cursor <= runnerForm.endDate) {
      const windowEnd = addDays(cursor, runnerForm.windowDays - 1)
      const endDate = windowEnd < runnerForm.endDate ? windowEnd : runnerForm.endDate
      for (const source of selectedSources()) {
        const runName = `${runnerForm.namePrefix}-${source}-${cursor}-${endDate}`
        await createBacktestRun(toRunPayload(runName, cursor, endDate, source))
        created += 1
      }
      cursor = addDays(cursor, runnerForm.stepDays)
    }
    await loadRuns()
    setRunnerMessage(t('backtest.runnerBatchSuccess').replace('{count}', String(created)))
  }

  const onSubmitRunner = async () => {
    if (!runnerForm.entryWeekdays.length) {
      setRunnerMessage(t('backtest.runnerWeekdayError'))
      return
    }
    setRunnerBusy(true)
    setRunnerMessage('')
    try {
      if (runnerForm.mode === 'single') {
        await submitSingleRun()
      } else {
        await submitBatchRuns()
      }
    } catch (submitError) {
      const detail = submitError instanceof Error ? submitError.message : t('backtest.loadError')
      setRunnerMessage(`${t('backtest.runnerError')}: ${detail}`)
    } finally {
      setRunnerBusy(false)
    }
  }

  useEffect(() => {
    let alive = true
    if (!selectedRunId) {
      setTrades([])
      return
    }

    ;(async () => {
      try {
        setTradeLoading(true)
        const data = await fetchBacktestTrades(selectedRunId)
        if (alive) {
          setTrades(data)
          setTradePage(1)
        }
      } catch {
        if (alive) {
          setTrades([])
          setTradePage(1)
        }
      } finally {
        if (alive) {
          setTradeLoading(false)
        }
      }
    })()

    return () => {
      alive = false
    }
  }, [selectedRunId])

  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? null
  const reusableRuns = runs
    .filter((run) => run.strategy_type === 'PREDICTION_THRESHOLD')
    .sort((left, right) => right.id - left.id)
  const reusedRun = reuseRunId ? reusableRuns.find((run) => run.id === Number(reuseRunId)) ?? null : null
  const totalRunPages = Math.max(1, Math.ceil(runs.length / runPageSize))
  const clampedRunPage = Math.min(runPage, totalRunPages)
  const runStartIndex = (clampedRunPage - 1) * runPageSize
  const displayedRuns = runs.slice(runStartIndex, runStartIndex + runPageSize)

  const totalTradePages = Math.max(1, Math.ceil(trades.length / TRADE_HISTORY_PAGE_SIZE))
  const clampedTradePage = Math.min(tradePage, totalTradePages)
  const tradeStartIndex = (clampedTradePage - 1) * TRADE_HISTORY_PAGE_SIZE
  const displayedTrades = trades.slice(tradeStartIndex, tradeStartIndex + TRADE_HISTORY_PAGE_SIZE)

  const selectedReport = (selectedRun?.report ?? {}) as Record<string, unknown>
  const selectedParameters = (selectedRun?.parameters ?? {}) as Record<string, unknown>
  const benchmark = (selectedReport.benchmark ?? {}) as Record<string, unknown>
  const entryWeekdays = Array.isArray(selectedReport.entry_weekdays)
    ? formatStoredWeekdays(selectedReport.entry_weekdays)
    : Array.isArray(selectedParameters.entry_weekdays)
      ? formatStoredWeekdays(selectedParameters.entry_weekdays)
      : '--'
  const predictionSource = String(selectedReport.prediction_source ?? selectedParameters.prediction_source ?? '--')
  const candidateMode = String(selectedParameters.candidate_mode ?? selectedReport.candidate_mode ?? 'top_n')
  const candidateModeLabel = candidateMode === 'trade_score' ? t('backtest.runnerCandidateModeTradeScore') : t('backtest.runnerCandidateModeTopN')
  const topNMetric = String(selectedParameters.top_n_metric ?? 'up_prob_7d')
  const topNMetricLabelMap: Record<string, string> = {
    trade_score: t('backtest.runnerTopNMetricTradeScore'),
    up_prob_3d: t('backtest.runnerTopNMetricUpProb3d'),
    up_prob_7d: t('backtest.runnerTopNMetricUpProb7d'),
    up_prob_30d: t('backtest.runnerTopNMetricUpProb30d'),
  }
  const topNMetricLabel = topNMetricLabelMap[topNMetric] ?? topNMetric
  const tradeScoreScope = String(selectedParameters.trade_score_scope ?? 'independent')
  const tradeScoreScopeLabel = tradeScoreScope === 'combined'
    ? t('backtest.runnerTradeScoreScopeCombined')
    : t('backtest.runnerTradeScoreScopeIndependent')
  const macroContextEnabled = selectedParameters.use_macro_context
  const stopTargetEnabled = selectedParameters.enable_stop_target_exit
  const showHorizonSelector = runnerForm.candidateMode === 'trade_score' || runnerForm.topNMetric === 'trade_score'

  const openDashboardWithCurrentConfig = () => {
    const bundle = buildDashboardFilterBundleFromRunnerConfig(
      {
        predictionSource: runnerForm.predictionSource,
        horizonDays: runnerForm.horizonDays,
        upThreshold: runnerForm.upThreshold,
        candidateMode: runnerForm.candidateMode,
        topN: runnerForm.topN,
        topNMetric: runnerForm.topNMetric,
        tradeScoreScope: runnerForm.tradeScoreScope,
        tradeScoreThreshold: runnerForm.tradeScoreThreshold,
        maxPositions: runnerForm.maxPositions,
        useMacroContext: runnerForm.useMacroContext,
        holdingPeriodDays: runnerForm.holdingPeriodDays,
        enableStopTargetExit: runnerForm.enableStopTargetExit,
        capitalFractionPerEntry: runnerForm.capitalFractionPerEntry,
        initialCapital: runnerForm.initialCapital,
        entryWeekdays: runnerForm.entryWeekdays,
      },
      reusedRun ? { sourceRunId: reusedRun.id, sourceRunName: reusedRun.name } : {},
    )

    if (!bundle) {
      setRunnerMessage(t('backtest.runnerDashboardNeedsSingleSource'))
      return
    }

    navigate({ pathname: '/', search: `?${buildDashboardSearchParams(bundle).toString()}` })
  }

  return (
    <section>
      <header className="page-header">
        <h2>{t('backtest.title')}</h2>
        <p>{t('backtest.desc')}</p>
      </header>
      <div className="card runner-card">
        <h3>{t('backtest.runnerTitle')}</h3>
        <p className="subtitle">{t('backtest.runnerDesc')}</p>
        <div className="runner-grid">
          <label>
            {t('backtest.runnerMode')}
            <select
              value={runnerForm.mode}
              onChange={(event) => setRunnerForm((current) => ({ ...current, mode: event.target.value as 'single' | 'batch' }))}
            >
              <option value="single">{t('backtest.runnerModeSingle')}</option>
              <option value="batch">{t('backtest.runnerModeBatch')}</option>
            </select>
          </label>
          <label>
            {t('backtest.runnerNamePrefix')}
            <input
              value={runnerForm.namePrefix}
              onChange={(event) => setRunnerForm((current) => ({ ...current, namePrefix: event.target.value }))}
            />
          </label>
          <label>
            {t('backtest.runnerReuseConfig')}
            <select
              value={reuseRunId}
              onChange={(event) => {
                const nextId = event.target.value
                setReuseRunId(nextId)
                if (!nextId) return
                const matched = reusableRuns.find((run) => run.id === Number(nextId))
                if (matched) {
                  applyRunConfig(matched)
                }
              }}
            >
              <option value="">{t('backtest.runnerReuseConfigPlaceholder')}</option>
              {reusableRuns.map((run) => (
                <option key={run.id} value={run.id}>
                  #{run.id} {run.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t('backtest.predictionSource')}
            <select
              value={runnerForm.predictionSource}
              onChange={(event) => setRunnerForm((current) => ({ ...current, predictionSource: event.target.value as PredictionSource }))}
            >
              <option value="heuristic">heuristic</option>
              <option value="lightgbm">lightgbm</option>
              <option value="lstm">lstm</option>
              <option value="all">all-models</option>
            </select>
          </label>
          <label>
            {t('backtest.runnerStartDate')}
            <input
              type="date"
              value={runnerForm.startDate}
              onChange={(event) => setRunnerForm((current) => ({ ...current, startDate: event.target.value }))}
            />
          </label>
          <label>
            {t('backtest.runnerEndDate')}
            <input
              type="date"
              value={runnerForm.endDate}
              onChange={(event) => setRunnerForm((current) => ({ ...current, endDate: event.target.value }))}
            />
          </label>
          <div className="runner-quick-ranges">
            <span>{t('backtest.runnerQuickRange')}</span>
            <div className="runner-weekday-chips">
              <button type="button" className="chip" onClick={() => applyQuickRange(365)}>{t('backtest.runnerPastYear')}</button>
              <button type="button" className="chip" onClick={() => applyQuickRange(182)}>{t('backtest.runnerPastHalfYear')}</button>
            </div>
          </div>
          {showHorizonSelector && (
            <label>
              {t('backtest.runnerHorizon')}
              <select
                value={runnerForm.horizonDays}
                onChange={(event) => setRunnerForm((current) => ({ ...current, horizonDays: Number(event.target.value) as 3 | 7 | 30 }))}
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
              value={runnerForm.upThreshold}
              onChange={(event) => setRunnerForm((current) => ({ ...current, upThreshold: Number(event.target.value) }))}
            />
          </label>
          <label>
            {t('backtest.runnerCandidateMode')}
            <select
              value={runnerForm.candidateMode}
              onChange={(event) => setRunnerForm((current) => ({ ...current, candidateMode: event.target.value as 'top_n' | 'trade_score' }))}
            >
              <option value="top_n">{t('backtest.runnerCandidateModeTopN')}</option>
              <option value="trade_score">{t('backtest.runnerCandidateModeTradeScore')}</option>
            </select>
          </label>
          {runnerForm.candidateMode === 'top_n' && (
            <label>
              {t('backtest.runnerTopN')}
              <input
                type="number"
                min={1}
                value={runnerForm.topN}
                onChange={(event) => setRunnerForm((current) => ({ ...current, topN: Number(event.target.value) }))}
              />
            </label>
          )}
          {runnerForm.candidateMode === 'top_n' && (
            <label>
              {t('backtest.runnerTopNMetric')}
              <select
                value={runnerForm.topNMetric}
                onChange={(event) => {
                  const metric = event.target.value as TopNMetric
                  const metricHorizonMap: Record<Exclude<TopNMetric, 'trade_score'>, 3 | 7 | 30> = {
                    up_prob_3d: 3,
                    up_prob_7d: 7,
                    up_prob_30d: 30,
                  }
                  setRunnerForm((current) => ({
                    ...current,
                    topNMetric: metric,
                    horizonDays: metric === 'trade_score' ? current.horizonDays : metricHorizonMap[metric],
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
          {runnerForm.candidateMode === 'trade_score' && (
            <>
              <label>
                {t('backtest.runnerMaxPositions')}
                <input
                  type="number"
                  min={1}
                  value={runnerForm.maxPositions}
                  onChange={(event) => setRunnerForm((current) => ({ ...current, maxPositions: Number(event.target.value) }))}
                />
              </label>
              <label>
                {t('backtest.runnerTradeScoreScope')}
                <select
                  value={runnerForm.tradeScoreScope}
                  onChange={(event) => setRunnerForm((current) => ({ ...current, tradeScoreScope: event.target.value as 'independent' | 'combined' }))}
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
                  value={runnerForm.tradeScoreThreshold}
                  onChange={(event) => setRunnerForm((current) => ({ ...current, tradeScoreThreshold: Number(event.target.value) }))}
                />
              </label>
            </>
          )}
          <label>
            {t('backtest.runnerHoldingDays')}
            <input
              type="number"
              min={1}
              value={runnerForm.holdingPeriodDays}
              onChange={(event) => setRunnerForm((current) => ({ ...current, holdingPeriodDays: Number(event.target.value) }))}
            />
          </label>
          <label>
            {t('backtest.runnerCapitalFraction')}
            <input
              type="number"
              min={0.01}
              max={1}
              step={0.01}
              value={runnerForm.capitalFractionPerEntry}
              onChange={(event) => setRunnerForm((current) => ({ ...current, capitalFractionPerEntry: Number(event.target.value) }))}
            />
          </label>
          <label>
            {t('backtest.runnerInitialCapital')}
            <input
              value={runnerForm.initialCapital}
              onChange={(event) => setRunnerForm((current) => ({ ...current, initialCapital: event.target.value }))}
            />
          </label>
          <label>
            {t('backtest.runnerUseMacroContext')}
            <select
              value={runnerForm.useMacroContext ? 'true' : 'false'}
              onChange={(event) => setRunnerForm((current) => ({ ...current, useMacroContext: event.target.value === 'true' }))}
            >
              <option value="true">{t('common.yes')}</option>
              <option value="false">{t('common.no')}</option>
            </select>
          </label>
          <label>
            {t('backtest.runnerUseStopTargetExit')}
            <select
              value={runnerForm.enableStopTargetExit ? 'true' : 'false'}
              onChange={(event) => setRunnerForm((current) => ({ ...current, enableStopTargetExit: event.target.value === 'true' }))}
            >
              <option value="true">{t('common.yes')}</option>
              <option value="false">{t('common.no')}</option>
            </select>
          </label>
          {runnerForm.mode === 'batch' && (
            <>
              <label>
                {t('backtest.runnerWindowDays')}
                <input
                  type="number"
                  min={1}
                  value={runnerForm.windowDays}
                  onChange={(event) => setRunnerForm((current) => ({ ...current, windowDays: Number(event.target.value) }))}
                />
              </label>
              <label>
                {t('backtest.runnerStepDays')}
                <input
                  type="number"
                  min={1}
                  value={runnerForm.stepDays}
                  onChange={(event) => setRunnerForm((current) => ({ ...current, stepDays: Number(event.target.value) }))}
                />
              </label>
            </>
          )}
        </div>
        <div className="runner-weekdays">
          <span>{t('backtest.runnerWeekdays')}</span>
          <div className="runner-weekday-chips">
            {WEEKDAY_LABELS.map((label, index) => {
              const day = index + 1
              const selected = runnerForm.entryWeekdays.includes(day)
              return (
                <button
                  key={label}
                  type="button"
                  className={selected ? 'chip selected' : 'chip'}
                  onClick={() => toggleWeekday(day)}
                >
                  {label}
                </button>
              )
            })}
          </div>
        </div>
        <div className="runner-actions">
          <button type="button" disabled={runnerBusy} onClick={onSubmitRunner}>
            {runnerBusy ? t('common.loading') : t('backtest.runnerSubmit')}
          </button>
          <button type="button" disabled={runnerBusy} onClick={loadRuns}>
            {t('backtest.runnerRefresh')}
          </button>
          <button type="button" disabled={runnerBusy} onClick={openDashboardWithCurrentConfig}>
            {t('backtest.runnerOpenDashboard')}
          </button>
        </div>
        {runnerMessage ? <p className="status">{runnerMessage}</p> : null}
      </div>
      {selectedRun && (
        <div className="metric-grid">
          <article className="card metric-card">
            <span>{t('backtest.selectedRun')}</span>
            <strong>#{selectedRun.id} {selectedRun.name}</strong>
          </article>
          <article className="card metric-card">
            <span>{t('backtest.predictionSource')}</span>
            <strong>{predictionSource}</strong>
          </article>
          <article className="card metric-card">
            <span>{t('backtest.totalTrades')}</span>
            <strong>{selectedRun.total_trades}</strong>
          </article>
          <article className="card metric-card">
            <span>{t('backtest.benchmarkReturn')}</span>
            <strong>{typeof benchmark.total_return === 'number' ? `${(benchmark.total_return * 100).toFixed(2)}%` : '--'}</strong>
          </article>
        </div>
      )}
      <div className="card">
        {loading && <p className="status">{t('common.loading')}</p>}
        {error && <p className="status disconnected">{error}</p>}
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('backtest.runId')}</th>
              <th>{t('backtest.name')}</th>
              <th>{t('backtest.strategy')}</th>
              <th>{t('backtest.predictionSource')}</th>
              <th>{t('backtest.status')}</th>
              <th>{t('backtest.return')}</th>
              <th>{t('backtest.maxDrawdown')}</th>
              <th>{t('backtest.winRate')}</th>
              <th>{t('backtest.sharpe')}</th>
              <th>{t('backtest.totalTrades')}</th>
            </tr>
          </thead>
          <tbody>
            {displayedRuns.map((run) => (
              <tr key={run.id} className={selectedRunId === run.id ? 'row-selected' : ''} onClick={() => setSelectedRunId(run.id)}>
                <td>#{run.id}</td>
                <td>{run.name}</td>
                <td>{run.strategy_type}</td>
                <td>{String(run.report?.prediction_source ?? run.parameters?.prediction_source ?? '--')}</td>
                <td>{run.status}</td>
                <td>{run.status === 'COMPLETED' && run.total_return !== null ? `${(Number(run.total_return) * 100).toFixed(2)}%` : '--'}</td>
                <td>{run.status === 'COMPLETED' && run.max_drawdown !== null ? `${(Number(run.max_drawdown) * 100).toFixed(2)}%` : '--'}</td>
                <td>{run.status === 'COMPLETED' && run.win_rate !== null ? `${(Number(run.win_rate) * 100).toFixed(2)}%` : '--'}</td>
                <td>{run.status === 'COMPLETED' && run.sharpe_ratio !== null ? Number(run.sharpe_ratio).toFixed(2) : '--'}</td>
                <td>{run.total_trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="table-pagination">
          <label>
            {t('backtest.entriesPerPage')}
            <select
              value={runPageSize}
              onChange={(event) => {
                setRunPageSize(Number(event.target.value))
                setRunPage(1)
              }}
            >
              <option value={5}>5</option>
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={50}>50</option>
            </select>
          </label>
          <button type="button" disabled={clampedRunPage <= 1} onClick={() => setRunPage((value) => Math.max(1, value - 1))}>
            {t('backtest.prev')}
          </button>
          <span>{t('backtest.page')} {clampedRunPage}/{totalRunPages}</span>
          <button type="button" disabled={clampedRunPage >= totalRunPages} onClick={() => setRunPage((value) => Math.min(totalRunPages, value + 1))}>
            {t('backtest.next')}
          </button>
        </div>
      </div>
      {selectedRun && (
        <div className="card">
          <h3>{t('backtest.tradeDetails')}</h3>
          <p className="subtitle">
            {t('backtest.schedule')}: {entryWeekdays} · {t('backtest.holdDays')}: {String(selectedReport.holding_period_days ?? '--')}
          </p>
          <div className="metric-grid">
            <article className="metric-card">
              <span>{t('backtest.initialCapital')}</span>
              <strong>{Number(selectedRun.initial_capital).toFixed(2)}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.finalValue')}</span>
              <strong>{selectedRun.final_value !== null ? Number(selectedRun.final_value).toFixed(2) : '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.return')}</span>
              <strong>{selectedRun.total_return !== null ? `${(Number(selectedRun.total_return) * 100).toFixed(2)}%` : '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.annualizedReturn')}</span>
              <strong>{selectedRun.annualized_return !== null ? `${(Number(selectedRun.annualized_return) * 100).toFixed(2)}%` : '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.maxDrawdown')}</span>
              <strong>{selectedRun.max_drawdown !== null ? `${(Number(selectedRun.max_drawdown) * 100).toFixed(2)}%` : '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.sharpe')}</span>
              <strong>{selectedRun.sharpe_ratio !== null ? Number(selectedRun.sharpe_ratio).toFixed(2) : '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.winRate')}</span>
              <strong>{selectedRun.win_rate !== null ? `${(Number(selectedRun.win_rate) * 100).toFixed(2)}%` : '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.totalTrades')}</span>
              <strong>{selectedRun.total_trades}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.winningTrades')}</span>
              <strong>{selectedRun.winning_trades ?? '--'}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.benchmarkReturn')}</span>
              <strong>{typeof benchmark.total_return === 'number' ? `${(benchmark.total_return * 100).toFixed(2)}%` : '--'}</strong>
            </article>
          </div>
          <h4>{t('backtest.runnerConfig')}</h4>
          <div className="metric-grid">
            <article className="metric-card">
              <span>{t('backtest.runnerStartDate')}</span>
              <strong>{selectedRun.start_date}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerEndDate')}</span>
              <strong>{selectedRun.end_date}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.predictionSource')}</span>
              <strong>{predictionSource}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerCandidateMode')}</span>
              <strong>{candidateModeLabel}</strong>
            </article>
            {candidateMode === 'trade_score' && (
              <article className="metric-card">
                <span>{t('backtest.runnerHorizon')}</span>
                <strong>{String(selectedParameters.horizon_days ?? selectedReport.horizon_days ?? '--')}</strong>
              </article>
            )}
            <article className="metric-card">
              <span>{t('backtest.runnerUpThreshold')}</span>
              <strong>{fmtMaybeNumber(selectedParameters.up_threshold, 2)}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerTopN')}</span>
              <strong>{String(selectedParameters.top_n ?? '--')}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerTopNMetric')}</span>
              <strong>{topNMetricLabel}</strong>
            </article>
            {candidateMode === 'trade_score' && (
              <>
                <article className="metric-card">
                  <span>{t('backtest.runnerMaxPositions')}</span>
                  <strong>{String(selectedParameters.max_positions ?? '--')}</strong>
                </article>
                <article className="metric-card">
                  <span>{t('backtest.runnerTradeScoreScope')}</span>
                  <strong>{tradeScoreScopeLabel}</strong>
                </article>
                <article className="metric-card">
                  <span>{t('backtest.runnerTradeScoreThreshold')}</span>
                  <strong>{fmtMaybeNumber(selectedParameters.trade_score_threshold, 2)}</strong>
                </article>
              </>
            )}
            <article className="metric-card">
              <span>{t('backtest.runnerUseMacroContext')}</span>
              <strong>
                {typeof macroContextEnabled === 'boolean' ? (macroContextEnabled ? t('common.yes') : t('common.no')) : '--'}
              </strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerUseStopTargetExit')}</span>
              <strong>
                {typeof stopTargetEnabled === 'boolean' ? (stopTargetEnabled ? t('common.yes') : t('common.no')) : '--'}
              </strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerWeekdays')}</span>
              <strong>{entryWeekdays}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerHoldingDays')}</span>
              <strong>{String(selectedParameters.holding_period_days ?? selectedReport.holding_period_days ?? '--')}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerCapitalFraction')}</span>
              <strong>{fmtMaybeNumber(selectedParameters.capital_fraction_per_entry, 2)}</strong>
            </article>
            <article className="metric-card">
              <span>{t('backtest.runnerInitialCapital')}</span>
              <strong>{fmtMaybeNumber(selectedRun.initial_capital, 2)}</strong>
            </article>
          </div>
          {tradeLoading && <p className="status">{t('common.loading')}</p>}
          {!tradeLoading && (
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t('backtest.tradeDate')}</th>
                  <th>{t('backtest.side')}</th>
                  <th>{t('models.asset')}</th>
                  <th>{t('backtest.price')}</th>
                  <th>{t('backtest.fee')}</th>
                  <th>{t('backtest.amount')}</th>
                  <th>{t('backtest.pnl')}</th>
                </tr>
              </thead>
              <tbody>
                {displayedTrades.map((trade) => (
                  <tr key={trade.id}>
                    <td>{trade.trade_date}</td>
                    <td>{trade.side}</td>
                    <td>{trade.asset_symbol}</td>
                    <td>{Number(trade.price).toFixed(4)}</td>
                    <td>{Number(trade.fee).toFixed(4)}</td>
                    <td>{Number(trade.amount).toFixed(4)}</td>
                    <td>{Number(trade.pnl).toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!tradeLoading && (
            <div className="table-pagination">
              <button type="button" disabled={clampedTradePage <= 1} onClick={() => setTradePage((value) => Math.max(1, value - 1))}>
                {t('backtest.prev')}
              </button>
              <span>{t('backtest.page')} {clampedTradePage}/{totalTradePages}</span>
              <button type="button" disabled={clampedTradePage >= totalTradePages} onClick={() => setTradePage((value) => Math.min(totalTradePages, value + 1))}>
                {t('backtest.next')}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
