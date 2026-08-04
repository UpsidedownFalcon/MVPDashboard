// Overview device panel (UIUX §3): projected Injury Risk is the headline —
// closest horizon huge, later horizons in a smaller stack; live "now" +
// sparkline secondary; top action-first insight; flags. Whole card clicks
// through to the detail page.

import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchForecasts, fetchInsights, type Insight } from '../lib/api'
import { POLL_FORECASTS_MS, POLL_INSIGHTS_MS } from '../lib/config'
import type { LiveDevice } from '../lib/devices'
import { durationToSeconds, horizonLabel, metricValue, timeAgo } from '../lib/format'
import { COMPOSITE, SEVERITY_RANK } from '../lib/metrics'
import { useLive } from '../lib/ws'
import Battery from './Battery'
import CalibrationBadge, { useCalibrationState } from './CalibrationBadge'
import { FlagChips, QualityMeter, RenameInline, SensorDots, SeverityChip, StatusBadge } from './bits'
import LiveChart from './LiveChart'
import RiskStat from './RiskStat'

function topInsight(insights: Insight[] | undefined): Insight | null {
  if (!insights?.length) return null
  // most-severe recent first; feed is already newest-first
  return [...insights].sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity],
  )[0]
}

export default function DeviceCard({ device }: { device: LiveDevice }) {
  const navigate = useNavigate()
  const { latest, getBuffer } = useLive()
  const live = latest[device.device_id]
  const calibration = useCalibrationState(
    device.device_id, device.online, live?.flags, device.lastSignalMs, live?.cal,
  )

  const forecasts = useQuery({
    queryKey: ['forecasts', device.device_id],
    queryFn: () => fetchForecasts(device.device_id),
    refetchInterval: POLL_FORECASTS_MS,
    retry: false, // 404 = no run yet, not an error
  })
  const insights = useQuery({
    queryKey: ['insights', device.device_id],
    queryFn: () => fetchInsights(device.device_id, 5),
    refetchInterval: POLL_INSIGHTS_MS,
  })

  const points = forecasts.data
    ? [...forecasts.data.points].sort(
        (a, b) => durationToSeconds(a.horizon) - durationToSeconds(b.horizon),
      )
    : []
  const closest = points[0]
  const rest = points.slice(1)
  const top = topInsight(insights.data)

  return (
    <article
      className="card device-card"
      onClick={() => navigate(`/device/${device.device_id}`)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') navigate(`/device/${device.device_id}`)
      }}
      tabIndex={0}
      role="link"
      aria-label={`${device.display_name} details`}
    >
      <header className="device-card-head">
        <RenameInline device={device} />
        <StatusBadge online={device.online} />
        <CalibrationBadge state={calibration} />
        <QualityMeter quality={live?.q ?? device.quality} />
        <SensorDots sensors={device.sensors} />
        {/* battery sits top-right, phone-style */}
        <span className="device-card-battery">
          <Battery soc={device.soc} />
        </span>
      </header>

      {closest ? (
        <div className="device-card-projection">
          <RiskStat
            value={closest.pred}
            label={`Projected risk · ${horizonLabel(closest.horizon)}`}
            size="hero"
            sub={forecasts.data ? `made ${timeAgo(forecasts.data.made_at)}` : undefined}
            title={COMPOSITE.tooltip}
          />
          {rest.length > 0 && (
            <div className="device-card-horizons">
              {rest.map((p) => (
                <RiskStat
                  key={p.horizon}
                  value={p.pred}
                  label={horizonLabel(p.horizon)}
                  size="small"
                />
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="device-card-projection">
          <RiskStat
            value={live?.c ?? null}
            label="Injury risk · now"
            size="hero"
            sub="waiting for the first projection…"
            title={COMPOSITE.tooltip}
          />
        </div>
      )}

      <div className="device-card-now">
        <span className="device-card-now-label">
          now <b>{metricValue(live?.c)}</b>
        </span>
        <div className="device-card-spark" aria-label="Live injury-risk trend, last 30 seconds">
          <LiveChart
            getData={() => getBuffer(device.device_id)}
            seriesIdx={6}
            color={COMPOSITE.color}
            height={42}
            windowS={30}
            frozen={!device.online}
          />
        </div>
      </div>

      {top && (
        <div className="device-card-insight">
          <SeverityChip severity={top.severity} />
          <span className="device-card-insight-text">{top.action ?? top.message}</span>
        </div>
      )}

      <FlagChips flags={live?.flags ?? []} />
    </article>
  )
}
