// Demo live feed (STAGE4 D2/D3): fills a uPlot-shaped ring buffer per soldier
// on the same absolute 1/60 s grid the real ticker uses, by catching up from
// the buffer's last timestamp to "now" on every tick. The buffers live in
// LiveProvider's separate `demoBuffers` ref, so the WebSocket reconnect /
// tab-return logic (which wipes the real buffer map) never touches them, and
// because every sample is a pure function of wall-clock time, a StrictMode
// double-mount or a throttled hidden tab resumes cleanly with no duplicates.

import { LIVE_BUFFER_S } from '../config'
import type { LiveData } from '../ws'
import { DEMO_PROFILES, SESSION_START_MS, profileFor, sessionSeconds, type DemoProfile } from './profiles'
import { qualityAt, sampleAt } from './signal'

const HZ = 60
/** Timer cadence. Browsers throttle this in hidden tabs; the catch-up logic
 *  below makes that harmless. */
const FEED_INTERVAL_MS = 50

/** Local copy: ws.tsx keeps its own `emptyData` private on purpose, and the
 *  demo layer must never import runtime values from ws (D2 import direction). */
function emptyData(): LiveData {
  return [[], [], [], [], [], [], []] as LiveData
}

/** Advance `buf` to `nowMs`. Idempotent for the same instant; regenerates the
 *  whole window after a gap longer than the buffer; trims with one splice. */
export function fillDemoBuffer(buf: LiveData, profile: DemoProfile, nowMs: number): void {
  const nowK = Math.floor((nowMs / 1000) * HZ)
  const span = HZ * LIVE_BUFFER_S
  const times = buf[0]
  let lastK = times.length ? Math.round(times[times.length - 1] * HZ) : nowK - span - 1
  if (nowK - lastK > span) {
    for (const col of buf) col.length = 0
    lastK = nowK - span
  }
  const sessionStartS = SESSION_START_MS / 1000
  for (let k = lastK + 1; k <= nowK; k++) {
    const t = k / HZ
    const { m, c } = sampleAt(profile, t - sessionStartS)
    times.push(t)
    for (let i = 0; i < 5; i++) (buf[i + 1] as (number | null)[]).push(m[i])
    ;(buf[6] as (number | null)[]).push(c)
  }
  // Keep exactly one window. The grid is uniform, so trimming by count is
  // exact where a float cutoff comparison keeps or drops the boundary sample
  // depending on rounding.
  const drop = times.length - span
  if (drop > 0) for (const col of buf) col.splice(0, drop)
}

/** Start the feed for every scripted soldier. Returns the stop function. */
export function startDemoFeed(
  buffers: Record<string, LiveData>,
  now: () => number = Date.now,
): () => void {
  const step = () => {
    const t = now()
    for (const profile of DEMO_PROFILES) {
      const buf = (buffers[profile.id] ??= emptyData())
      fillDemoBuffer(buf, profile, t)
    }
  }
  step()
  const id: ReturnType<typeof setInterval> = setInterval(step, FEED_INTERVAL_MS)
  return () => clearInterval(id)
}

/** The non-series part of a tick for the 250 ms `latest` snapshot: link
 *  quality, the scripted flag chips, and `cal: null` (never calibrating, so
 *  the calibration badge never arms on a soldier). */
export function demoLatestMeta(
  id: string,
  nowMs: number,
): { q: number; flags: string[]; cal: null } | undefined {
  const profile = profileFor(id)
  if (!profile) return undefined
  return { q: qualityAt(profile, sessionSeconds(nowMs)), flags: profile.flags, cal: null }
}
