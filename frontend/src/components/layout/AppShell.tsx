import { NavLink, Outlet } from 'react-router-dom'
import { useI18n } from '../../useI18n'

const navItems = [
  { to: '/', key: 'nav.dashboard', exact: true },
  { to: '/indicator-board', key: 'nav.indicatorBoard', exact: true },
  { to: '/stock/600519', key: 'nav.stock', exact: true },
  { to: '/macro', key: 'nav.macro', exact: true },
  { to: '/models', key: 'nav.models', exact: true },
  { to: '/backtest', key: 'nav.backtest', exact: true },
  // `/etf` must stay exact once `/etf/backtest` exists, or the dashboard link lights up
  // for the workbench too and neither shows where the operator is.
  { to: '/etf', key: 'nav.etf', exact: true },
  { to: '/etf/backtest', key: 'nav.etfBacktest', exact: true },
  { to: '/alerts', key: 'nav.alerts', exact: true },
  { to: '/settings', key: 'nav.settings', exact: true },
]

export function AppShell() {
  const { t } = useI18n()

  return (
    <div className="app-shell">
      <aside className="side-nav">
        <h1>FinanceAnalysis</h1>
        <p className="subtitle">{t('shell.phase')}</p>
        <nav>
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
              end={item.exact}
            >
              {t(item.key)}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="content-area">
        <Outlet />
      </main>
    </div>
  )
}
