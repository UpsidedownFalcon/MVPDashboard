// Battery state of charge — phone-style icon + percentage.
//
// The value arriving on /api/devices is already the LOWEST of the device's two
// leg MCUs (backend publish.py), so a flat unit cannot hide behind a healthy
// one. `null` means no datagram has carried a reading yet — render nothing
// rather than a misleading 0%.

import { Zap } from 'lucide-react'

const LOW = 20
const CRITICAL = 10

export default function Battery({ soc }: { soc: number | null | undefined }) {
  if (soc == null) return null
  const pct = Math.max(0, Math.min(100, soc))
  const state = pct <= CRITICAL ? 'critical' : pct <= LOW ? 'low' : 'ok'
  const color =
    state === 'critical'
      ? 'var(--status-critical)'
      : state === 'low'
        ? 'var(--status-warning)'
        : 'var(--ink-2)'

  return (
    <span
      className={`battery battery-${state}`}
      title={`Battery ${pct}% (lowest of the two leg sensors)`}
      role="img"
      aria-label={`Battery ${pct} percent`}
    >
      <span className="battery-shell" style={{ borderColor: color }} aria-hidden>
        <span className="battery-fill" style={{ width: `${pct}%`, background: color }} />
        {state === 'critical' && <Zap className="battery-bolt" aria-hidden />}
      </span>
      <span className="battery-cap" style={{ background: color }} aria-hidden />
      <span className="battery-pct" style={{ color }}>
        {pct}%
      </span>
    </span>
  )
}
