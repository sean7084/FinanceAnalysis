import { fetchDateContract, type DateContractDto } from '../lib/api'
import { TableSkeleton } from '../components/layout/TableSkeleton'
import { useApiResource } from '../hooks/useApiResource'
import { useI18n } from '../useI18n'

export function SettingsPage() {
  const { locale, setLocale, t } = useI18n()
  const contractResource = useApiResource<DateContractDto>(
    'system/date-contract',
    () => fetchDateContract(),
  )
  const contract = contractResource.data?.asset_classes ?? []

  return (
    <section>
      <header className="page-header">
        <h2>{t('settings.title')}</h2>
        <p>{t('settings.desc')}</p>
      </header>

      <div className="card">
        <label htmlFor="locale-select">{t('settings.language')}</label>
        <select
          id="locale-select"
          value={locale}
          onChange={(e) => setLocale(e.target.value as 'zh-CN' | 'en-US')}
        >
          <option value="zh-CN">简体中文</option>
          <option value="en-US">English</option>
        </select>
      </div>

      <div className="card">
        <h3>{t('settings.contractTitle')}</h3>
        <p>{t('settings.contractDesc')}</p>
        {contractResource.loading && <TableSkeleton columns={4} rows={2} />}
        {contractResource.error && (
          <p className="status disconnected">
            {contractResource.backendUnavailable
              ? t('dash.backendDown')
              : t('settings.contractLoadError')}
          </p>
        )}
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t('settings.contractAsset')}</th>
                <th>{t('settings.contractRaw')}</th>
                <th>{t('settings.contractTraining')}</th>
                <th>{t('settings.contractBacktest')}</th>
              </tr>
            </thead>
            <tbody>
              {contract.map((row) => (
                <tr key={row.key}>
                  <td>{row.key === 'etf' ? t('settings.contractAssetEtf') : t('settings.contractAssetStocks')}</td>
                  <td>{row.raw_data_from ?? '-'}</td>
                  <td>{row.training_start}</td>
                  <td>{row.backtest_start}</td>
                </tr>
              ))}
              {contract.length === 0 && !contractResource.loading && (
                <tr>
                  <td colSpan={4}>{t('common.noData')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p>{t('settings.contractNote')}</p>
      </div>
    </section>
  )
}
