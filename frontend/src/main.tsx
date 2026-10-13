import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { I18nProvider } from './i18n.tsx'
import { bootstrapSession } from './lib/api.ts'

// Probe the HttpOnly refresh cookie and, when present, exchange it for an
// in-memory access token before the first render. This avoids a 401 round-trip
// on the first authenticated API call and lets pages render the correct
// signed-in / signed-out state immediately. bootstrapSession never rejects.
void bootstrapSession().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <I18nProvider>
        <App />
      </I18nProvider>
    </StrictMode>,
  )
})
