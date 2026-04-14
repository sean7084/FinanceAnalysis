import { useEffect, useState } from 'react'
import { fetchMacroContexts, hasAnyAuthCredential, type MacroContextDto } from '../lib/api'
import { useI18n } from '../i18n'

export function MacroContextPage() {
  const { t } = useI18n()
  const [contexts, setContexts] = useState<MacroContextDto[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        setLoading(true)
        const data = await fetchMacroContexts(30)
        if (alive) {
          setContexts(data)
          setError(null)
        }
      } catch {
        if (alive) {
          setError(hasAnyAuthCredential() ? t('macro.loadError') : `${t('settings.desc')} (${t('nav.settings')})`)
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
        <h2>{t('macro.title')}</h2>
        <p>{t('macro.desc')}</p>
      </header>
      <div className="card">
        {loading && <p className="status">{t('common.loading')}</p>}
        {error && <p className="status disconnected">{error}</p>}
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('macro.phaseHeader')}</th>
              <th>{t('macro.eventTagHeader')}</th>
              <th>{t('macro.statusHeader')}</th>
            </tr>
          </thead>
          <tbody>
            {contexts.map((ctx) => (
              <tr key={ctx.id}>
                <td>{ctx.macro_phase}</td>
                <td>{ctx.event_tag || t('macro.noEventTag')}</td>
                <td>{ctx.is_active ? t('macro.active') : t('macro.inactive')}</td>
              </tr>
            ))}
            {contexts.length === 0 && !loading && (
              <tr>
                <td colSpan={3}>{t('common.noData')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
