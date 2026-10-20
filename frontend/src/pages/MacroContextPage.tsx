import { fetchMacroContexts, hasAnyAuthCredential, type MacroContextDto } from '../lib/api'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

export function MacroContextPage() {
  const { t } = useI18n()
  const contextsResource = useApiResource<MacroContextDto[]>(
    'macro/contexts?limit=30',
    () => fetchMacroContexts(30),
  )
  const contexts = contextsResource.data ?? []

  return (
    <section>
      <header className="page-header">
        <h2>{t('macro.title')}</h2>
        <p>{t('macro.desc')}</p>
      </header>
      <div className="card">
        {contextsResource.loading && <TableSkeleton columns={3} rows={6} />}
        {contextsResource.error && (
          <p className="status disconnected">
            {hasAnyAuthCredential() ? t('macro.loadError') : `${t('settings.desc')} (${t('nav.settings')})`}
          </p>
        )}
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
            {contexts.length === 0 && !contextsResource.loading && (
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
