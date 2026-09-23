// The post-save panel (PLAN_msd_management 4.2): a host eject does NOT end
// the sleeve's session, only unplugging does (s3), so the wording is always
// "eject, then unplug". Adds the power-cycle note for WiFi keys, the decision
// I full-scale sync outcome and the identity-change notice.
import { CircleCheck } from 'lucide-react'
import { fill, STORAGE_COPY } from '../../lib/storage/copy'
import type { SavedInfo } from '../../lib/storage/pageState'

export default function EjectNotice({ saved }: { saved: SavedInfo }) {
  const c = STORAGE_COPY.editor.saved
  return (
    <div className="storage-saved" role="status">
      <p className="storage-saved-title">
        <CircleCheck size={16} aria-hidden />
        {c.title}
      </p>
      <p>{c.body}</p>
      {saved.needsPowerCycle && <p className="storage-saved-note">{c.powerCycle}</p>}
      {saved.fsSynced && (
        <p className="storage-saved-note">{fill(c.fsSynced, { unit: saved.fsSynced })}</p>
      )}
      {saved.fsSyncFailed && (
        <p className="storage-warn" role="alert">
          {fill(c.fsSyncFailed, saved.fsSyncFailed)}
        </p>
      )}
      {saved.identityChanged && (
        <p className="storage-saved-note">{fill(c.identityChanged, saved.identityChanged)}</p>
      )}
    </div>
  )
}
