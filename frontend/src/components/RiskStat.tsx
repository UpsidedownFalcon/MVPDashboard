// Stat-tile / hero figure for a 0–100 risk value (UIUX §3/§4): big semibold
// sans number (proportional figures — never tabular at display size), tinted
// by risk band, with the band word always beside the color.

import { TrendingDown, TrendingUp } from 'lucide-react'
import { metricValue } from '../lib/format'
import { riskBand, RISK_BAND_META } from '../lib/metrics'

interface Props {
  value: number | null | undefined
  label: string
  /** small line under the number (e.g. "made 2m ago"). */
  sub?: string
  size?: 'hero' | 'big' | 'small'
  trend?: 'up' | 'down' | 'flat' | null
  title?: string
}

export default function RiskStat({ value, label, sub, size = 'big', trend, title }: Props) {
  const band = value != null ? RISK_BAND_META[riskBand(value)] : null
  return (
    <div className={`risk risk-${size}`} title={title}>
      <div className="risk-label">{label}</div>
      <div className="risk-row">
        <span
          className="risk-value"
          style={{ color: band ? `var(${band.cssVar})` : 'var(--ink-3)' }}
        >
          {metricValue(value)}
        </span>
        {band && <span className="risk-band">{band.label}</span>}
        {trend === 'up' && <TrendingUp className="risk-trend" size={16} aria-label="rising" />}
        {trend === 'down' && (
          <TrendingDown className="risk-trend" size={16} aria-label="falling" />
        )}
      </div>
      {sub && <div className="risk-sub">{sub}</div>}
    </div>
  )
}
