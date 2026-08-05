// Projections tab (UIUX §4 tab 3): composite-only. Per-horizon stat row, then
// recent actuals continuing into forecast points with a band. ⚠ The band is
// labelled via lib/format's forecastBandLabel/forecastBandNote — under
// 'dose-scenario-*' models it is a scenario range, NOT a confidence interval,
// and calling it CI would make a probabilistic claim SPEC §2 forbids.

import { useQuery } from '@tanstack/react-query'
import type { EChartsOption } from 'echarts'
import { Table2 } from 'lucide-react'
import { useState } from 'react'
import { fetchForecasts, fetchHistory } from '../lib/api'
import { HISTORY_MAX_BUCKETS, POLL_FORECASTS_MS } from '../lib/config'
import {
  clockTime,
  durationToSeconds,
  evenBucketCount,
  forecastBandLabel,
  forecastBandNote,
  horizonLabel,
  metricValue,
  timeAgo,
} from '../lib/format'
import { COMPOSITE } from '../lib/metrics'
import EChart from './EChart'
import RiskStat from './RiskStat'

const AXIS_INK = '#898781'
const GRIDLINE = 'rgba(255,255,255,0.06)'

export default function ForecastChart({
  device,
  windows,
}: {
  device: string
  windows: string[]
}) {
  const [showTable, setShowTable] = useState(false)

  const forecasts = useQuery({
    queryKey: ['forecasts', device],
    queryFn: () => fetchForecasts(device),
    refetchInterval: POLL_FORECASTS_MS,
    retry: false,
  })
  // recent actuals for context: the shortest configured window
  const actuals = useQuery({
    queryKey: ['history', device, windows[0]],
    queryFn: () =>
      fetchHistory(device, windows[0], evenBucketCount(windows[0], HISTORY_MAX_BUCKETS)),
    refetchInterval: POLL_FORECASTS_MS,
    enabled: windows.length > 0,
  })

  if (forecasts.isLoading) return <p className="notice">Loading projections…</p>
  if (!forecasts.data) {
    // Bootstrapped forecasts arrive in ~2.5-3.5 min, not the old 15-20, so the
    // wait is short enough to name.
    return <p className="notice">First projection in a couple of minutes…</p>
  }

  const fc = forecasts.data
  const points = [...fc.points].sort(
    (a, b) => durationToSeconds(a.horizon) - durationToSeconds(b.horizon),
  )
  const bandLabel = forecastBandLabel(fc.model_version)
  const bandNote = forecastBandNote(fc.model_version)

  const actualSeries: [number, number][] = (actuals.data?.buckets ?? [])
    .filter((b): b is NonNullable<typeof b> => b != null && b.composite.avg != null)
    .map((b) => [Date.parse(b.t), b.composite.avg as number])

  const predSeries: [number, number][] = points.map((p) => [
    Date.parse(p.target_time),
    p.pred,
  ])
  const bandLow: [number, number][] = []
  const bandSpan: [number, number][] = []
  // anchor the band at the last actual so it fans out from "now"
  const anchor = actualSeries.length
    ? actualSeries[actualSeries.length - 1]
    : predSeries.length
      ? predSeries[0]
      : null
  if (anchor) {
    bandLow.push([anchor[0], anchor[1]])
    bandSpan.push([anchor[0], 0])
  }
  for (const p of points) {
    if (p.ci_low != null && p.ci_high != null) {
      const t = Date.parse(p.target_time)
      bandLow.push([t, p.ci_low])
      bandSpan.push([t, p.ci_high - p.ci_low])
    }
  }

  const option: EChartsOption = {
    animation: false,
    grid: { left: 34, right: 12, top: 12, bottom: 24 },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: GRIDLINE } },
      axisLabel: { color: AXIS_INK, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      interval: 25,
      axisLabel: { color: AXIS_INK, fontSize: 10 },
      splitLine: { lineStyle: { color: GRIDLINE } },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1b1b1b',
      borderColor: 'rgba(255,255,255,0.14)',
      textStyle: { color: '#ffffff', fontSize: 12 },
      valueFormatter: (v) => metricValue(v as number, 1),
    },
    series: [
      // band: transparent floor + tinted span stacked on it
      {
        type: 'line',
        data: bandLow,
        stack: 'band',
        lineStyle: { opacity: 0 },
        symbol: 'none',
        silent: true,
        tooltip: { show: false },
      },
      {
        type: 'line',
        data: bandSpan,
        stack: 'band',
        lineStyle: { opacity: 0 },
        symbol: 'none',
        areaStyle: { color: COMPOSITE.color, opacity: 0.1 },
        silent: true,
        tooltip: { show: false },
        name: bandLabel,
      },
      {
        type: 'line',
        name: 'actual',
        data: actualSeries,
        showSymbol: false,
        lineStyle: { color: COMPOSITE.color, width: 2 },
      },
      {
        type: 'line',
        name: 'projected',
        data: anchor ? [anchor, ...predSeries] : predSeries,
        lineStyle: { color: COMPOSITE.color, width: 2, type: 'dashed' },
        symbol: 'circle',
        symbolSize: 9,
        itemStyle: { color: COMPOSITE.color, borderColor: '#0d0d0d', borderWidth: 2 },
      },
    ],
  }

  return (
    <div className="forecast">
      {/* Demo posture (2026-08-05): the "early projection · treat as
          provisional" banner is not rendered — `fc.provisional` still arrives
          from the API for when the hedged framing returns. */}
      <div className="forecast-stats">
        {points.map((p, i) => (
          <RiskStat
            key={p.horizon}
            value={p.pred}
            label={`Projected ${horizonLabel(p.horizon)}`}
            size={i === 0 ? 'big' : 'small'}
            sub={
              p.ci_low != null && p.ci_high != null
                ? `${bandLabel} ${metricValue(p.ci_low)}–${metricValue(p.ci_high)}`
                : undefined
            }
          />
        ))}
        <button
          className={`segment table-toggle ${showTable ? 'is-active' : ''}`}
          aria-pressed={showTable}
          onClick={() => setShowTable((s) => !s)}
          title="Table view"
        >
          <Table2 size={14} aria-hidden /> table
        </button>
      </div>

      {!showTable ? (
        <EChart option={option} height={220} ariaLabel="Recent injury risk and projections" />
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>horizon</th>
                <th>target time</th>
                <th>projected</th>
                <th>{bandLabel}</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.horizon}>
                  <td>{horizonLabel(p.horizon)}</td>
                  <td>{clockTime(p.target_time)}</td>
                  <td>{metricValue(p.pred, 1)}</td>
                  <td>
                    {p.ci_low != null && p.ci_high != null
                      ? `${metricValue(p.ci_low, 1)}–${metricValue(p.ci_high, 1)}`
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="forecast-note">
        {bandNote} Made {timeAgo(fc.made_at)} · {fc.model_version}
      </p>
    </div>
  )
}
