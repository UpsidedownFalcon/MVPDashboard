// Start-of-session calibration badge: a countdown telling the athlete how much
// longer to stand still, then a brief verdict.
//
// ⚠️ It is driven ONLY by the tick's `cal` countdown (and `cal_failed`) — never
// by `warming_up`. That was the bug behind "I stood still for 30 s and it kept
// blinking": `warming_up` is m4/m5's warm-up, which needs 60 s / 30 s of
// MOVEMENT to clear, while calibration needs STILLNESS. The two are mutually
// exclusive, so a badge driven by both could never stop while standing still.
//
// `carried_over` is deliberately not here either. It means "running last
// session's values, not measured today", which is a state, not a wait — it has
// its own info chip.
//
// The countdown is computed by the backend from the real per-sensor stillness
// accumulators, so it RISES again if the athlete moves. That is honest:
// movement genuinely costs them the accumulated window.

import { CheckCircle2, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { OFFLINE_HIDE_MS } from '../lib/config'

/** How long the verdict stays up once calibration resolves. */
const VERDICT_MS = 8_000

export interface CalibrationState {
  mode: 'counting' | 'done' | 'failed' | 'off'
  /** seconds of stillness still required (mode === 'counting') */
  secondsLeft: number
}

export function useCalibrationState(
  deviceId: string,
  online: boolean,
  flags: string[] | undefined,
  lastSignalMs: number | null,
  /** the tick's `cal` field: seconds left, or null when nothing is calibrating */
  calLeft: number | null | undefined,
): CalibrationState {
  const wasCounting = useRef(false)
  const resolvedAt = useRef<number | null>(null)
  const [, tick] = useState(0)

  // A real absence (silent past OFFLINE_HIDE_MS) means the device left the UI
  // entirely; its return is a new session and the badge re-arms. Keying on the
  // `online` flag instead would re-arm on a 2 s packet dropout.
  const gone = lastSignalMs == null || Date.now() - lastSignalMs > OFFLINE_HIDE_MS
  if (gone) {
    wasCounting.current = false
    resolvedAt.current = null
  }

  const counting = !gone && online && calLeft != null
  const failed = !!flags?.includes('cal_failed')

  // Latch the moment counting stops, so the verdict is shown once and briefly.
  if (counting) {
    wasCounting.current = true
    resolvedAt.current = null
  } else if (wasCounting.current && resolvedAt.current == null) {
    resolvedAt.current = Date.now()
  }

  const showingVerdict =
    resolvedAt.current != null && Date.now() - resolvedAt.current < VERDICT_MS

  // repaint at 1 Hz only while something is on screen
  useEffect(() => {
    if (!counting && !showingVerdict) return
    const id = window.setInterval(() => tick((n) => n + 1), 500)
    return () => window.clearInterval(id)
  }, [counting, showingVerdict, deviceId])

  if (counting) {
    return { mode: 'counting', secondsLeft: Math.max(0, Math.ceil(calLeft as number)) }
  }
  if (showingVerdict) {
    return { mode: failed ? 'failed' : 'done', secondsLeft: 0 }
  }
  return { mode: 'off', secondsLeft: 0 }
}

export default function CalibrationBadge({ state }: { state: CalibrationState }) {
  if (state.mode === 'off') return null

  if (state.mode === 'counting') {
    return (
      <span
        className="chip calibrating"
        title="Calibrating — keep the athlete standing still. The countdown is the stillness still required; it goes back up if they move, because the window has to be continuous."
        aria-live="polite"
      >
        <span className="calibrating-dot" aria-hidden />
        Stand still · {state.secondsLeft}s
      </span>
    )
  }

  if (state.mode === 'failed') {
    return (
      <span
        className="chip flag flag-alert"
        title="A sensor held still but its reading disagrees with gravity, so the correction was refused and last-known-good values stand. That points at the hardware, not the athlete."
      >
        <TriangleAlert aria-hidden /> Calibration failed
      </span>
    )
  }

  return (
    <span className="chip calibrated" title="Calibrated on this athlete, this session.">
      <CheckCircle2 aria-hidden /> Calibrated
    </span>
  )
}
