import { useState } from 'react'
import { AuthSettingsPanel } from '../components/layout/AuthSettingsPanel'
import { readApiKey, readAuthToken } from '../lib/api'
import { useI18n } from '../i18n'

export function SettingsPage() {
  const { locale, setLocale, t } = useI18n()
  const [jwtConfigured, setJwtConfigured] = useState(Boolean(readAuthToken()))
  const [apiKeyConfigured, setApiKeyConfigured] = useState(Boolean(readApiKey()))

  const refreshAuthStatus = () => {
    setJwtConfigured(Boolean(readAuthToken()))
    setApiKeyConfigured(Boolean(readApiKey()))
  }

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
        <h3>{t('settings.authStatusTitle')}</h3>
        <div className="auth-status-grid">
          <p>{t('settings.jwtStatus')}</p>
          <p className={`status-chip ${jwtConfigured ? 'connected' : 'disconnected'}`}>
            {jwtConfigured ? t('settings.configured') : t('settings.notConfigured')}
          </p>
          <p>{t('settings.apiKeyStatus')}</p>
          <p className={`status-chip ${apiKeyConfigured ? 'connected' : 'disconnected'}`}>
            {apiKeyConfigured ? t('settings.configured') : t('settings.notConfigured')}
          </p>
        </div>
      </div>

      <AuthSettingsPanel onAuthChange={refreshAuthStatus} />
    </section>
  )
}