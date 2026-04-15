export type MarketTag = 'RECOVERY' | 'OVERHEAT' | 'STAGFLATION' | 'RECESSION'

export interface CandidateStock {
  id: number
  symbol: string
  name: string
  score: number
  probability: number
}

export interface BacktestRun {
  id: number
  strategy_type: string
  status: string
  initial_capital: number
  final_value: number | null
  total_return: number | null
  sharpe_ratio: number | null
  created_at: string
}

export interface SignalSnapshot {
  date: string
  predictionSignals: number
  alertSignals: number
}

export interface ProbabilityPoint {
  horizon: string
  up: number
  flat: number
  down: number
}

export interface MacroOverview {
  phase: MarketTag
  eventTag: string
  confidence: number
}

export interface ModelMetricRow {
  label: string
  value: string
}
