import { useEffect, useState } from 'react'
import { useAlertsSocket } from '../hooks/useAlertsSocket'
import { apiGet, hasAnyAuthCredential, type AlertEventDto, type Paginated } from '../lib/api'
import { useI18n } from '../i18n'

export function AlertCenterPage() {
  const { t } = useI18n()
  const { connected, reconnecting, messages } = useAlertsSocket()
  const [history, setHistory] = useState<AlertEventDto[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const payload = await apiGet<Paginated<AlertEventDto>>('/alert-events/?page_size=20')
        if (alive) {
          setHistory(payload.results)
          setError(null)
        }
      } catch {
        if (alive) {
          setError(hasAnyAuthCredential() ? t('alerts.historyError') : `${t('settings.desc')} (${t('nav.settings')})`)
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
        <h2>{t('alerts.title')}</h2>
        <p>{t('alerts.desc')}</p>
      </header>
      <div className="card">
        <p className={connected ? 'status connected' : 'status disconnected'}>
          {connected ? t('alerts.connected') : reconnecting ? t('alerts.reconnecting') : t('alerts.disconnected')}
        </p>
        {error && <p className="status disconnected">{error}</p>}
        <ul className="stack-list">
          {messages.length === 0 && <li><span>{t('alerts.noLive')}</span></li>}
          {messages.map((msg) => (
            <li key={msg.id}>
              <strong>{msg.level}</strong>
              <span>{msg.title}</span>
              <em>{new Date(msg.time).toLocaleTimeString()}</em>
            </li>
          ))}
        </ul>
        <h3>{t('alerts.history')}</h3>
        <ul className="stack-list">
          {history.map((item) => (
            <li key={item.id}>
              <strong>{item.status}</strong>
              <span>Alert Event #{item.id}</span>
              <em>API</em>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
