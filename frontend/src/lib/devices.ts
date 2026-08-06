// Device registry + liveness merge (UIUX §3): REST registry polled every 10s,
// overridden by real-time WS status events and the tick stream itself.
//
// CHANGED 2026-08-06 (user decision): offline devices are no longer hidden.
// Every registered device stays in the sidebar and the overview grid — with
// the offline badge, frozen live column and its stored history, projections
// and insights — sorted online-first. The old rule (silent > OFFLINE_HIDE_MS
// disappears entirely) is gone; OFFLINE_HIDE_MS survives only as the
// calibration badge's re-arm threshold (CalibrationBadge.tsx).

import { useQuery } from '@tanstack/react-query'
import { fetchDevices, type Device } from './api'
import { POLL_DEVICES_MS } from './config'
import { useLive } from './ws'

export interface LiveDevice extends Device {
  /** ms epoch of the freshest signal seen (registry, status event, or tick). */
  lastSignalMs: number | null
}

export interface VisibleDevices {
  /** every registered device, online first then by name */
  visible: LiveDevice[]
  /** how many of them are currently online */
  onlineCount: number
  isLoading: boolean
  isError: boolean
}

export interface MergedDevices {
  devices: LiveDevice[]
  isLoading: boolean
  isError: boolean
}

/** Full registry merged with live signals, in registry order. */
export function useMergedDevices(): MergedDevices {
  const query = useQuery({
    queryKey: ['devices'],
    queryFn: fetchDevices,
    refetchInterval: POLL_DEVICES_MS,
  })
  const { latest, status } = useLive()

  const now = Date.now()
  const devices: LiveDevice[] = (query.data ?? []).map((d) => {
    const evt = status[d.device_id]
    const tickT = latest[d.device_id]?.t
    const candidates = [
      d.last_seen ? Date.parse(d.last_seen) : null,
      evt ? Date.parse(evt.last_seen) : null,
      tickT != null ? tickT * 1000 : null,
    ].filter((v): v is number => v != null)
    const lastSignalMs = candidates.length ? Math.max(...candidates) : null
    // ticks flowing right now beat any stale registry verdict
    const online =
      (tickT != null && now - tickT * 1000 <= 2_500) || (evt ? evt.online : d.online)
    return { ...d, online, lastSignalMs }
  })
  return { devices, isLoading: query.isLoading, isError: query.isError }
}

/** Every registered device, sorted for display: online first, then by name. */
export function useVisibleDevices(): VisibleDevices {
  const { devices, isLoading, isError } = useMergedDevices()
  const visible = [...devices].sort(
    (a, b) =>
      Number(b.online) - Number(a.online) ||
      a.display_name.localeCompare(b.display_name),
  )
  return {
    visible,
    onlineCount: visible.filter((d) => d.online).length,
    isLoading,
    isError,
  }
}
