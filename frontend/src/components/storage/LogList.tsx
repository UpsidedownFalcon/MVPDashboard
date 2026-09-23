// The card's LOG files (PLAN_msd_management 4.3): one row per file grouped
// by session, all selected by default, with the facts read from each BIN's
// 512 B header (firmware, sleeve identity, full scale). CONFIG.TXT never
// appears here: it cannot match the log name pattern by construction. During
// a run the last column carries each item's status line.
import { unitIdFrom } from '../../lib/storage/configSchema'
import { fill, STORAGE_COPY, transferFailureText } from '../../lib/storage/copy'
import { formatBytes, formatEta, formatRate, percent } from '../../lib/storage/format'
import type { ItemState, LogRow, RunProgress } from '../../lib/storage/pageState'
import { fullScaleText } from '../../lib/rig'

interface Props {
  rows: LogRow[]
  selected: string[]
  items: Record<string, ItemState>
  run: RunProgress | undefined
  /** Selection is frozen while a transfer runs. */
  disabled: boolean
  onSelect: (name: string, selected: boolean) => void
  onSelectAll: (selected: boolean) => void
}

/** NNNN as the file name spells it. */
const SESSION_DIGITS = 4

export function itemStatusText(item: ItemState | undefined, run: RunProgress | undefined): string {
  const s = STORAGE_COPY.transfer.status
  if (!item) return ''
  switch (item.status) {
    case 'queued':
      return s.queued
    case 'copying':
      return fill(s.copying, {
        pct: percent(item.bytesDone, item.bytesTotal),
        rate: formatRate(run?.bytesPerS ?? 0),
        eta: formatEta(run?.etaMs ?? 0),
      })
    case 'verifying':
      return s.verifying
    case 'deleting':
      return s.deleting
    case 'done':
      if (item.result === 'already-transferred') return s.alreadyTransferred
      return item.deleted ? s.doneRemoved : s.doneKept
    case 'failed':
      return item.code ? transferFailureText(item.code) : s.failed
    case 'cancelled':
      return s.cancelled
  }
}

export default function LogList({ rows, selected, items, run, disabled, onSelect, onSelectAll }: Props) {
  const c = STORAGE_COPY.logs
  const chosen = new Set(selected)
  const all = rows.length > 0 && rows.every((r) => chosen.has(r.name))
  return (
    <section className="card storage-card" aria-label={c.section}>
      <h2 className="storage-h2">{c.section}</h2>
      {rows.length === 0 ? (
        <p className="notice">{c.empty}</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table storage-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label={c.columns.selectAll}
                    checked={all}
                    disabled={disabled}
                    onChange={(e) => onSelectAll(e.target.checked)}
                  />
                </th>
                <th>{c.columns.file}</th>
                <th>{c.columns.size}</th>
                <th>{c.columns.session}</th>
                <th>{c.columns.firmware}</th>
                <th>{c.columns.sleeve}</th>
                <th>{c.columns.fullScale}</th>
                <th>{c.columns.status}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const on = chosen.has(row.name)
                const h = row.header
                return (
                  <tr key={row.name} className={on ? '' : 'is-unselected'}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={row.name}
                        checked={on}
                        disabled={disabled}
                        onChange={(e) => onSelect(row.name, e.target.checked)}
                      />
                    </td>
                    <td className="storage-mono">{row.name}</td>
                    <td>{formatBytes(row.size)}</td>
                    <td className="storage-mono">{String(row.session).padStart(SESSION_DIGITS, '0')}</td>
                    <td>{h ? h.fw : c.none}</td>
                    <td className="storage-mono">
                      {h ? (
                        unitIdFrom(h.deviceId, h.sourceId)
                      ) : row.headerError ? (
                        <span className="chip flag flag-warning">{c.headerError[row.headerError]}</span>
                      ) : (
                        c.none
                      )}
                    </td>
                    <td>{h ? fullScaleText(h.accelFsG, h.gyroFsDps) : c.none}</td>
                    <td className="storage-status" aria-live="polite">
                      {itemStatusText(items[row.name], run)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
