// The CSV + summary card (agent-docs/03_PLAN_csv_summary 4.6, decisions O,
// P, R, V): one row per raw BIN queued this session (by the transfer, by
// "Convert missing" or by a Retry), the summary of the selected row, and
// the button that queues raw files found without outputs. Display only: the
// page owns the queue and the worker. No download link (decision P): the
// summary is already on disk beside raw/ and the line under the heading
// says where.
import { FileText, RotateCcw } from 'lucide-react'
import { conversionFailureText, fill, STORAGE_COPY } from '../../lib/storage/copy'
import type { ConversionJob, SelectedSummary } from '../../lib/storage/pageState'

interface Props {
  rows: ConversionJob[]
  selected: string | null
  summary: SelectedSummary | null
  /** Decision O: raw files without outputs found in the destination. */
  pendingCount: number
  scanning: boolean
  /** The scan and the button only make sense with a destination to scan. */
  destReady: boolean
  onSelect: (id: string) => void
  onRetry: (id: string) => void
  onConvertMissing: () => void
}

export function conversionStatusText(job: ConversionJob): string {
  const s = STORAGE_COPY.conversion.status
  switch (job.status) {
    case 'queued':
      return s.queued
    case 'scanning':
      return fill(s.scanning, { pct: job.pct })
    case 'converting':
      return fill(s.converting, { pct: job.pct })
    case 'converted':
      return s.converted
    case 'already-converted':
      return s.alreadyConverted
    case 'failed':
      return job.error ? conversionFailureText(job.error.code) : s.failed
    case 'cancelled':
      return s.cancelled
  }
}

export default function ConversionPanel(props: Props) {
  const { rows, selected, summary, pendingCount, scanning, destReady } = props
  const c = STORAGE_COPY.conversion
  return (
    <section className="card storage-card" aria-label={c.section}>
      <h2 className="storage-h2">{c.section}</h2>
      <p className="storage-help">{c.intro}</p>

      {destReady && (
        <div className="storage-actions">
          <button
            type="button"
            className="segment rig-action"
            disabled={scanning || pendingCount === 0}
            onClick={props.onConvertMissing}
          >
            <FileText size={13} aria-hidden />
            {fill(c.convertMissing, { n: pendingCount })}
          </button>
          {scanning ? (
            <span className="storage-help" role="status">
              {c.scanning}
            </span>
          ) : (
            pendingCount === 0 && <span className="storage-help">{c.noneMissing}</span>
          )}
        </div>
      )}

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table storage-table">
            <thead>
              <tr>
                <th>{c.columns.select}</th>
                <th>{c.columns.file}</th>
                <th>{c.columns.sleeve}</th>
                <th>{c.columns.status}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((job) => {
                const on = selected === job.id
                const canRetry = job.status === 'failed' || job.status === 'cancelled'
                return (
                  <tr key={job.id} className={on ? 'is-selected' : ''}>
                    <td>
                      <input
                        type="radio"
                        name="storage-conversion-selected"
                        aria-label={job.id}
                        checked={on}
                        onChange={() => props.onSelect(job.id)}
                      />
                    </td>
                    <td className="storage-mono">{job.localName}</td>
                    <td className="storage-mono">{job.unitId ?? job.folder}</td>
                    <td className="storage-status" aria-live="polite">
                      {conversionStatusText(job)}
                      {canRetry && (
                        <button
                          type="button"
                          className="segment rig-action storage-inline-action"
                          onClick={() => props.onRetry(job.id)}
                        >
                          <RotateCcw size={13} aria-hidden />
                          {c.retry}
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {rows.length > 0 &&
        (summary ? (
          <div className="storage-summary-block">
            <h3 className="storage-h3">{c.summaryHeading}</h3>
            <p className="storage-help">{fill(c.summaryWhere, { folder: summary.folder, file: summary.file })}</p>
            <pre className="storage-summary-text">{summary.text}</pre>
          </div>
        ) : (
          <p className="storage-help">{c.summaryEmpty}</p>
        ))}
    </section>
  )
}
