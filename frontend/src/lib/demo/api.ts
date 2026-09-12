// Synthetic REST responses for demo soldiers (STAGE4 D4). Every function is a
// pure function of (profile, nowMs) and returns exactly the lib/api.ts shape
// the real route would, so no component can tell the difference. `nowMs` is
// always a parameter: only the lib/api.ts boundary reads the clock.

import type { Device, Sensor } from '../api'
import { valueNoise } from './noise'
import { DEMO_PROFILES, sessionSeconds, type DemoProfile } from './profiles'
import { qualityAt } from './signal'

/** Same layout as the backend's default LIMB_MAP (TRD §3). */
const LIMBS: readonly [number, number, string][] = [
  [0, 1, 'left_shin'],
  [0, 2, 'left_thigh'],
  [1, 1, 'right_thigh'],
  [1, 2, 'right_shin'],
]

/** Millisecond ISO with a Z suffix: byte-identical to the backend's _iso(). */
export function iso(ms: number): string {
  return new Date(ms).toISOString()
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v
}

/** Battery %: the profile's start level minus a linear drain, floored so a
 *  tab left open for a day never reads 0 (which the UI reserves for "dead"). */
export function demoSoc(profile: DemoProfile, s: number): number {
  return clamp(Math.round(profile.soc0 - (profile.socDrainPerHour * s) / 3600), 5, 100)
}

/** Four mapped sensors, each streaming near the measured 640 Hz. */
export function demoSensors(profile: DemoProfile, nowMs: number): Sensor[] {
  const s = sessionSeconds(nowMs)
  return LIMBS.map(([source_id, sensor_id, limb], i) => ({
    source_id,
    sensor_id,
    limb,
    rate_hz: Math.round((639.5 + 1.5 * (valueNoise(profile.seed * 17 + i, s, 30) - 0.5)) * 10) / 10,
    last_seen: iso(nowMs),
  }))
}

/** GET /api/devices rows for the five soldiers: always online, seen now. */
export function demoDevices(nowMs: number): Device[] {
  const s = sessionSeconds(nowMs)
  return DEMO_PROFILES.map((p) => ({
    device_id: p.id,
    display_name: p.display_name,
    online: true,
    last_seen: iso(nowMs),
    quality: qualityAt(p, s),
    soc: demoSoc(p, s),
    sensors: demoSensors(p, nowMs),
  }))
}
