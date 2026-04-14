import { useEffect, useState } from 'react'
import { fetchBacktestRuns, hasAnyAuthCredential, type BacktestRunDto } from '../lib/api'
import { useI18n } from '../i18n'

export function BacktestWorkbenchPage() {
  const { t } = useI18n()
  const [runs, setRuns] = useState<BacktestRunDto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const data = await fetchBacktestRuns(20)
        if (alive) {
          setRuns(data)
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

  return (
    <section>
      <header className="page-header">
        <h2>{t('backtest.title')}</h2>
        <p>{t('backtest.desc')}</p>
      </header>
      <div className="card">
        {loading && <p className="status">{t('common.loading')}</p>}
        {error && <p className="status disconnected">{error}</p>}
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('backtest.runId')}</th>
              <th>{t('backtest.strategy')}</th>
              <th>{t('backtest.status')}</th>
              <th>{t('backtest.return')}</th>
              <th>{t('backtest.sharpe')}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>#{run.id}</td>
                <td>{run.strategy_type}</td>
                <td>{run.status}</td>
                <td>{run.status === 'COMPLETED' && run.total_return !== null ? `${(Number(run.total_return) * 100).toFixed(2)}%` : '--'}</td>
                <td>{run.status === 'COMPLETED' && run.sharpe_ratio !== null ? Number(run.sharpe_ratio).toFixed(2) : '--'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
