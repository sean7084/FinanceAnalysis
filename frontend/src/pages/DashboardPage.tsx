import { useEffect, useMemo, useState } from 'react'
import {
  fetchDashboardData,
  fetchDashboardStocks,
  hasAnyAuthCredential,
  type DashboardStockRowDto,
} from '../lib/api'
import { useI18n } from '../i18n'

export function DashboardPage() {
  const { t } = useI18n()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dashboard, setDashboard] = useState({
    macroPhase: 'N/A',
    hotConcepts: 'N/A',
    predictionSignals: 0,
    alertTriggers: 0,
    completedBacktests: 0,
    avgBottomProbability: 0,
  })
  const [dashboardRows, setDashboardRows] = useState<DashboardStockRowDto[]>([])
  const [topN, setTopN] = useState(5)
  const [modelFamily, setModelFamily] = useState<'both' | 'heuristic' | 'lightgbm'>('both')
  const [candidateScope, setCandidateScope] = useState<'all' | 'suggested'>('all')
  const [sortField, setSortField] = useState<keyof DashboardStockRowDto>('composite_score')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const [data, rows] = await Promise.all([
          fetchDashboardData(),
          fetchDashboardStocks(7),
        ])

        if (alive) {
          setDashboard(data)
          setDashboardRows(rows)
          setError(null)
        }
      } catch {
        if (alive) {
          setError('Failed to load dashboard data. Check API auth token/API key.')
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

  const topCandidateRows = useMemo(() => {
    const filtered = dashboardRows.filter((row) => {
      if (candidateScope === 'suggested') {
        if (modelFamily === 'heuristic') return row.heuristic_suggested
        if (modelFamily === 'lightgbm') return row.lightgbm_suggested
        return row.heuristic_suggested || row.lightgbm_suggested
      }
      return true
    })

    const scoreForModel = (row: DashboardStockRowDto) => {
      if (modelFamily === 'heuristic') return Number(row.heuristic_trade_score ?? row.heuristic_up_probability ?? 0)
      if (modelFamily === 'lightgbm') return Number(row.lightgbm_trade_score ?? row.lightgbm_up_probability ?? 0)
      return Math.max(
        Number(row.heuristic_trade_score ?? row.heuristic_up_probability ?? 0),
        Number(row.lightgbm_trade_score ?? row.lightgbm_up_probability ?? 0),
      )
    }

    return [...filtered]
      .sort((left, right) => scoreForModel(right) - scoreForModel(left))
      .slice(0, topN)
  }, [candidateScope, dashboardRows, modelFamily, topN])

  const allStocksRows = useMemo(() => {
    const matchesFilter = (value: unknown, filter: string) => String(value ?? '').toLowerCase().includes(filter.toLowerCase())
    const filtered = dashboardRows.filter((row) => (
      Object.entries(columnFilters).every(([key, value]) => !value || matchesFilter(row[key as keyof DashboardStockRowDto], value))
    ))

    return [...filtered].sort((left, right) => {
      const leftValue = left[sortField]
      const rightValue = right[sortField]
      if (leftValue == null && rightValue == null) return 0
      if (leftValue == null) return 1
      if (rightValue == null) return -1
      if (typeof leftValue === 'string' && typeof rightValue === 'string') {
        return sortDirection === 'asc' ? leftValue.localeCompare(rightValue) : rightValue.localeCompare(leftValue)
      }
      const leftNumber = Number(leftValue)
      const rightNumber = Number(rightValue)
      return sortDirection === 'asc' ? leftNumber - rightNumber : rightNumber - leftNumber
    })
  }, [columnFilters, dashboardRows, sortDirection, sortField])

  const handleSort = (field: keyof DashboardStockRowDto) => {
    if (sortField === field) {
      setSortDirection((value) => value === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortField(field)
    setSortDirection('desc')
  }

  const setColumnFilter = (field: keyof DashboardStockRowDto, value: string) => {
    setColumnFilters((current) => ({
      ...current,
      [field]: value,
    }))
  }

  const tableColumns: Array<{ key: keyof DashboardStockRowDto; label: string }> = [
    { key: 'asset_symbol', label: t('screener.symbol') },
    { key: 'asset_name', label: t('screener.name') },
    { key: 'composite_score', label: t('screener.comp') },
    { key: 'bottom_probability_score', label: t('screener.bottomProb') },
    { key: 'fundamental_score', label: t('dash.fundamental') },
    { key: 'capital_flow_score', label: t('dash.capitalFlow') },
    { key: 'technical_score', label: t('dash.technical') },
    { key: 'factor_sentiment_score', label: t('dash.factorSentiment') },
    { key: 'rsi', label: 'RSI' },
    { key: 'macd', label: 'MACD' },
    { key: 'bb_upper', label: 'BB Upper' },
    { key: 'bb_lower', label: 'BB Lower' },
    { key: 'sma_60', label: 'SMA60' },
    { key: 'heuristic_trade_score', label: t('dash.heuristicTradeScore') },
    { key: 'heuristic_target_price', label: t('trade.targetPrice') },
    { key: 'heuristic_stop_loss_price', label: t('trade.stopLoss') },
    { key: 'heuristic_risk_reward_ratio', label: t('dash.heuristicRR') },
    { key: 'heuristic_suggested', label: t('dash.heuristicSuggested') },
    { key: 'lightgbm_trade_score', label: t('dash.lightgbmTradeScore') },
    { key: 'lightgbm_target_price', label: t('trade.targetPrice') },
    { key: 'lightgbm_stop_loss_price', label: t('trade.stopLoss') },
    { key: 'lightgbm_risk_reward_ratio', label: t('dash.lightgbmRR') },
    { key: 'lightgbm_suggested', label: t('dash.lightgbmSuggested') },
  ]

  const renderValue = (value: unknown) => {
    if (typeof value === 'boolean') {
      return value ? t('common.yes') : t('common.no')
    }
    if (value == null || value === '') {
      return '--'
    }
    if (typeof value === 'number') {
      return value.toFixed(2)
    }
    const numericValue = Number(value)
    if (!Number.isNaN(numericValue) && String(value).trim() !== '') {
      return numericValue.toFixed(2)
    }
    return String(value)
  }

  const translatedMetrics = [
    { label: t('dash.macro'), value: dashboard.macroPhase },
    { label: t('dash.hotConcepts'), value: dashboard.hotConcepts },
    { label: t('dash.predSignals'), value: `${dashboard.predictionSignals} ${t('dash.today')}` },
    { label: t('dash.alertTriggers'), value: `${dashboard.alertTriggers} ${t('dash.live')}` },
    { label: t('dash.completedBacktests'), value: String(dashboard.completedBacktests) },
    { label: t('dash.avgBottomProb'), value: `${(dashboard.avgBottomProbability * 100).toFixed(1)}%` },
    { label: t('dash.suggestedSetups'), value: String(dashboardRows.filter((row) => row.heuristic_suggested || row.lightgbm_suggested).length) },
    { label: t('dash.topTradeScore'), value: dashboardRows.length ? Number(Math.max(...dashboardRows.map((row) => Math.max(Number(row.heuristic_trade_score ?? 0), Number(row.lightgbm_trade_score ?? 0))))).toFixed(2) : '--' },
  ]

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
            <strong>{loading ? t('common.loading') : m.value}</strong>
          </article>
        ))}
      </div>

      {error && <p className="status disconnected">{hasAnyAuthCredential() ? t('dash.loadError') : `${t('settings.desc')} (${t('nav.settings')})`}</p>}

      <div className="card">
        <label htmlFor="dashboard-top-n">{t('dash.topN')}</label>
        <select
          id="dashboard-top-n"
          value={topN}
          onChange={(e) => setTopN(Number(e.target.value))}
        >
          <option value={3}>Top 3</option>
          <option value={5}>Top 5</option>
          <option value={10}>Top 10</option>
          <option value={20}>Top 20</option>
        </select>

        <label htmlFor="dashboard-model-family">{t('dash.modelFamily')}</label>
        <select
          id="dashboard-model-family"
          value={modelFamily}
          onChange={(e) => setModelFamily(e.target.value as 'both' | 'heuristic' | 'lightgbm')}
        >
          <option value="both">{t('dash.modelBoth')}</option>
          <option value="heuristic">{t('dash.modelHeuristic')}</option>
          <option value="lightgbm">{t('dash.modelLightgbm')}</option>
        </select>

        <label htmlFor="dashboard-candidate-scope">{t('dash.candidateScope')}</label>
        <select
          id="dashboard-candidate-scope"
          value={candidateScope}
          onChange={(e) => setCandidateScope(e.target.value as 'all' | 'suggested')}
        >
          <option value="all">{t('dash.allCandidates')}</option>
          <option value="suggested">{t('dash.suggestedOnly')}</option>
        </select>

        <h3>{t('dash.topCandidate')}</h3>
        <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('screener.symbol')}</th>
              <th>{t('screener.name')}</th>
              <th>{t('comparison.heuristic')}</th>
              <th>{t('comparison.upProbability')}</th>
              <th>{t('trade.rr')}</th>
              <th>{t('trade.tradeScore')}</th>
              <th>{t('trade.targetPrice')}</th>
              <th>{t('trade.stopLoss')}</th>
              <th>{t('trade.suggested')}</th>
              <th>{t('comparison.lightgbm')}</th>
              <th>{t('comparison.upProbability')}</th>
              <th>{t('trade.rr')}</th>
              <th>{t('trade.tradeScore')}</th>
              <th>{t('trade.targetPrice')}</th>
              <th>{t('trade.stopLoss')}</th>
              <th>{t('trade.suggested')}</th>
            </tr>
          </thead>
          <tbody>
            {topCandidateRows.map((row) => (
              <tr key={row.asset_symbol}>
                <td>{row.asset_symbol}</td>
                <td>{row.asset_name}</td>
                <td>{row.heuristic_label || '--'}</td>
                <td>{row.heuristic_up_probability != null ? `${(Number(row.heuristic_up_probability) * 100).toFixed(1)}%` : '--'}</td>
                <td>{row.heuristic_risk_reward_ratio != null ? Number(row.heuristic_risk_reward_ratio).toFixed(2) : '--'}</td>
                <td>{row.heuristic_trade_score != null ? Number(row.heuristic_trade_score).toFixed(2) : '--'}</td>
                <td>{row.heuristic_target_price != null ? Number(row.heuristic_target_price).toFixed(2) : '--'}</td>
                <td>{row.heuristic_stop_loss_price != null ? Number(row.heuristic_stop_loss_price).toFixed(2) : '--'}</td>
                <td>{row.heuristic_suggested ? t('common.yes') : t('common.no')}</td>
                <td>{row.lightgbm_label || '--'}</td>
                <td>{row.lightgbm_up_probability != null ? `${(Number(row.lightgbm_up_probability) * 100).toFixed(1)}%` : '--'}</td>
                <td>{row.lightgbm_risk_reward_ratio != null ? Number(row.lightgbm_risk_reward_ratio).toFixed(2) : '--'}</td>
                <td>{row.lightgbm_trade_score != null ? Number(row.lightgbm_trade_score).toFixed(2) : '--'}</td>
                <td>{row.lightgbm_target_price != null ? Number(row.lightgbm_target_price).toFixed(2) : '--'}</td>
                <td>{row.lightgbm_stop_loss_price != null ? Number(row.lightgbm_stop_loss_price).toFixed(2) : '--'}</td>
                <td>{row.lightgbm_suggested ? t('common.yes') : t('common.no')}</td>
              </tr>
            ))}
            {topCandidateRows.length === 0 && !loading && (
              <tr>
                <td colSpan={16}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </div>

      <div className="card">
        <h3>{t('dash.allStocks')}</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                {tableColumns.map((column) => (
                  <th key={column.key}>
                    <button type="button" className="table-sort-button" onClick={() => handleSort(column.key)}>
                      {column.label}
                      {sortField === column.key ? (sortDirection === 'asc' ? ' ↑' : ' ↓') : ''}
                    </button>
                  </th>
                ))}
              </tr>
              <tr>
                {tableColumns.map((column) => (
                  <th key={`${column.key}-filter`}>
                    <input
                      value={columnFilters[column.key] ?? ''}
                      onChange={(e) => setColumnFilter(column.key, e.target.value)}
                      placeholder={t('dash.filterColumn')}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {allStocksRows.map((row) => (
                <tr key={row.asset_symbol}>
                  {tableColumns.map((column) => (
                    <td key={`${row.asset_symbol}-${column.key}`}>{renderValue(row[column.key])}</td>
                  ))}
                </tr>
              ))}
              {allStocksRows.length === 0 && !loading && (
                <tr>
                  <td colSpan={tableColumns.length}>{t('common.noData')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
