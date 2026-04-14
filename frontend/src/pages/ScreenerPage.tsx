import { useEffect, useState } from 'react'
import { fetchScreenerRows, hasAnyAuthCredential, type CandidateDto } from '../lib/api'
import { useI18n } from '../i18n'

export function ScreenerPage() {
  const { t } = useI18n()
  const [rows, setRows] = useState<CandidateDto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const data = await fetchScreenerRows(30)
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
  }, [])

  return (
    <section>
      <header className="page-header">
        <h2>{t('screener.title')}</h2>
        <p>{t('screener.desc')}</p>
      </header>
      <div className="card">
        {loading && <p className="status">{t('common.loading')}</p>}
        {error && <p className="status disconnected">{error}</p>}
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
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.asset_symbol}</td>
                <td>{row.asset_name}</td>
                <td>{Number(row.composite_score).toFixed(2)}</td>
                <td>{(Number(row.bottom_probability_score) * 100).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
