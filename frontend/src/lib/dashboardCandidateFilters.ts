export const WEEKDAY_LABELS = ['MON', 'TUE', 'WED', 'THU', 'FRI'] as const

export type WeekdayLabel = (typeof WEEKDAY_LABELS)[number]
export type CandidatePredictionSource = 'heuristic' | 'lightgbm' | 'lstm'
export type PredictionSource = CandidatePredictionSource | 'all'
export type TopNMetric = 'trade_score' | 'up_prob_3d' | 'up_prob_7d' | 'up_prob_30d'
export type CandidateMode = 'top_n' | 'trade_score'
export type TradeScoreScope = 'independent' | 'combined'
export type HorizonDays = 3 | 7 | 30

export interface DashboardCandidateFilters {
  predictionSource: CandidatePredictionSource
  horizonDays: HorizonDays
  upThreshold: number
  candidateMode: CandidateMode
  topN: number
  topNMetric: TopNMetric
  tradeScoreScope: TradeScoreScope
  tradeScoreThreshold: number
  maxPositions: number
  useMacroContext: boolean
}

export interface DashboardFilterReminders {
  sourceRunId?: number
  sourceRunName?: string
  holdingPeriodDays?: number
  enableStopTargetExit?: boolean
  capitalFractionPerEntry?: number
  initialCapital?: string
  entryWeekdays?: WeekdayLabel[]
}

export interface DashboardFilterBundle {
  filters: DashboardCandidateFilters
  reminders: DashboardFilterReminders
}

export interface RunnerConfigLike {
  predictionSource: PredictionSource
  horizonDays: HorizonDays
  upThreshold: number
  candidateMode: CandidateMode
  topN: number
  topNMetric: TopNMetric
  tradeScoreScope: TradeScoreScope
  tradeScoreThreshold: number
  maxPositions: number
  useMacroContext: boolean
  holdingPeriodDays: number
  enableStopTargetExit: boolean
  capitalFractionPerEntry: number
  initialCapital: string
  entryWeekdays: number[]
}

function parseNumber(value: string | null, fallback: number): number {
  if (!value) {
    return fallback
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function parseInteger(value: string | null, fallback: number, minimum = 1): number {
  const parsed = parseNumber(value, fallback)
  return Math.max(minimum, Math.trunc(parsed))
}

function parsePredictionSource(value: string | null, fallback: CandidatePredictionSource): CandidatePredictionSource {
  if (value === 'heuristic' || value === 'lightgbm' || value === 'lstm') {
    return value
  }
  return fallback
}

function parseHorizon(value: string | null, fallback: HorizonDays): HorizonDays {
  if (value === '3') return 3
  if (value === '7') return 7
  if (value === '30') return 30
  return fallback
}

function parseCandidateMode(value: string | null, fallback: CandidateMode): CandidateMode {
  return value === 'trade_score' ? 'trade_score' : fallback
}

function parseTopNMetric(value: string | null, fallback: TopNMetric): TopNMetric {
  if (value === 'trade_score' || value === 'up_prob_3d' || value === 'up_prob_7d' || value === 'up_prob_30d') {
    return value
  }
  return fallback
}

function parseTradeScoreScope(value: string | null, fallback: TradeScoreScope): TradeScoreScope {
  return value === 'combined' ? 'combined' : fallback
}

function parseBoolean(value: string | null, fallback: boolean): boolean {
  if (value == null || value === '') {
    return fallback
  }
  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase())
}

function parseWeekdayLabels(value: string | null): WeekdayLabel[] | undefined {
  if (!value) {
    return undefined
  }
  const labels = value
    .split(',')
    .map((item) => item.trim().toUpperCase())
    .filter((item): item is WeekdayLabel => (WEEKDAY_LABELS as readonly string[]).includes(item))
  return labels.length ? labels : undefined
}

export function isoWeekdayToLabel(day: number): WeekdayLabel | null {
  if (day === 1) return 'MON'
  if (day === 2) return 'TUE'
  if (day === 3) return 'WED'
  if (day === 4) return 'THU'
  if (day === 5) return 'FRI'
  return null
}

export function topNMetricHorizon(metric: TopNMetric, fallback: HorizonDays): HorizonDays {
  if (metric === 'up_prob_3d') return 3
  if (metric === 'up_prob_7d') return 7
  if (metric === 'up_prob_30d') return 30
  return fallback
}

export function dashboardCandidateLimit(filters: DashboardCandidateFilters): number {
  const value = filters.candidateMode === 'top_n' ? filters.topN : filters.maxPositions
  return Math.max(1, Math.trunc(value))
}

export function dashboardPredictionHorizon(filters: DashboardCandidateFilters): HorizonDays {
  if (filters.candidateMode === 'top_n') {
    return topNMetricHorizon(filters.topNMetric, filters.horizonDays)
  }
  return filters.horizonDays
}

export function createDefaultDashboardCandidateFilters(): DashboardCandidateFilters {
  return {
    predictionSource: 'lightgbm',
    horizonDays: 7,
    upThreshold: 0.45,
    candidateMode: 'top_n',
    topN: 8,
    topNMetric: 'up_prob_7d',
    tradeScoreScope: 'independent',
    tradeScoreThreshold: 1,
    maxPositions: 5,
    useMacroContext: true,
  }
}

export function buildDashboardFilterBundleFromRunnerConfig(
  config: RunnerConfigLike,
  reminders: Partial<DashboardFilterReminders> = {},
): DashboardFilterBundle | null {
  if (config.predictionSource === 'all') {
    return null
  }

  const entryWeekdays = config.entryWeekdays
    .map((day) => isoWeekdayToLabel(day))
    .filter((value): value is WeekdayLabel => value !== null)

  return {
    filters: {
      predictionSource: config.predictionSource,
      horizonDays: config.horizonDays,
      upThreshold: config.upThreshold,
      candidateMode: config.candidateMode,
      topN: config.topN,
      topNMetric: config.topNMetric,
      tradeScoreScope: config.tradeScoreScope,
      tradeScoreThreshold: config.tradeScoreThreshold,
      maxPositions: config.maxPositions,
      useMacroContext: config.useMacroContext,
    },
    reminders: {
      holdingPeriodDays: config.holdingPeriodDays,
      enableStopTargetExit: config.enableStopTargetExit,
      capitalFractionPerEntry: config.capitalFractionPerEntry,
      initialCapital: config.initialCapital,
      entryWeekdays: entryWeekdays.length ? entryWeekdays : undefined,
      ...reminders,
    },
  }
}

export function buildDashboardSearchParams(bundle: DashboardFilterBundle): URLSearchParams {
  const params = new URLSearchParams()
  params.set('prediction_source', bundle.filters.predictionSource)
  params.set('horizon_days', String(bundle.filters.horizonDays))
  params.set('candidate_mode', bundle.filters.candidateMode)
  params.set('up_threshold', String(bundle.filters.upThreshold))
  params.set('top_n', String(bundle.filters.topN))
  params.set('top_n_metric', bundle.filters.topNMetric)
  params.set('trade_score_scope', bundle.filters.tradeScoreScope)
  params.set('trade_score_threshold', String(bundle.filters.tradeScoreThreshold))
  params.set('max_positions', String(bundle.filters.maxPositions))
  params.set('use_macro_context', bundle.filters.useMacroContext ? 'true' : 'false')

  if (bundle.reminders.sourceRunId != null) {
    params.set('source_run_id', String(bundle.reminders.sourceRunId))
  }
  if (bundle.reminders.sourceRunName) {
    params.set('source_run_name', bundle.reminders.sourceRunName)
  }
  if (bundle.reminders.holdingPeriodDays != null) {
    params.set('reminder_holding_period_days', String(bundle.reminders.holdingPeriodDays))
  }
  if (bundle.reminders.enableStopTargetExit != null) {
    params.set('reminder_enable_stop_target_exit', bundle.reminders.enableStopTargetExit ? 'true' : 'false')
  }
  if (bundle.reminders.capitalFractionPerEntry != null) {
    params.set('reminder_capital_fraction_per_entry', String(bundle.reminders.capitalFractionPerEntry))
  }
  if (bundle.reminders.initialCapital) {
    params.set('reminder_initial_capital', bundle.reminders.initialCapital)
  }
  if (bundle.reminders.entryWeekdays?.length) {
    params.set('reminder_entry_weekdays', bundle.reminders.entryWeekdays.join(','))
  }

  return params
}

export function parseDashboardSearchParams(searchParams: URLSearchParams): DashboardFilterBundle {
  const defaults = createDefaultDashboardCandidateFilters()

  return {
    filters: {
      predictionSource: parsePredictionSource(searchParams.get('prediction_source'), defaults.predictionSource),
      horizonDays: parseHorizon(searchParams.get('horizon_days'), defaults.horizonDays),
      upThreshold: parseNumber(searchParams.get('up_threshold'), defaults.upThreshold),
      candidateMode: parseCandidateMode(searchParams.get('candidate_mode'), defaults.candidateMode),
      topN: parseInteger(searchParams.get('top_n'), defaults.topN),
      topNMetric: parseTopNMetric(searchParams.get('top_n_metric'), defaults.topNMetric),
      tradeScoreScope: parseTradeScoreScope(searchParams.get('trade_score_scope'), defaults.tradeScoreScope),
      tradeScoreThreshold: parseNumber(searchParams.get('trade_score_threshold'), defaults.tradeScoreThreshold),
      maxPositions: parseInteger(searchParams.get('max_positions'), defaults.maxPositions),
      useMacroContext: parseBoolean(searchParams.get('use_macro_context'), defaults.useMacroContext),
    },
    reminders: {
      sourceRunId: searchParams.get('source_run_id') ? parseInteger(searchParams.get('source_run_id'), 0) : undefined,
      sourceRunName: searchParams.get('source_run_name') || undefined,
      holdingPeriodDays: searchParams.get('reminder_holding_period_days')
        ? parseInteger(searchParams.get('reminder_holding_period_days'), 1)
        : undefined,
      enableStopTargetExit: searchParams.has('reminder_enable_stop_target_exit')
        ? parseBoolean(searchParams.get('reminder_enable_stop_target_exit'), false)
        : undefined,
      capitalFractionPerEntry: searchParams.get('reminder_capital_fraction_per_entry')
        ? parseNumber(searchParams.get('reminder_capital_fraction_per_entry'), 0)
        : undefined,
      initialCapital: searchParams.get('reminder_initial_capital') || undefined,
      entryWeekdays: parseWeekdayLabels(searchParams.get('reminder_entry_weekdays')),
    },
  }
}