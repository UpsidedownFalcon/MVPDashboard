// Overview (UIUX §3): collapsible hero (figure + plain-words explainer +
// live squad stats) above the device panel grid. One quick look = the full
// picture; projections lead.

import { useQueries, useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import DeviceCard from '../components/DeviceCard'
import HumanoidFigure from '../components/HumanoidFigure'
import { fetchForecasts, fetchInsights } from '../lib/api'
import { POLL_FORECASTS_MS, POLL_INSIGHTS_MS } from '../lib/config'
import { useVisibleDevices } from '../lib/devices'
import { durationToSeconds, horizonLabel, metricValue } from '../lib/format'
import { COMPOSITE, METRICS, riskBand, RISK_BAND_META } from '../lib/metrics'
import { useLive } from '../lib/ws'

const HERO_KEY = 'hippos:hero-collapsed'

function useSquadStats(deviceIds: string[], names: Record<string, string>) {
  const { latest } = useLive()
  const forecastQueries = useQueries({
    queries: deviceIds.map((dev) => ({
      queryKey: ['forecasts', dev],
      queryFn: () => fetchForecasts(dev),
      refetchInterval: POLL_FORECASTS_MS,
      retry: false,
    })),
  })
  const alerts = useQuery({
    queryKey: ['insights', 'all'],
    queryFn: () => fetchInsights(undefined, 20),
    refetchInterval: POLL_INSIGHTS_MS,
  })

  let topProjected: { dev: string; value: number; horizon: string } | null = null
  forecastQueries.forEach((q, i) => {
    const points = q.data?.points
    if (!points?.length) return
    const closest = [...points].sort(
      (a, b) => durationToSeconds(a.horizon) - durationToSeconds(b.horizon),
    )[0]
    if (!topProjected || closest.pred > topProjected.value) {
      topProjected = { dev: deviceIds[i], value: closest.pred, horizon: closest.horizon }
    }
  })
  // fallback while no forecasts exist yet: highest current risk
  if (!topProjected) {
    for (const dev of deviceIds) {
      const c = latest[dev]?.c
      if (c != null && (!topProjected || c > (topProjected as { value: number }).value)) {
        topProjected = { dev, value: c, horizon: '' }
      }
    }
  }
  const recentAlerts =
    alerts.data?.filter(
      (i) =>
        i.severity === 'alert' && Date.now() - Date.parse(i.created_at) < 30 * 60_000,
    ).length ?? 0

  return { topProjected: topProjected as { dev: string; value: number; horizon: string } | null, recentAlerts, names }
}

function Hero({ deviceIds, names }: { deviceIds: string[]; names: Record<string, string> }) {
  const { topProjected, recentAlerts } = useSquadStats(deviceIds, names)
  const band = topProjected ? RISK_BAND_META[riskBand(topProjected.value)] : null

  return (
    <div className="hero-body">
      <div className="hero-figure">
        <HumanoidFigure variant="hero" />
      </div>
      <div className="hero-copy">
        <div className="eyebrow">Bilateral · 4 sensors · live</div>
        <h1>
          Every impact, every stride —<br />
          <span className="hero-accent">turned into injury-risk insight.</span>
        </h1>
        <p>
          Sensors on each thigh and shin stream motion hundreds of times a second.
          We distill it into six scores — how hard impacts land, how abruptly load
          is applied, how much has accumulated, how steady the movement stays and
          how evenly left and right share the work — combined into one{' '}
          <b>Injury Risk</b> trend that rises when the same work starts costing
          more than it should. A monitoring aid that flags who is worth a look,
          now and in the coming session — not a prediction.
        </p>
        <ul className="hero-legend" aria-label="Metric legend">
          {[COMPOSITE, ...METRICS].map((m) => (
            <li key={m.id} title={m.tooltip}>
              <span className="swatch" style={{ background: `var(${m.cssVar})` }} aria-hidden />
              {m.label}
            </li>
          ))}
        </ul>
      </div>
      <div className="hero-stats">
        <div className="hero-stat">
          <div className="hero-stat-label">Athletes online</div>
          <div className="hero-stat-value">{deviceIds.length}</div>
        </div>
        <div className="hero-stat">
          <div className="hero-stat-label">
            {topProjected?.horizon
              ? `Highest projected · ${horizonLabel(topProjected.horizon)}`
              : 'Highest risk now'}
          </div>
          <div
            className="hero-stat-value"
            style={band ? { color: `var(${band.cssVar})` } : undefined}
          >
            {topProjected ? metricValue(topProjected.value) : '—'}
          </div>
          {topProjected && (
            <div className="hero-stat-sub">{names[topProjected.dev] ?? topProjected.dev}</div>
          )}
        </div>
        <div className="hero-stat">
          <div className="hero-stat-label">Alerts · 30 min</div>
          <div
            className="hero-stat-value"
            style={recentAlerts ? { color: 'var(--status-critical)' } : undefined}
          >
            {recentAlerts}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Overview() {
  const { visible, hiddenCount, isLoading, isError } = useVisibleDevices()
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(HERO_KEY) === '1')
  const toggle = () => {
    setCollapsed((c) => {
      localStorage.setItem(HERO_KEY, c ? '0' : '1')
      return !c
    })
  }

  const names = Object.fromEntries(visible.map((d) => [d.device_id, d.display_name]))

  return (
    <div className="overview">
      <section className={`hero ${collapsed ? 'hero-collapsed' : ''}`}>
        <button className="hero-toggle" onClick={toggle} aria-expanded={!collapsed}>
          <span className="eyebrow">About this data</span>
          {collapsed ? <ChevronDown size={14} aria-hidden /> : <ChevronUp size={14} aria-hidden />}
        </button>
        {!collapsed && (
          <Hero deviceIds={visible.map((d) => d.device_id)} names={names} />
        )}
      </section>

      {isError && <p className="notice">Failed to load devices — retrying…</p>}
      {isLoading && <p className="notice">Loading devices…</p>}

      {!isLoading && visible.length === 0 && (
        <div className="card empty-state">
          <p>
            <b>Waiting for devices…</b> point wearables at this server's UDP port.
          </p>
          {hiddenCount > 0 && (
            <p className="notice">
              {hiddenCount} offline device{hiddenCount > 1 ? 's' : ''} hidden
            </p>
          )}
        </div>
      )}

      <div className="device-grid">
        {visible.map((d) => (
          <DeviceCard key={d.device_id} device={d} />
        ))}
      </div>
      {visible.length > 0 && hiddenCount > 0 && (
        <p className="notice">
          {hiddenCount} offline device{hiddenCount > 1 ? 's' : ''} hidden
        </p>
      )}
    </div>
  )
}
