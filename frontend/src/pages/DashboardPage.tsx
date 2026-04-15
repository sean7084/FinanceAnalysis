import { useEffect, useState } from 'react'
import {
  fetchDashboardData,
  fetchLightGBMPredictionBySymbol,
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
  const [comparisonRows, setComparisonRows] = useState<Array<{
    key: string
    symbol: string
    name: string
    horizon: string
    heuristicLabel: string
    heuristicConfidence: string
    heuristicUp: string
    lightgbmLabel: string
    lightgbmConfidence: string
    lightgbmUp: string
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
            const [prediction, lightgbmPrediction] = await Promise.all([
              fetchPredictionBySymbol(row.asset_symbol),
              fetchLightGBMPredictionBySymbol(row.asset_symbol),
            ])

            const comparison = new Map<number, {
              heuristicLabel: string
              heuristicConfidence: string
              heuristicUp: string
              lightgbmLabel: string
              lightgbmConfidence: string
              lightgbmUp: string
            }>()

            for (const result of prediction?.results ?? []) {
              comparison.set(result.horizon_days, {
                heuristicLabel: result.predicted_label,
                heuristicConfidence: `${(Number(result.confidence) * 100).toFixed(1)}%`,
                heuristicUp: `${(Number(result.up) * 100).toFixed(1)}%`,
                lightgbmLabel: '--',
                lightgbmConfidence: '--',
                lightgbmUp: '--',
              })
            }

            for (const result of lightgbmPrediction?.results ?? []) {
              const existing = comparison.get(result.horizon_days) ?? {
                heuristicLabel: '--',
                heuristicConfidence: '--',
                heuristicUp: '--',
                lightgbmLabel: '--',
                lightgbmConfidence: '--',
                lightgbmUp: '--',
              }
              comparison.set(result.horizon_days, {
                ...existing,
                lightgbmLabel: result.predicted_label,
                lightgbmConfidence: `${(Number(result.confidence) * 100).toFixed(1)}%`,
                lightgbmUp: `${(Number(result.up) * 100).toFixed(1)}%`,
              })
            }

            return {
              comparisonRows: Array.from(comparison.entries())
                .sort((left, right) => left[0] - right[0])
                .map(([horizonDays, values]) => ({
                  key: `${row.asset_symbol}-${horizonDays}`,
                  symbol: row.asset_symbol,
                  name: row.asset_name,
                  horizon: `${horizonDays}D`,
                  ...values,
                })),
            }
          }),
        )

        if (alive) {
          setDashboard(data)
          setCandidates(rows)
          setComparisonRows(predictionRows.flatMap((row) => row.comparisonRows))
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

      <div className="card">
        <h3>{t('dash.modelComparison')}</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('screener.symbol')}</th>
              <th>{t('screener.name')}</th>
              <th>{t('models.horizon')}</th>
              <th>{t('comparison.heuristic')}</th>
              <th>{t('models.confidence')}</th>
              <th>{t('comparison.upProbability')}</th>
              <th>{t('comparison.lightgbm')}</th>
              <th>{t('models.confidence')}</th>
              <th>{t('comparison.upProbability')}</th>
            </tr>
          </thead>
          <tbody>
            {comparisonRows.map((row) => (
              <tr key={row.key}>
                <td>{row.symbol}</td>
                <td>{row.name}</td>
                <td>{row.horizon}</td>
                <td>{row.heuristicLabel}</td>
                <td>{row.heuristicConfidence}</td>
                <td>{row.heuristicUp}</td>
                <td>{row.lightgbmLabel}</td>
                <td>{row.lightgbmConfidence}</td>
                <td>{row.lightgbmUp}</td>
              </tr>
            ))}
            {comparisonRows.length === 0 && !loading && (
              <tr>
                <td colSpan={9}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
