import { NavLink, Outlet } from 'react-router-dom'
import { useI18n } from '../../i18n'

const navItems = [
  { to: '/', key: 'nav.dashboard' },
  { to: '/stock/600519', key: 'nav.stock' },
  { to: '/screener', key: 'nav.screener' },
  { to: '/macro', key: 'nav.macro' },
  { to: '/backtest', key: 'nav.backtest' },
  { to: '/alerts', key: 'nav.alerts' },
  { to: '/settings', key: 'nav.settings' },
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
              end={item.to === '/'}
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
