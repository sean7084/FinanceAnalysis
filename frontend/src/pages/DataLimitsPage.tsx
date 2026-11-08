import { fetchDataLimits, type DataLimitsDto } from '../lib/api'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

function formatRows(value: number | null): string {
  return value === null ? '-' : value.toLocaleString()
}

export function DataLimitsPage() {
  const { t } = useI18n()
  const resource = useApiResource<DataLimitsDto>(
    'system/data-limits',
    () => fetchDataLimits(),
  )
  const data = resource.data
  const coverage = data?.etf_coverage

  return (
    <section>
      <header className="page-header">
        <h2>{t('dataLimits.title')}</h2>
        <p>{t('dataLimits.desc')}</p>
      </header>

      {resource.loading && <TableSkeleton columns={6} rows={8} />}
      {resource.error && (
        <div className="card">
          <p className="status disconnected">
            {resource.backendUnavailable ? t('dash.backendDown') : t('dataLimits.loadError')}
          </p>
        </div>
      )}

      {coverage && (
        <div className="metric-grid">
          <div className="metric-card">
            <span>{t('dataLimits.listedFunds')}</span>
            <strong>{coverage.listed_funds.toLocaleString()}</strong>
          </div>
          <div className="metric-card">
            <span>{t('dataLimits.eligibleLatest')}</span>
            <strong>{coverage.eligible_on_latest_snapshot.toLocaleString()}</strong>
          </div>
          <div className="metric-card">
            <span>{t('dataLimits.etfFirstBar')}</span>
            <strong>{coverage.earliest_first_trade_date ?? '-'}</strong>
          </div>
          <div className="metric-card">
            <span>{t('dataLimits.maxPositionsTitle')}</span>
            <strong>{data?.max_positions.value ?? '-'}</strong>
          </div>
        </div>
      )}

      <div className="card">
        <h3>{t('dataLimits.extentTitle')}</h3>
        <p>{t('dataLimits.extentDesc')}</p>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('dataLimits.colDataset')}</th>
                <th>{t('dataLimits.colEarliest')}</th>
                <th>{t('dataLimits.colLatest')}</th>
                <th>{t('dataLimits.colRows')}</th>
              </tr>
            </thead>
            <tbody>
              {(data?.datasets ?? []).map((row) => (
                <tr key={row.model}>
                  <td>
                    {row.label}
                    {row.note ? <p>{row.note}</p> : null}
                  </td>
                  <td>{row.earliest ?? '-'}</td>
                  <td>{row.latest ?? '-'}</td>
                  <td>{formatRows(row.rows_estimate)}</td>
                </tr>
              ))}
              {(data?.datasets.length ?? 0) === 0 && !resource.loading && (
                <tr>
                  <td colSpan={4}>{t('common.noData')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3>{t('dataLimits.thresholdTitle')}</h3>
        <p>{t('dataLimits.thresholdDesc')}</p>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('dataLimits.colLimit')}</th>
                <th>{t('dataLimits.colValue')}</th>
                <th>{t('dataLimits.colSource')}</th>
              </tr>
            </thead>
            <tbody>
              {(data?.thresholds ?? []).map((row) => (
                <tr key={row.key}>
                  <td>{row.label}</td>
                  <td>
                    {row.value.toLocaleString()} {row.unit}
                  </td>
                  <td>
                    {row.configured_in}
                    <p>{row.source}</p>
                  </td>
                </tr>
              ))}
              {(data?.thresholds.length ?? 0) === 0 && !resource.loading && (
                <tr>
                  <td colSpan={3}>{t('common.noData')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
