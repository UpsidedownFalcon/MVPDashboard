// Thin fetch layer for the BACKEND_SCHEMA §3 routes. Crude by design (S2-T07).

export interface Sensor {
  source_id: number
  sensor_id: number
  limb: string
  rate_hz: number
  last_seen: string | null
}

export interface Device {
  device_id: string
  display_name: string
  online: boolean
  last_seen: string | null
  quality: number | null
  sensors: Sensor[]
}

export interface WindowEntry {
  window: string
  from: string
  m: (number | null)[]
  composite: { avg: number | null; min: number | null; max: number | null }
  quality: number | null
  trend: 'up' | 'down' | 'flat'
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
  points: ForecastPoint[]
}

export interface Insight {
  insight_id: number
  created_at: string
  device_id: string
  severity: 'info' | 'warning' | 'alert'
  rule_id: string
  message: string
  context: Record<string, unknown> | null
}

export interface Recent {
  device_id: string
  t0: string | null
  rows: [number, ...(number | null)[]][]
}

async function get<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`${resp.status} ${url}`)
  return resp.json()
}

export const fetchDevices = () => get<Device[]>('/api/devices')
export const fetchWindows = (dev: string) =>
  get<{ windows: WindowEntry[] }>(`/api/metrics/windows?device=${dev}`)
export const fetchForecasts = (dev: string) =>
  get<Forecasts>(`/api/forecasts/latest?device=${dev}`)
export const fetchInsights = (dev: string) =>
  get<Insight[]>(`/api/insights?device=${dev}&limit=10`)
export const fetchRecent = (dev: string, seconds: number) =>
  get<Recent>(`/api/metrics/recent?device=${dev}&seconds=${seconds}`)

export async function renameDevice(dev: string, name: string): Promise<Device> {
  const resp = await fetch(`/api/devices/${dev}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: name }),
  })
  if (!resp.ok) throw new Error(`rename failed: ${resp.status}`)
  return resp.json()
}
