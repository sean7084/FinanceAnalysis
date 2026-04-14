import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiRequestError,
  clearAuthSettings,
  createDeveloperApiKey,
  obtainJwtToken,
  readApiKey,
  readAuthPersistenceMode,
  readAuthToken,
  saveAuthSettings,
  type AuthPersistenceMode,
} from '../../lib/api'
import { useI18n } from '../../i18n'

interface AuthSettingsPanelProps {
  onAuthChange?: () => void
}

export function AuthSettingsPanel({ onAuthChange }: AuthSettingsPanelProps) {
  const { t } = useI18n()
  const [token, setToken] = useState(readAuthToken())
  const [apiKey, setApiKey] = useState(readApiKey())
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
  const [mode, setMode] = useState<AuthPersistenceMode>(readAuthPersistenceMode())
  const [savedAt, setSavedAt] = useState<string>('')
  const [apiKeyName, setApiKeyName] = useState('frontend-ui')
  const [showToken, setShowToken] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [toast, setToast] = useState('')
  const toastTimerRef = useRef<number | null>(null)

  const showToast = (text: string) => {
    setToast(text)
    if (toastTimerRef.current !== null) {
      window.clearTimeout(toastTimerRef.current)
    }
    toastTimerRef.current = window.setTimeout(() => {
      setToast('')
      toastTimerRef.current = null
    }, 1800)
  }

  useEffect(() => {
    return () => {
      if (toastTimerRef.current !== null) {
        window.clearTimeout(toastTimerRef.current)
      }
    }
  }, [])

  const maskSecret = (value: string): string => {
    if (!value) return ''
    if (value.length <= 8) return `${value.slice(0, 2)}***`
    return `${value.slice(0, 4)}...${value.slice(-4)}`
  }

  const copyToClipboard = async (value: string) => {
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      showToast(t('settings.copySuccess'))
    } catch {
      showToast(t('settings.copyFail'))
    }
  }

  const storageHint = useMemo(() => {
    if (mode === 'local') return t('settings.persistence.local')
    if (mode === 'session') return t('settings.persistence.session')
    return t('settings.persistence.none')
  }, [mode, t])

  const withErrorCode = (base: string, error: unknown) => {
    if (error instanceof ApiRequestError) {
      if (error.detail) {
        return `${base} (HTTP ${error.status}: ${error.detail})`
      }
      return `${base} (HTTP ${error.status})`
    }
    return base
  }

  const onSave = () => {
    saveAuthSettings(token, apiKey, mode)
    setSavedAt(new Date().toLocaleTimeString())
    setMessage('')
    if (mode === 'none') {
      setToken('')
      setApiKey('')
    }
    onAuthChange?.()
  }

  const onClear = () => {
    clearAuthSettings()
    setToken('')
    setApiKey('')
    setSavedAt('')
    setMessage('')
    onAuthChange?.()
  }

  const onGetJwt = async () => {
    try {
      const access = await obtainJwtToken(username.trim(), password)
      let generatedApiKey = apiKey
      let apiKeyError: unknown = null

      try {
        const baseName = apiKeyName.trim() || 'frontend-ui'
        generatedApiKey = await createDeveloperApiKey(baseName, access)
        setApiKey(generatedApiKey)
      } catch (err) {
        apiKeyError = err
        // Retry with a unique suffix to avoid potential key-name conflicts.
        try {
          const uniqueName = `${(apiKeyName.trim() || 'frontend-ui')}-${Date.now()}`
          generatedApiKey = await createDeveloperApiKey(uniqueName, access)
          setApiKeyName(uniqueName)
          setApiKey(generatedApiKey)
        } catch (retryErr) {
          apiKeyError = retryErr
          // Keep JWT even when API key generation fails.
        }
      }

      setToken(access)
      saveAuthSettings(access, generatedApiKey, mode)
      setSavedAt(new Date().toLocaleTimeString())

      if (generatedApiKey) {
        setMessage(t('settings.jwtAndApiKeySuccess'))
      } else {
        setMessage(withErrorCode(t('settings.jwtSuccessApiKeyFail'), apiKeyError))
      }
      onAuthChange?.()
    } catch (err) {
      setMessage(withErrorCode(t('settings.jwtFail'), err))
    }
  }

  const onGenerateApiKey = async () => {
    try {
      const rawKey = await createDeveloperApiKey(apiKeyName.trim() || 'frontend-ui', token)
      setApiKey(rawKey)
      saveAuthSettings(token, rawKey, mode)
      setSavedAt(new Date().toLocaleTimeString())
      setMessage(t('settings.apiKeySuccess'))
      onAuthChange?.()
    } catch (err) {
      setMessage(withErrorCode(t('settings.apiKeyFail'), err))
    }
  }

  return (
    <section className="auth-panel card">
      <h3>{t('settings.authTitle')}</h3>
      <p className="auth-help">{t('settings.authHelp')}</p>

      <label htmlFor="username-input">{t('settings.user')}</label>
      <input
        id="username-input"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        placeholder="admin"
      />

      <label htmlFor="password-input">{t('settings.pass')}</label>
      <input
        id="password-input"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="******"
      />

      <div className="auth-actions">
        <button type="button" onClick={onGetJwt}>{t('settings.getJwt')}</button>
      </div>

      <label htmlFor="jwt-input">{t('settings.jwt')}</label>
      <div className="credential-row">
        <input
          id="jwt-input"
          value={showToken ? token : maskSecret(token)}
          onChange={(e) => {
            if (showToken) {
              setToken(e.target.value)
            }
          }}
          placeholder="Bearer token value only"
          readOnly={!showToken}
        />
        <button type="button" onClick={() => setShowToken((v) => !v)}>{showToken ? t('settings.hide') : t('settings.show')}</button>
        <button type="button" onClick={() => copyToClipboard(token)}>{t('settings.copy')}</button>
      </div>
      {!showToken && <p className="auth-help">{t('settings.hiddenReadonlyHint')}</p>}

      <label htmlFor="apikey-input">{t('settings.apiKey')}</label>
      <div className="credential-row">
        <input
          id="apikey-input"
          value={showApiKey ? apiKey : maskSecret(apiKey)}
          onChange={(e) => {
            if (showApiKey) {
              setApiKey(e.target.value)
            }
          }}
          placeholder="fa-..."
          readOnly={!showApiKey}
        />
        <button type="button" onClick={() => setShowApiKey((v) => !v)}>{showApiKey ? t('settings.hide') : t('settings.show')}</button>
        <button type="button" onClick={() => copyToClipboard(apiKey)}>{t('settings.copy')}</button>
      </div>
      {!showApiKey && <p className="auth-help">{t('settings.hiddenReadonlyHint')}</p>}

      <label htmlFor="apikey-name-input">{t('settings.apiKeyName')}</label>
      <input
        id="apikey-name-input"
        value={apiKeyName}
        onChange={(e) => setApiKeyName(e.target.value)}
        placeholder="frontend-ui"
      />

      <div className="auth-actions">
        <button type="button" onClick={onGenerateApiKey}>{t('settings.genApiKey')}</button>
      </div>

      <label htmlFor="persist-mode">{t('settings.persistence')}</label>
      <select
        id="persist-mode"
        value={mode}
        onChange={(e) => setMode(e.target.value as AuthPersistenceMode)}
      >
        <option value="local">{t('settings.persistence.local')}</option>
        <option value="session">{t('settings.persistence.session')}</option>
        <option value="none">{t('settings.persistence.none')}</option>
      </select>

      <p className="auth-help">{storageHint}</p>

      <div className="auth-actions">
        <button type="button" onClick={onSave}>{t('settings.save')}</button>
        <button type="button" onClick={onClear}>{t('settings.clear')}</button>
      </div>

      {savedAt && <p className="auth-help">{t('settings.savedAt')}: {savedAt}</p>}
      {message && <p className="auth-help">{message}</p>}
      {toast && <div className="toast-banner">{toast}</div>}
    </section>
  )
}
