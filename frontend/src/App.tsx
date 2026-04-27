import { Suspense, lazy, type ReactElement } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { useI18n } from './i18n'

const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const StockDetailPage = lazy(() => import('./pages/StockDetailPage').then((m) => ({ default: m.StockDetailPage })))
const MacroContextPage = lazy(() => import('./pages/MacroContextPage').then((m) => ({ default: m.MacroContextPage })))
const BacktestWorkbenchPage = lazy(() => import('./pages/BacktestWorkbenchPage').then((m) => ({ default: m.BacktestWorkbenchPage })))
const IndicatorBoardPage = lazy(() => import('./pages/IndicatorBoardPage').then((m) => ({ default: m.IndicatorBoardPage })))
const AlertCenterPage = lazy(() => import('./pages/AlertCenterPage').then((m) => ({ default: m.AlertCenterPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const ModelMonitoringPage = lazy(() => import('./pages/ModelMonitoringPage').then((m) => ({ default: m.ModelMonitoringPage })))

function withSuspense(element: ReactElement) {
  return <Suspense fallback={<RouteLoadingFallback />}>{element}</Suspense>
}

function RouteLoadingFallback() {
  const { t } = useI18n()
  return <div className="card">{t('common.loading')}</div>
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: withSuspense(<DashboardPage />) },
      { path: 'indicator-board', element: withSuspense(<IndicatorBoardPage />) },
      { path: 'stock/:symbol', element: withSuspense(<StockDetailPage />) },
      { path: 'macro', element: withSuspense(<MacroContextPage />) },
      { path: 'models', element: withSuspense(<ModelMonitoringPage />) },
      { path: 'backtest', element: withSuspense(<BacktestWorkbenchPage />) },
      { path: 'alerts', element: withSuspense(<AlertCenterPage />) },
      { path: 'settings', element: withSuspense(<SettingsPage />) },
    ],
  },
])

function App() {
  return <RouterProvider router={router} />
}

export default App
