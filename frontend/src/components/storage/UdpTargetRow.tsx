// Decision G: where the sleeve streams, read-only in the Basic view, with a
// one-click "Point at this dashboard". The suffix comes from
// udpTargetStatus(): equal to GET /api/config/udp-target, different, or
// unknown because the api could not resolve its own address.
import { Crosshair } from 'lucide-react'
import { fill, STORAGE_COPY } from '../../lib/storage/copy'
import type { UdpStatus } from '../../lib/storage/pageState'

interface Props {
  ip: string
  port: string
  status: UdpStatus
  /** False when the target ip is null: the button explains why. */
  canPoint: boolean
  disabled: boolean
  onPoint: () => void
}

const SUFFIX_CLASS: Record<UdpStatus, string> = {
  'this-dashboard': 'is-ok',
  other: 'is-other',
  unknown: '',
}

export default function UdpTargetRow({ ip, port, status, canPoint, disabled, onPoint }: Props) {
  const c = STORAGE_COPY.editor.udp
  const suffix =
    status === 'this-dashboard' ? c.thisDashboard : status === 'other' ? c.notThisDashboard : c.unknown
  return (
    <div className="storage-udp">
      <span>
        {fill(c.streamsTo, { ip: ip || c.notSet, port: port || c.notSet })}{' '}
        <span className={`storage-udp-suffix ${SUFFIX_CLASS[status]}`}>{suffix}</span>
      </span>
      <button
        type="button"
        className="segment rig-action"
        disabled={disabled || !canPoint || status === 'this-dashboard'}
        title={canPoint ? undefined : c.pointDisabled}
        onClick={onPoint}
      >
        <Crosshair size={13} aria-hidden />
        {c.point}
      </button>
      {!canPoint && <span className="storage-help">{c.pointDisabled}</span>}
    </div>
  )
}
