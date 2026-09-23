// Typed fetch layer for every BACKEND_SCHEMA §3 route. Any 401 outside /login
// redirects to /login (APPFLOW §1.1) — the httpOnly session cookie rides along
// automatically on same-origin requests.

import {
  demoAdviceTimeline,
  demoCurrentAdvice,
  demoDecision,
  demoDevices,
  demoForecasts,
  demoHistory,
  demoInsights,
  demoRecent,
  demoWindows,
} from './demo/api'
import { isDemoId } from './demo/ids'
import type { Severity } from './metrics'

export interface Sensor {
  source_id: number
  sensor_id: number
  limb: string
  rate_hz: number
  last_seen: string | null
  /** Which sleeve this sensor sits on; absent or null on a bilateral rig. */
  unit_id?: string | null
}

/** One knee sleeve: a single MCU with two sensors on ONE leg. `rig_id`,
 *  `rig_display_name` and `paired` come from GET /api/units (the pair picker);
 *  the `units` array inside a device row carries the rest. Sleeve packets do
 *  not carry their full scale, so accel_fs_g / gyro_fs_dps are configuration
 *  the dashboard owns, not a measurement. */
export interface Unit {
  unit_id: string
  /** null until an operator sets it - never guessed (decision G). */
  side: 'left' | 'right' | null
  accel_fs_g: number
  gyro_fs_dps: number
  online: boolean
  last_seen: string | null
  soc: number | null
  /** GET /api/units only */
  rig_id?: string
  rig_display_name?: string
  paired?: boolean
}

export interface Device {
  device_id: string
  display_name: string
  online: boolean
  last_seen: string | null
  quality: number | null
  /** Battery state of charge 0–100, already the LOWEST across the device's two
   *  leg MCUs (a dying unit must not hide behind a healthy one). `null` until a
   *  datagram has been seen. */
  soc: number | null
  sensors: Sensor[]
  /** OPTIONAL, additive 2026-09-23: absent means bilateral. lib/rig.ts is the
   *  only place that interprets it, and it treats undefined as bilateral so
   *  the demo layer (bilateral by decision L) needs no change. */
  kind?: 'bilateral' | 'unilateral'
  /** The sleeves making up this rig: one when unpaired, two when paired.
   *  Absent or empty on a bilateral rig. */
  units?: Unit[]
}

export interface WindowEntry {
  window: string
  from: string
  m: (number | null)[]
  /** within-window std dev of m1..m5 (additive, 2026-08-03). */
  sd: (number | null)[]
  composite: { avg: number | null; min: number | null; max: number | null; sd: number | null }
  quality: number | null
  /** observed rows ÷ expected rows for the window, 0..1. A "past 1h" average
   *  built from 4 minutes of streaming is not comparable to a full hour —
   *  surface this, never silently. */
  coverage: number | null
  trend: 'up' | 'down' | 'flat'
}

export interface HistoryBucket {
  t: string
  m: (number | null)[]
  composite: { avg: number | null; min: number | null; max: number | null }
  quality: number | null
}

export interface History {
  device_id: string
  window: string
  from: string
  bucket_s: number
  buckets: (HistoryBucket | null)[]
}

export interface ForecastPoint {
  horizon: string
  target_time: string
  pred: number
  ci_low: number | null
  ci_high: number | null
}

export interface Forecasts {
  made_at: string
  model_version: string
  /** True while the forecast is still bootstrapping from raw buckets
   *  (`trend-ols-boot-1`). Both models produce a genuine statistical prediction
   *  interval, so the band label is unaffected — but an early projection must
   *  not look as settled as a mature one. */
  provisional?: boolean
  /** Horizon set is NOT fixed: it starts 1m/2m and becomes 10m/30m/1h. Always
   *  read horizons from these points; never assume a count. */
  points: ForecastPoint[]
}

export interface Insight {
  insight_id: number
  created_at: string
  device_id: string
  severity: Severity
  rule_id: string
  message: string
  context: Record<string, unknown> | null
  action: string | null
  rationale: string | null
}

/** One supporting reason behind an action. `text` is a complete short sentence
 *  sized to render in full as a bullet — never truncate it, never hide it
 *  behind an expander (BACKEND_SCHEMA §3). */
export interface AdviceReason {
  rule_id: string
  severity: Severity
  text: string
  unvalidated: boolean
  created_at: string
}

/** One imperative headline. `action` is rendered verbatim — never re-worded
 *  or appended to. `unvalidated` is true only when EVERY reason behind it
 *  comes from m4/m5; demo posture (2026-08-05) means the UI currently does
 *  not render the marker. */
export interface AdviceAction {
  action_id: string
  action: string
  /** Static coaching cue from the action catalogue — the SAME text every time
   *  this action fires and NOT derived from this athlete's data. Rendered
   *  under the "Coaching cue" label. */
  tip: string | null
  severity: Severity
  updated_at: string
  unvalidated: boolean
  reasons: AdviceReason[]
  /** timeline only: the newest decision on this card, null when undecided */
  decision?: InsightDecision | null
}

/** GET /api/insights/current — the STATE view: the advice standing right now.
 *  `actions` is 0..max_actions, ordered strongest severity first; the backend
 *  has already deduped, ranked and capped, so the client must not repeat any
 *  of that. An empty `actions` array is a normal, calm state. */
export interface CurrentAdvice {
  device_id: string
  generated_at: string
  hold_s: number
  max_actions: number
  actions: AdviceAction[]
}

/** The newest Adopt/Override decision on one advice card (migration 004).
 *  Decisions are changeable — the server keeps every press, newest wins. */
export interface InsightDecision {
  decision: 'adopted' | 'overridden'
  /** override only: what the trainer did instead; null = no comment */
  note: string | null
  decided_by: string | null
  decided_at: string
}

/** One age bucket of the advice timeline. `window` is "live" or a PAST_WINDOWS
 *  label ("5m", "30m", "2h") — config strings, never hardcoded durations. */
export interface AdviceBucket {
  window: string
  actions: AdviceAction[]
}

/** /api/insights/timeline — the reload-safe advice history (2026-08-06):
 *  /current's live actions plus stored insights bucketed over the same
 *  PAST_WINDOWS as the historical metrics. Buckets arrive newest-first,
 *  actions newest-first within each; ≤ max_actions per bucket. */
export interface AdviceTimeline {
  device_id: string
  generated_at: string
  hold_s: number
  max_actions: number
  windows: string[]
  buckets: AdviceBucket[]
}

export interface Recent {
  device_id: string
  t0: string | null
  rows: [number, ...(number | null)[]][]
}

export interface Me {
  username: string
  role: string
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

/** Hard redirect to /login (full reload clears all client state). Exported for
 *  the WS layer, which signals expiry via close code 4401. */
export function authExpired(): void {
  if (!location.pathname.startsWith('/login')) {
    location.assign('/login')
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  if (resp.status === 401 && !url.startsWith('/api/auth/login')) {
    authExpired()
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${url}`
    try {
      const body = (await resp.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail)
  }
  return resp.json() as Promise<T>
}

const post = <T,>(url: string, body?: unknown): Promise<T> =>
  request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

// Demo soldiers (STAGE4 D4): every per-device helper answers a `demo-` id
// from lib/demo without a request, so the same components serve real and
// synthetic devices and the network never learns a soldier exists.
export const fetchDevices = () => request<Device[]>('/api/devices')
export const fetchWindows = (dev: string) =>
  isDemoId(dev)
    ? Promise.resolve(demoWindows(dev, Date.now()))
    : request<{ windows: WindowEntry[] }>(`/api/metrics/windows?device=${dev}`)
export const fetchHistory = (dev: string, window: string, buckets: number) =>
  isDemoId(dev)
    ? Promise.resolve(demoHistory(dev, window, buckets, Date.now()))
    : request<History>(
        `/api/metrics/history?device=${dev}&window=${encodeURIComponent(window)}&buckets=${buckets}`,
      )
export const fetchForecasts = (dev: string) =>
  isDemoId(dev)
    ? Promise.resolve(demoForecasts(dev, Date.now()))
    : request<Forecasts>(`/api/forecasts/latest?device=${dev}`)
export const fetchInsights = async (dev?: string, limit = 20): Promise<Insight[]> => {
  const now = Date.now()
  if (dev !== undefined) {
    return isDemoId(dev)
      ? demoInsights(dev, limit, now)
      : request<Insight[]>(`/api/insights?device=${dev}&limit=${limit}`)
  }
  // Squad-wide feed (hero alert count): real rows merged with the soldiers'.
  // A failed real fetch must not hide the soldiers' alerts; a 401 still
  // redirects to /login inside request() before we get here.
  const real = await request<Insight[]>(`/api/insights?limit=${limit}`).catch((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) throw e
    return [] as Insight[]
  })
  return [...real, ...demoInsights(undefined, limit, now)]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, limit)
}
/** device is REQUIRED - advice is per soldier. */
export const fetchCurrentAdvice = (dev: string) =>
  isDemoId(dev)
    ? Promise.resolve(demoCurrentAdvice(dev, Date.now()))
    : request<CurrentAdvice>(`/api/insights/current?device=${dev}`)
export const fetchAdviceTimeline = (dev: string) =>
  isDemoId(dev)
    ? Promise.resolve(demoAdviceTimeline(dev, Date.now()))
    : request<AdviceTimeline>(`/api/insights/timeline?device=${dev}`)
export const postInsightDecision = (body: {
  device_id: string
  action_id: string
  action_updated_at: string
  decision: 'adopted' | 'overridden'
  note?: string
}) =>
  isDemoId(body.device_id)
    ? Promise.resolve(demoDecision(body, Date.now()))
    : post<InsightDecision>('/api/insights/decisions', body)
export const fetchRecent = (dev: string, seconds: number) =>
  isDemoId(dev)
    ? Promise.resolve(demoRecent(dev, seconds, Date.now()))
    : request<Recent>(`/api/metrics/recent?device=${dev}&seconds=${seconds}`)

export const renameDevice = (dev: string, display_name: string) => {
  if (isDemoId(dev)) {
    // a soldier keeps its name (STAGE4 R2); RenameInline never reaches here
    const device = demoDevices(Date.now()).find((d) => d.device_id === dev)
    return device
      ? Promise.resolve(device)
      : Promise.reject(new ApiError(404, `unknown device ${dev}`))
  }
  return request<Device>(`/api/devices/${dev}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name }),
  })
}

// Sleeve units (PLAN_unilateral_devices §6). Same demo guard as renameDevice:
// a demo soldier is bilateral (decision L) and carries no units, so these
// resolve locally and the network never learns it exists.
const demoUnitError = (id: string) =>
  new ApiError(404, `${id} is a demo soldier - bilateral, with no sleeve units`)

/** GET /api/units - every known sleeve, for the pair picker. `rig` is only the
 *  demo guard for the rig asking (the route itself takes no parameters). */
export const fetchUnits = (rig?: string): Promise<Unit[]> =>
  rig !== undefined && isDemoId(rig)
    ? Promise.resolve([])
    : request<Unit[]>('/api/units')

/** PATCH /api/units/{id} - side and full-scale. 409 when the side is already
 *  taken in the rig, 422 when a full-scale value is outside the allowed set. */
export const patchUnit = (
  unitId: string,
  body: { side?: 'left' | 'right' | null; accel_fs_g?: number; gyro_fs_dps?: number },
) =>
  isDemoId(unitId)
    ? Promise.reject(demoUnitError(unitId))
    : request<Unit>(`/api/units/${unitId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

/** POST /api/units/{host}/pair - the host keeps its id and history, the joiner
 *  becomes a member and its own row disappears until unpaired (decision H). */
export const pairUnit = (
  hostUnitId: string,
  body: { unit_id: string; side: 'left' | 'right'; host_side?: 'left' | 'right' },
) =>
  isDemoId(hostUnitId)
    ? Promise.reject(demoUnitError(hostUnitId))
    : post<Device>(`/api/units/${hostUnitId}/pair`, body)

/** POST /api/units/{id}/unpair - releases the member, which returns with its
 *  own history. Sides are kept. */
export const unpairUnit = (unitId: string) =>
  isDemoId(unitId)
    ? Promise.reject(demoUnitError(unitId))
    : post<{ units: Unit[] }>(`/api/units/${unitId}/unpair`)

/** GET /api/config/udp-target (PLAN_msd_management decision G): where the
 *  knee sleeves should stream to. `ip` is null when the api could not resolve
 *  DOMAIN and no UDP_PUBLIC_IP override is set; `source` says which it used. */
export interface UdpTarget {
  ip: string | null
  port: number
  source: 'env' | 'dns' | 'unresolved'
}
export const fetchUdpTarget = () => request<UdpTarget>('/api/config/udp-target')

export const login = (username: string, password: string) =>
  post<Me>('/api/auth/login', { username, password })
export const logout = () => post<Record<string, never>>('/api/auth/logout')
export const fetchMe = () => request<Me>('/api/auth/me')
