// Start-of-session calibration badge: a countdown telling the athlete how much
// longer to stand still, then a brief verdict.
//
// ⚠️ It is driven ONLY by the tick's `cal` countdown (and `cal_failed`) — never
// by `warming_up`. That was the bug behind "I stood still for 30 s and it kept
// blinking": `warming_up` is m4/m5's warm-up, which needs 60 s / 30 s of
// MOVEMENT to clear, while calibration needs STILLNESS. The two are mutually
// exclusive, so a badge driven by both could never stop while standing still.
//
// The countdown is BOUNDED (user decision 2026-08-04): when a device appears
// and starts calibrating, the UI latches a wall-clock deadline of
// min(backend cal, CAL_UI_MAX_S) and counts down monotonically — it never
// rises, and at zero the badge disappears REGARDLESS of backend state. The
// backend's `cal` remains the honest accumulator (it rises when the athlete
// moves), but a badge that can blink forever teaches people to ignore it, so
// the UI clamps it to one bounded, finishable ask. If the backend resolves
// within the cap, the usual verdict shows; if the cap expires first, the badge
// just goes away — `uncalibrated`/`carried_over` chips carry the state from
// there.
//
// `carried_over` is deliberately not here either. It means "running last
// session's values, not measured today", which is a state, not a wait — it has
// its own info chip.

import { CheckCircle2, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { OFFLINE_HIDE_MS } from '../lib/config'

/** How long the verdict stays up once calibration resolves. */
const VERDICT_MS = 8_000
/** Hard cap on how long the countdown may stay on screen, whatever the backend says. */
const CAL_UI_MAX_S = 20

export interface CalibrationState {
  mode: 'counting' | 'done' | 'failed' | 'off'
  /** seconds shown on the countdown (mode === 'counting') */
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
  /** wall-clock end of the visible countdown; null when not counting */
  const deadline = useRef<number | null>(null)
  /** how this session's badge ended; blocks re-arming until the device is gone */
  const outcome = useRef<'done' | 'failed' | 'timeout' | null>(null)
  const resolvedAt = useRef<number | null>(null)
  const [, tick] = useState(0)

  const now = Date.now()

  // A real absence (silent past OFFLINE_HIDE_MS) means the device left the UI
  // entirely; its return is a new session and the badge re-arms. Keying on the
  // `online` flag instead would re-arm on a 2 s packet dropout.
  const gone = lastSignalMs == null || now - lastSignalMs > OFFLINE_HIDE_MS
  if (gone) {
    deadline.current = null
    outcome.current = null
    resolvedAt.current = null
  }

  const failed = !!flags?.includes('cal_failed')

  if (!gone && outcome.current == null) {
    if (deadline.current == null) {
      // Arm once per session appearance, from the backend's own estimate but
      // never beyond the UI cap.
      if (online && calLeft != null) {
        deadline.current = now + Math.min(calLeft, CAL_UI_MAX_S) * 1000
      }
    } else if (calLeft == null) {
      // Backend resolved within the cap: show the verdict once, briefly.
      outcome.current = failed ? 'failed' : 'done'
      resolvedAt.current = now
      deadline.current = null
    } else if (now >= deadline.current) {
      // Cap expired with the backend still counting: disappear, no verdict.
      outcome.current = 'timeout'
      deadline.current = null
    }
  }

  const counting = deadline.current != null && !gone
  const showingVerdict =
    resolvedAt.current != null &&
    now - resolvedAt.current < VERDICT_MS &&
    (outcome.current === 'done' || outcome.current === 'failed')

  // repaint at 1 Hz only while something is on screen
  useEffect(() => {
    if (!counting && !showingVerdict) return
    const id = window.setInterval(() => tick((n) => n + 1), 500)
    return () => window.clearInterval(id)
  }, [counting, showingVerdict, deviceId])

  if (counting) {
    return {
      mode: 'counting',
      secondsLeft: Math.max(0, Math.ceil(((deadline.current as number) - now) / 1000)),
    }
  }
  if (showingVerdict) {
    return { mode: outcome.current === 'failed' ? 'failed' : 'done', secondsLeft: 0 }
  }
  return { mode: 'off', secondsLeft: 0 }
}

export default function CalibrationBadge({ state }: { state: CalibrationState }) {
  if (state.mode === 'off') return null

  if (state.mode === 'counting') {
    return (
      <span
        className="chip calibrating"
        title="Calibrating — keep the athlete standing still for the countdown."
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
