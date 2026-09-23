// Top card of Sleeve storage: intro, the unsupported state (decision A:
// Chrome/Edge on a secure context only), Open / Reconnect and the drive's
// validity. Reconnect exists because a handle persisted in IndexedDB comes
// back with permission 'prompt' after a reload: requestPermission() must run
// inside a click (user gesture), never on mount.
import { HardDrive, RefreshCw } from 'lucide-react'
import { fill, STORAGE_COPY } from '../../lib/storage/copy'
import type { DriveState, SupportState } from '../../lib/storage/pageState'

interface Props {
  support: SupportState
  drive: DriveState
  /** A save or transfer is running: the drive must not be swapped. */
  busy: boolean
  onOpen: () => void
  onReconnect: () => void
}

export default function DrivePanel({ support, drive, busy, onOpen, onReconnect }: Props) {
  const c = STORAGE_COPY
  if (support === 'unsupported') {
    return (
      <div className="card storage-card">
        <p className="storage-intro">{c.intro}</p>
        <p className="notice storage-unsupported" role="alert">
          {c.unsupported}
        </p>
      </div>
    )
  }
  const checking = drive.status === 'checking'
  const locked = busy || checking || support !== 'ok'
  return (
    <div className="card storage-card">
      <p className="storage-intro">{c.intro}</p>
      <div className="storage-actions">
        {drive.status === 'reconnect' && (
          <button type="button" className="segment rig-action storage-primary" disabled={locked} onClick={onReconnect}>
            <RefreshCw size={13} aria-hidden />
            {c.drive.reconnect}
          </button>
        )}
        <button
          type="button"
          className={`segment rig-action ${drive.status === 'reconnect' ? '' : 'storage-primary'}`}
          disabled={locked}
          onClick={onOpen}
        >
          <HardDrive size={13} aria-hidden />
          {c.drive.open}
        </button>
        {drive.name && drive.status !== 'invalid' && (
          <span className="storage-drive-name">{fill(c.drive.name, { name: drive.name })}</span>
        )}
        {checking && (
          <span className="notice" role="status">
            {c.drive.checking}
          </span>
        )}
      </div>
      {drive.status === 'invalid' && drive.reason && (
        <p className="rig-error notice" role="alert">
          {fill(c.drive.invalid[drive.reason], { detail: drive.detail ?? '' })}
        </p>
      )}
    </div>
  )
}
