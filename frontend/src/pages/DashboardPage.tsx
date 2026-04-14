import { useEffect, useState } from 'react'
import { ProbabilityChart } from '../components/charts/ProbabilityChart'
import {
  fetchDashboardData,
  fetchPredictionBySymbol,
  fetchScreenerRows,
  hasAnyAuthCredential,
  type CandidateDto,
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
  const [candidateCharts, setCandidateCharts] = useState<Array<{
    symbol: string
    name: string
    data: Array<{ horizon: string; up: number; flat: number; down: number }>
  }>>([])
  const [topN, setTopN] = useState(5)
  const [candidates, setCandidates] = useState<CandidateDto[]>([])

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const [data, rows] = await Promise.all([
          fetchDashboardData(),
          fetchScreenerRows(topN),
        ])

        const predictionRows = await Promise.all(
          rows.map(async (row) => {
            const prediction = await fetchPredictionBySymbol(row.asset_symbol)
            return {
              symbol: row.asset_symbol,
              name: row.asset_name,
              data: (prediction?.results ?? []).map((x) => ({
                horizon: `${x.horizon_days}D`,
                up: Number(x.up),
                flat: Number(x.flat),
                down: Number(x.down),
              })),
            }
          }),
        )

        if (alive) {
          setDashboard(data)
          setCandidates(rows)
          setCandidateCharts(predictionRows)
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
  }, [topN])

  const translatedMetrics = [
    { label: t('dash.macro'), value: dashboard.macroPhase },
    { label: t('dash.hotConcepts'), value: dashboard.hotConcepts },
    { label: t('dash.predSignals'), value: `${dashboard.predictionSignals} ${t('dash.today')}` },
    { label: t('dash.alertTriggers'), value: `${dashboard.alertTriggers} ${t('dash.live')}` },
    { label: t('dash.completedBacktests'), value: String(dashboard.completedBacktests) },
    { label: t('dash.avgBottomProb'), value: `${(dashboard.avgBottomProbability * 100).toFixed(1)}%` },
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

        <h3>{t('dash.topCandidates')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('screener.symbol')}</th>
              <th>{t('screener.name')}</th>
              <th>{t('screener.comp')}</th>
              <th>{t('screener.bottomProb')}</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((row) => (
              <tr key={row.id}>
                <td>{row.asset_symbol}</td>
                <td>{row.asset_name}</td>
                <td>{Number(row.composite_score).toFixed(2)}</td>
                <td>{(Number(row.bottom_probability_score) * 100).toFixed(1)}%</td>
              </tr>
            ))}
            {candidates.length === 0 && !loading && (
              <tr>
                <td colSpan={4}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div>
        <h3>{t('dash.topProbOutlook')}</h3>
        {candidateCharts.map((row) => (
          <ProbabilityChart
            key={row.symbol}
            title={`${t('chart.probability')} · ${row.symbol}${row.name ? ` (${row.name})` : ''}`}
            data={row.data}
          />
        ))}
        {candidateCharts.length === 0 && !loading && <p className="status">{t('common.noData')}</p>}
      </div>
    </section>
  )
}
