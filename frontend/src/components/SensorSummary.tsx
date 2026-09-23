// Sensor summary (STAGE4 R1, 2026-09-12): the quality meter and the per-sensor
// rate readout collapse behind one static line. On the detail page a
// double-arrow toggle swaps the real readout in place; overview cards get the
// static line only, with no way to expand. The state is never persisted (the
// detail page remounts this per soldier via `key`), and nothing renders while
// the device is offline.

import { ChevronsLeft, ChevronsRight } from 'lucide-react'
import { useState, type MouseEvent } from 'react'
import type { Device } from '../lib/api'
import { sensorSummaryText } from '../lib/rig'
import { QualityMeter, SensorDots } from './bits'

interface Props {
  /** `kind`/`units` are what make the line rig-aware; both are optional, so a
   *  device that carries neither reads as bilateral (the R1 literal). */
  device: Pick<Device, 'online' | 'sensors' | 'kind' | 'units' | 'device_id'>
  quality: number | null
  /** detail page only: show the toggle that reveals the real readout */
  expandable?: boolean
}

export default function SensorSummary({ device, quality, expandable = false }: Props) {
  // hook before the early return: hooks must run unconditionally
  const [open, setOpen] = useState(false)
  if (!device.online) return null

  const toggle = (e: MouseEvent<HTMLButtonElement>) => {
    // defensive: keep a parent click-through card (overview) from navigating
    e.preventDefault()
    e.stopPropagation()
    setOpen((o) => !o)
  }
  const expanded = expandable && open
  const Icon = expanded ? ChevronsLeft : ChevronsRight
  const toggleLabel = expanded ? 'Hide sensor detail' : 'Show sensor detail'

  return (
    <span className="sensor-summary">
      {expandable && (
        <button
          type="button"
          className="sensor-summary-toggle"
          aria-expanded={expanded}
          aria-label={toggleLabel}
          title={toggleLabel}
          onClick={toggle}
        >
          <Icon size={13} aria-hidden />
        </button>
      )}
      {expanded ? (
        <>
          <QualityMeter quality={quality} />
          <SensorDots sensors={device.sensors} detailed />
        </>
      ) : (
        <span className="sensor-summary-text">{sensorSummaryText(device)}</span>
      )}
    </span>
  )
}
