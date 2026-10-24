import { useAlertsSocket } from '../hooks/useAlertsSocket'
import { apiGet, type AlertEventDto, type Paginated } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

export function AlertCenterPage() {
  const { t } = useI18n()
  const { connected, reconnecting, messages } = useAlertsSocket()
  const historyResource = useApiResource<AlertEventDto[]>(
    'alerts/history?limit=20',
    async () => (await apiGet<Paginated<AlertEventDto>>('/alert-events/?page_size=20')).results,
    [],
    { ttlMs: 30_000 },
  )
  const history = historyResource.data ?? []

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
        {historyResource.error && (
          <p className="status disconnected">
            {t('alerts.historyError')}
          </p>
        )}
        {historyResource.loading && <p className="status">{t('common.loading')}</p>}
        {historyResource.isRefreshing && <p className="status">{t('common.updating')}</p>}
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
          {history.map((item) => {
            const isFeedStale = item.metadata?.condition_type === 'FEED_STALE'
            const target = item.asset_symbol ?? (isFeedStale ? t('alerts.feedStale') : null)
            return (
              <li key={item.id}>
                <strong>{item.status}</strong>
                <span>
                  {item.alert_name ?? `Alert Event #${item.id}`}
                  {target ? ` · ${target}` : ''}
                </span>
                <em>API</em>
              </li>
            )
          })}
        </ul>
      </div>
    </section>
  )
}
