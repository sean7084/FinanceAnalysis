import { useEffect, useState } from 'react'
import { fetchScreenerRows, hasAnyAuthCredential, type CandidateDto } from '../lib/api'
import { useI18n } from '../i18n'

export function ScreenerPage() {
  const { t } = useI18n()
  const [rows, setRows] = useState<CandidateDto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState('bottom_probability_score')

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const data = await fetchScreenerRows(30, sortBy, 7)
        if (alive) {
          setRows(data)
          setError(null)
        }
      } catch {
        if (alive) {
          setError(hasAnyAuthCredential() ? t('screener.loadError') : `${t('settings.desc')} (${t('nav.settings')})`)
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
  }, [sortBy])

  return (
    <section>
      <header className="page-header">
        <h2>{t('screener.title')}</h2>
        <p>{t('screener.desc')}</p>
      </header>
      <div className="card">
        <label htmlFor="screener-sort-by">{t('screener.sortBy')}</label>
        <select id="screener-sort-by" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="bottom_probability_score">{t('screener.bottomProb')}</option>
          <option value="trade_score">{t('screener.tradeScore')}</option>
          <option value="risk_reward_ratio">{t('screener.rr')}</option>
        </select>
        {loading && <p className="status">{t('common.loading')}</p>}
        {error && <p className="status disconnected">{error}</p>}
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('screener.symbol')}</th>
              <th>{t('screener.name')}</th>
              <th>{t('screener.comp')}</th>
              <th>{t('screener.bottomProb')}</th>
              <th>{t('screener.tradeScore')}</th>
              <th>{t('screener.rr')}</th>
              <th>{t('screener.suggested')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.asset_symbol}</td>
                <td>{row.asset_name}</td>
                <td>{Number(row.composite_score).toFixed(2)}</td>
                <td>{(Number(row.bottom_probability_score) * 100).toFixed(1)}%</td>
                <td>{row.trade_score != null ? Number(row.trade_score).toFixed(2) : '--'}</td>
                <td>{row.risk_reward_ratio != null ? Number(row.risk_reward_ratio).toFixed(2) : '--'}</td>
                <td>{row.suggested ? t('common.yes') : t('common.no')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
