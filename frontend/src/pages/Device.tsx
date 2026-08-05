// Device detail (UIUX §4): LIVE left column (data-driven figure + current-risk
// hero + composite chart + stacked primitives) and the Insights / History /
// Projections tabs on the right. Offline: charts freeze under a last-seen
// overlay; a deep-linked hidden device still renders.

import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  FlagChips,
  QualityMeter,
  RenameInline,
  SensorDots,
  StatusBadge,
} from '../components/bits'
import Battery from '../components/Battery'
import CalibrationBadge, { useCalibrationState } from '../components/CalibrationBadge'
import ForecastChart from '../components/ForecastChart'
import HistoryBars from '../components/HistoryBars'
import HumanoidFigure, { type LimbState } from '../components/HumanoidFigure'
import InsightsPanel from '../components/InsightsPanel'
import LiveChart from '../components/LiveChart'
import RiskStat from '../components/RiskStat'
import Tabs from '../components/Tabs'
import { fetchWindows } from '../lib/api'
import { POLL_HISTORY_MS } from '../lib/config'
import { useMergedDevices } from '../lib/devices'
import { boundedMetricValue, clockTime, metricValue } from '../lib/format'
import {
  COMPOSITE,
  FLAG_META,
  isSignedMetric,
  m5Side,
  m5SideLabel,
  METRICS,
  metricMagnitude,
} from '../lib/metrics'
import { useLive } from '../lib/ws'

/** Why is this primitive null right now? Ordered by severity (UIUX §6:
 *  warming_up must never look like degraded_sensors). */
function nullReason(flags: string[]): { label: string; weight: string } {
  for (const f of ['degraded_sensors', 'partial', 'saturated', 'warming_up']) {
    if (flags.includes(f)) {
      const meta = FLAG_META[f]
      return { label: meta.label, weight: meta.weight }
    }
  }
  return { label: 'no data', weight: 'muted' }
}

export default function Device() {
  const { id = '' } = useParams()
  const { devices, isLoading } = useMergedDevices()
  const { latest, getBuffer } = useLive()
  const [tab, setTab] = useState('insights')

  const device = devices.find((d) => d.device_id === id)
  const live = latest[id]

  const windowsQuery = useQuery({
    queryKey: ['windows', id],
    queryFn: () => fetchWindows(id),
    refetchInterval: POLL_HISTORY_MS,
    enabled: !!device,
  })
  const windowLabels = useMemo(
    () => windowsQuery.data?.windows.map((w) => w.window) ?? [],
    [windowsQuery.data],
  )
  const shortestTrend = windowsQuery.data?.windows[0]?.trend ?? null

  const limbs = useMemo(() => {
    if (!device) return {}
    const now = Date.now()
    const out: Record<string, LimbState> = {}
    for (const s of device.sensors) {
      const seen = s.last_seen ? now - Date.parse(s.last_seen) : Infinity
      out[s.limb] = seen <= 10_000 ? 'good' : s.last_seen ? 'warning' : 'critical'
    }
    return out
  }, [device])

  // Hooks must run unconditionally — this sat below the early returns once,
  // which changed the hook order the moment loading resolved or a device
  // appeared (React rules-of-hooks violation).
  const calibration = useCalibrationState(
    id, device?.online ?? false, live?.flags, device?.lastSignalMs ?? null, live?.cal,
  )

  if (isLoading) return <p className="notice">Loading…</p>
  if (!device) {
    return (
      <div className="card empty-state">
        <p>Unknown device.</p>
        <Link to="/" className="segment">
          <ArrowLeft size={14} aria-hidden /> Back to overview
        </Link>
      </div>
    )
  }

  const getData = () => getBuffer(id)
  // signed m5 -> which side is carrying more load right now ('even' and null
  // both mean "no emphasis")
  const side = m5Side(live?.m[4] ?? null)
  const loadSide: 'left' | 'right' | null =
    side === 'left' || side === 'right' ? side : null

  return (
    <div className="device-page">
      <header className="device-head">
        <Link to="/" className="back" aria-label="Back to overview">
          <ArrowLeft size={15} aria-hidden />
        </Link>
        <RenameInline device={device} />
        <StatusBadge online={device.online} />
        <CalibrationBadge state={calibration} />
        <QualityMeter quality={live?.q ?? device.quality} />
        <SensorDots sensors={device.sensors} detailed />
        <FlagChips flags={live?.flags ?? []} />
        {/* battery, top-right */}
        <span className="device-head-battery">
          <Battery soc={device.soc} />
        </span>
      </header>

      <div className="device-cols">
        <section className="device-live card" aria-label="Live metrics">
          {!device.online && (
            <div className="offline-overlay">
              <div>
                <b>offline</b>
                {device.last_seen && <> — last seen {clockTime(device.last_seen)}</>}
              </div>
            </div>
          )}

          <div className="device-live-top">
            <HumanoidFigure
              variant="compact"
              limbs={limbs}
              active={device.online}
              // ambient emphasis on the side currently carrying more load;
              // the m5 row states it in words (SPEC §5.5: neutral, in-session)
              emphasis={loadSide}
            />
            <div className="device-live-risk">
              <RiskStat
                value={live?.c ?? null}
                label="Injury risk · now"
                size="hero"
                trend={shortestTrend}
                title={COMPOSITE.tooltip}
              />
              <p className="claims-note">Computed live from every impact and stride.</p>
            </div>
          </div>

          <div className="live-main">
            <div className="live-main-head">
              <span className="swatch" style={{ background: COMPOSITE.color }} aria-hidden />
              {COMPOSITE.label}
              <span className="live-main-val">{metricValue(live?.c)}</span>
            </div>
            <LiveChart
              getData={getData}
              seriesIdx={6}
              color={COMPOSITE.color}
              height={180}
              windowS={60}
              axes
              bandLines
              frozen={!device.online}
            />
          </div>

          <div className="live-stack">
            {METRICS.map((m, i) => {
              const raw = live?.m[i] ?? null
              const signed = isSignedMetric(m.id)
              // m5 is signed: show the MAGNITUDE as the value and the sign as a
              // neutral side label (SPEC §5.5 — never "weaker", never
              // cross-session). Its chart keeps the full −100..100 domain.
              const value = signed ? metricMagnitude(m.id, raw) : raw
              const side = signed ? m5SideLabel(raw) : null
              const reason = raw == null ? nullReason(live?.flags ?? []) : null
              return (
                <div key={m.id} className={`live-row ${raw == null ? 'is-null' : ''}`}>
                  <div className="live-row-head" title={m.tooltip}>
                    <span className="swatch" style={{ background: m.color }} aria-hidden />
                    <span className="live-row-label">{m.label}</span>
                    {reason ? (
                      <span className={`chip flag flag-${reason.weight}`}>{reason.label}</span>
                    ) : (
                      <>
                        {side && <span className="live-row-side">{side}</span>}
                        <span className="live-row-val">
                          {boundedMetricValue(m.id, value, live?.flags)}
                        </span>
                      </>
                    )}
                  </div>
                  <LiveChart
                    getData={getData}
                    seriesIdx={i + 1}
                    color={m.color}
                    height={56}
                    windowS={60}
                    frozen={!device.online}
                    yRange={signed ? [-100, 100] : [0, 100]}
                    zeroLine={signed}
                  />
                </div>
              )
            })}
          </div>
        </section>

        <section className="device-side" aria-label="Insights, history and projections">
          <Tabs
            label="Device analysis"
            active={tab}
            onChange={setTab}
            tabs={[
              {
                id: 'insights',
                label: 'Insights',
                content: <InsightsPanel device={id} />,
              },
              {
                id: 'history',
                label: 'History',
                content:
                  windowsQuery.data && windowsQuery.data.windows.length > 0 ? (
                    <HistoryBars device={id} windows={windowsQuery.data.windows} />
                  ) : (
                    <p className="notice">Loading…</p>
                  ),
              },
              {
                id: 'projections',
                label: 'Projections',
                content: <ForecastChart device={id} windows={windowLabels} />,
              },
            ]}
          />
        </section>
      </div>
    </div>
  )
}
