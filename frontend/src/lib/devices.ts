// Device registry + liveness merge (UIUX §3): REST registry polled every 10s,
// overridden by real-time WS status events and the tick stream itself. A
// device silent > OFFLINE_HIDE_MS disappears from panels and sidebar.

import { useQuery } from '@tanstack/react-query'
import { fetchDevices, type Device } from './api'
import { OFFLINE_HIDE_MS, POLL_DEVICES_MS } from './config'
import { useLive } from './ws'

export interface LiveDevice extends Device {
  /** ms epoch of the freshest signal seen (registry, status event, or tick). */
  lastSignalMs: number | null
}

export interface VisibleDevices {
  visible: LiveDevice[]
  hiddenCount: number
  isLoading: boolean
  isError: boolean
}

export interface MergedDevices {
  devices: LiveDevice[]
  isLoading: boolean
  isError: boolean
}

/** Full registry merged with live signals — includes devices the 10s rule
 *  hides (deep-linked detail pages must not go blank, UIUX §4). */
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

export function useVisibleDevices(): VisibleDevices {
  const { devices, isLoading, isError } = useMergedDevices()
  const now = Date.now()
  const visible = devices
    .filter((d) => d.lastSignalMs != null && now - d.lastSignalMs <= OFFLINE_HIDE_MS)
    .sort(
      (a, b) =>
        Number(b.online) - Number(a.online) ||
        a.display_name.localeCompare(b.display_name),
    )
  return { visible, hiddenCount: devices.length - visible.length, isLoading, isError }
}
