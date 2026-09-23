// Names on the HIPPOSDATA card. LOG_NNNN.BIN / LOG_NNNN.TXT are the only
// files the transfer ever lists, copies or deletes; CONFIG.TXT cannot match
// LOG_NAME_RE by construction (logNames.test.ts pins that).

export const LOG_NAME_RE = /^LOG_(\d{4})\.(BIN|TXT)$/i

export const CONFIG_FILE_NAME = 'CONFIG.TXT'

/** Chrome's createWritable() writes `<name>.crswap` beside the target. */
const CRSWAP_SUFFIX = '.crswap'
const WINDOWS_SYSTEM_ENTRIES = ['System Volume Information', '$RECYCLE.BIN']

export type LogKind = 'BIN' | 'TXT'

export interface LogName {
  session: number
  kind: LogKind
}

export function parseLogName(name: string): LogName | null {
  const m = LOG_NAME_RE.exec(name)
  if (!m) return null
  return { session: Number(m[1]), kind: m[2].toUpperCase() as LogKind }
}

/** Entries the listing hides: Chrome swap files, Windows system folders and
 *  dotfiles (macOS ._* resource forks, .Spotlight-V100, .Trashes ...). */
export function isIgnoredEntry(name: string): boolean {
  if (name.startsWith('.')) return true
  const lower = name.toLowerCase()
  if (lower.endsWith(CRSWAP_SUFFIX)) return true
  return WINDOWS_SYSTEM_ENTRIES.some((s) => s.toLowerCase() === lower)
}

/** The card's config file as actually named (FAT is case-insensitive;
 *  Chrome reports the stored spelling), or null when absent. */
export function findConfigEntry(names: string[]): string | null {
  const wanted = CONFIG_FILE_NAME.toLowerCase()
  for (const name of names) if (name.toLowerCase() === wanted) return name
  return null
}

export interface LogEntry {
  name: string
  size: number
}

export interface Session {
  session: number
  bin: LogEntry | null
  txt: LogEntry | null
}

/** Group LOG files by NNNN, ascending. Non-log names are skipped. A TXT
 *  without its BIN (or the reverse) still forms a session. */
export function groupSessions(entries: LogEntry[]): Session[] {
  const bySession = new Map<number, Session>()
  for (const entry of entries) {
    const parsed = parseLogName(entry.name)
    if (!parsed) continue
    let s = bySession.get(parsed.session)
    if (!s) {
      s = { session: parsed.session, bin: null, txt: null }
      bySession.set(parsed.session, s)
    }
    if (parsed.kind === 'BIN') s.bin = entry
    else s.txt = entry
  }
  return [...bySession.values()].sort((a, b) => a.session - b.session)
}

/** `dupName('LOG_0010.BIN', 2)` -> `LOG_0010-2.BIN`: the local name used
 *  when a different file of the same name already sits in the destination. */
export function dupName(name: string, n: number): string {
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return `${name}-${n}`
  return `${name.slice(0, dot)}-${n}${name.slice(dot)}`
}
