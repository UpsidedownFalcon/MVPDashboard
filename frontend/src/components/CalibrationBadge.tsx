// Start-of-session calibration indicator: a pulsing icon plus elapsed time,
// shown only while a freshly-online device is still settling.
//
// ⚠️ Why elapsed and NOT a countdown or progress bar. Calibration has no
// knowable duration (biomech SPEC §3.8): it discards the first 3 s, then needs
// 10 s of CONTINUOUS stillness, resets to zero on any movement or dropout, and
// never times out — it just keeps seeking. The per-sensor accumulators exist
// but are not published. A progress bar would therefore be fiction; counting
// up is the honest form.
//
// "Only at the very start when the device is first turned on" (user decision):
// once the settling flags clear for a device, this latches OFF for the rest of
// that session, so m4's later per-band re-warms do not re-trigger it. The latch
// resets when the device disappears (offline > OFFLINE_HIDE_MS) and returns.

import { Loader } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

/** Flags that mean "this device is still settling after power-on". */
const SETTLING_FLAGS = ['uncalibrated', 'warming_up']

export interface CalibrationState {
  /** show the badge */
  active: boolean
  /** seconds since the device first appeared online this session */
  elapsedS: number
}

/** Tracks first-seen time and the done-latch for one device. */
export function useCalibrationState(
  deviceId: string,
  online: boolean,
  flags: string[] | undefined,
): CalibrationState {
  const firstSeen = useRef<number | null>(null)
  const done = useRef(false)
  const [, tick] = useState(0)

  const settling = !!flags?.some((f) => SETTLING_FLAGS.includes(f))

  // reset the whole latch when the device goes away and comes back
  if (!online) {
    firstSeen.current = null
    done.current = false
  } else if (firstSeen.current == null) {
    firstSeen.current = Date.now()
  }
  // latch off the first time it settles — later re-warms must not re-trigger
  if (online && firstSeen.current != null && !settling && Date.now() - firstSeen.current > 1500) {
    done.current = true
  }

  const active = online && !done.current && settling

  // 1 Hz repaint only while the badge is up
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(id)
  }, [active, deviceId])

  return {
    active,
    elapsedS: firstSeen.current ? Math.floor((Date.now() - firstSeen.current) / 1000) : 0,
  }
}

function clock(totalS: number): string {
  const m = Math.floor(totalS / 60)
  const s = totalS % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function CalibrationBadge({ state }: { state: CalibrationState }) {
  if (!state.active) return null
  return (
    <span
      className="chip calibrating"
      title="Settling after power-on: the model is learning this athlete's baseline and watching for a still moment to calibrate against. It finishes on its own — there is no fixed duration, because it needs 10 seconds of continuous stillness and restarts if the athlete moves."
      aria-live="polite"
    >
      <Loader className="calibrating-spin" aria-hidden />
      Calibrating · {clock(state.elapsedS)}
    </span>
  )
}
