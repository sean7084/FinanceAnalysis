import { useI18n } from '../useI18n'

export function SettingsPage() {
  const { locale, setLocale, t } = useI18n()

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
    </section>
  )
}
