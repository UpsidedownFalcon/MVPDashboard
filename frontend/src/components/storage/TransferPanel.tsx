// Destination, keep-copies, Start/Cancel and the run-level progress of a
// transfer (PLAN_msd_management 4.3, decisions J and K). Per-item lines live
// in LogList; this card owns the overall bar, rate, ETA, the "do not unplug"
// notice while a run is live and the summary afterwards. Nothing here deletes
// or writes: the engine does, and only after a copy verified.
import { FolderOpen, Play, RefreshCw, X } from 'lucide-react'
import { fill, STORAGE_COPY } from '../../lib/storage/copy'
import { formatBytes, formatEta, formatRate } from '../../lib/storage/format'
import type { DestState, TransferState } from '../../lib/storage/pageState'

interface Props {
  dest: DestState
  transfer: TransferState
  canStart: boolean
  /** `sleeve-u<dev>-<src>` of the sleeve as CONFIG.TXT names it. */
  onPickDest: () => void
  onReconnectDest: () => void
  onKeep: (keep: boolean) => void
  onStart: () => void
  onCancel: () => void
}

export default function TransferPanel(props: Props) {
  const { dest, transfer, canStart } = props
  const c = STORAGE_COPY
  const running = transfer.running
  const run = transfer.run
  const summary = transfer.summary
  return (
    <section className="card storage-card" aria-label={c.transfer.progressLabel}>
      <div className="storage-actions">
        {dest.status === 'reconnect' ? (
          <button type="button" className="segment rig-action" disabled={running} onClick={props.onReconnectDest}>
            <RefreshCw size={13} aria-hidden />
            {c.destination.reconnect}
          </button>
        ) : (
          <button type="button" className="segment rig-action" disabled={running} onClick={props.onPickDest}>
            <FolderOpen size={13} aria-hidden />
            {c.destination.choose}
          </button>
        )}
        {dest.name && dest.status !== 'none' && (
          <span className="storage-drive-name">{fill(c.destination.current, { name: dest.name })}</span>
        )}
      </div>
      {dest.status === 'ready' && <p className="storage-help">{c.destination.hint}</p>}

      <label className="storage-check">
        <input
          type="checkbox"
          checked={transfer.keepCopies}
          disabled={running}
          onChange={(e) => props.onKeep(e.target.checked)}
        />
        {c.transfer.keepCopies}
      </label>

      <div className="storage-actions">
        {running ? (
          <button type="button" className="segment rig-action" disabled={transfer.cancelling} onClick={props.onCancel}>
            <X size={13} aria-hidden />
            {transfer.cancelling ? c.transfer.cancelling : c.transfer.cancel}
          </button>
        ) : (
          <button type="button" className="segment rig-action storage-primary" disabled={!canStart} onClick={props.onStart}>
            <Play size={13} aria-hidden />
            {c.transfer.start}
          </button>
        )}
        {running && (
          <span className="storage-warn" role="status">
            {c.transfer.doNotUnplug}
          </span>
        )}
      </div>

      {run && (running || summary) && (
        <div className="storage-progress" aria-live="polite">
          <progress className="storage-bar" max={run.bytesTotal || 1} value={run.bytesDone} aria-label={c.transfer.progressLabel} />
          {running && (
            <p className="storage-run">
              {fill(c.transfer.runLine, {
                done: formatBytes(run.bytesDone),
                total: formatBytes(run.bytesTotal),
                rate: formatRate(run.bytesPerS),
                eta: formatEta(run.etaMs),
              })}
            </p>
          )}
        </div>
      )}

      {summary && !running && (
        <p className="storage-summary" role="status">
          {fill(c.transfer.summary, {
            copied: summary.copied,
            already: summary.alreadyTransferred,
            failed: summary.failed,
            deleted: summary.deleted,
          })}
          {summary.notStarted > 0 && ` ${fill(c.transfer.notStarted, { n: summary.notStarted })}`}
        </p>
      )}
      {transfer.error && (
        <p className="rig-error notice" role="alert">
          {fill(c.transfer.stopped, { detail: transfer.error })}
        </p>
      )}

      <p className="storage-help">{c.transfer.speedNote}</p>
    </section>
  )
}
