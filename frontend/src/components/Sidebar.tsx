// Left navigation (UIUX §1): logo, overview, live online-device list with a
// mini risk number, connection dot, user + logout. Collapses to a top bar on
// tablet widths (app.css).

import { LayoutGrid, LogOut } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { LogoFull } from './Logo'
import { useAuth } from '../lib/auth'
import { useVisibleDevices } from '../lib/devices'
import { metricValue } from '../lib/format'
import { riskBand, RISK_BAND_META } from '../lib/metrics'
import { useLive } from '../lib/ws'

export default function Sidebar() {
  const { user, logout } = useAuth()
  const { conn, latest } = useLive()
  const { visible } = useVisibleDevices()

  return (
    <aside className="sb">
      <NavLink to="/" className="sb-logo" aria-label="HIPPOS overview">
        <LogoFull className="sb-logo-mark" />
        <span className="eyebrow">Motion Intelligence</span>
      </NavLink>

      <nav className="sb-nav">
        <div className="eyebrow sb-sec">Dashboard</div>
        <NavLink to="/" end className="sb-item">
          <LayoutGrid size={15} aria-hidden />
          Overview
        </NavLink>

        <div className="eyebrow sb-sec">Athletes online</div>
        {visible.length === 0 && <div className="sb-empty">none streaming</div>}
        {visible.map((d) => {
          const c = latest[d.device_id]?.c
          const band = c != null ? RISK_BAND_META[riskBand(c)] : null
          return (
            <NavLink key={d.device_id} to={`/device/${d.device_id}`} className="sb-item sb-dev">
              <span
                className={`dot ${d.online ? 'dot-on' : 'dot-off'}`}
                role="img"
                aria-label={d.online ? 'online' : 'offline'}
              />
              <span className="sb-dev-name">{d.display_name}</span>
              <span
                className="sb-dev-risk"
                style={band ? { color: `var(${band.cssVar})` } : undefined}
                title={c != null ? `Injury Risk ${metricValue(c)} (${band?.label})` : undefined}
              >
                {metricValue(c)}
              </span>
            </NavLink>
          )
        })}
      </nav>

      <div className="sb-bottom">
        <div className="sb-conn">
          <span
            className={`dot ${conn === 'connected' ? 'dot-on' : 'dot-warn'}`}
            role="img"
            aria-label={conn === 'connected' ? 'live connection ok' : 'reconnecting'}
          />
          {conn === 'connected' ? 'live' : 'reconnecting…'}
        </div>
        <div className="sb-user">
          <span className="sb-username">{user?.username}</span>
          <button onClick={() => void logout().then(() => location.assign('/login'))} title="Sign out">
            <LogOut size={15} aria-hidden />
            <span>Sign out</span>
          </button>
        </div>
      </div>
    </aside>
  )
}
