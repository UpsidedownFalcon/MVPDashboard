// Decision O: raw logs in the destination that lack one of their three
// outputs (transferred before change-set 2, or whose conversion failed or
// was interrupted before a reload). Found by a scan of
// `<dest>/sleeve-<unit>/raw/LOG_*.BIN` when the destination is chosen or
// reconnected; the page's "Convert missing" button queues them. Pure over
// DirLike so memDir.ts drives the tests. Read-only: nothing here writes.
import type { DirEntry, DirLike } from '../io'
import { UNIT_ID_RE } from '../configSchema'
import { FOLDER_PREFIX } from '../pageState'
import { RAW_SUBDIR } from '../transfer'
import { outputNames, stemOf, type OutputNames } from './types'

/** A raw copy as the transfer names it: LOG_0010.BIN, or LOG_0010-2.BIN for
 *  a same-name file that differed (logNames.dupName). Case-insensitive like
 *  LOG_NAME_RE: the local name keeps the card's own spelling. */
export const RAW_BIN_RE = /^LOG_\d{4}(-\d+)?\.BIN$/i

export type OutputKind = keyof OutputNames

const OUTPUT_KINDS: readonly OutputKind[] = ['csv', 'meta', 'summary']

export interface PendingRaw {
  /** sleeve-u<dev>-<src>, the folder under the destination. */
  folder: string
  /** Name of the raw file under `<folder>/raw/`. */
  localName: string
  stem: string
  size: number
  /** The `u<dev>-<src>` the folder name spells (the folder filter guarantees
   *  a match, so this is never empty). */
  unitId: string
  /** Which of the three outputs the folder lacks; never empty. */
  missing: OutputKind[]
}

/** `sleeve-u7-1` -> `u7-1`; null for any other folder name. */
export function unitIdOfFolder(folder: string): string | null {
  if (!folder.startsWith(FOLDER_PREFIX)) return null
  const rest = folder.slice(FOLDER_PREFIX.length)
  return UNIT_ID_RE.test(rest) ? rest : null
}

/** Outputs of `stem` that the folder listing does not hold as a NON-EMPTY
 *  file, in csv, meta, summary order. An empty file is the placeholder a
 *  failed or cancelled conversion can leave (the browser creates the file
 *  before the swap-file write starts), so it counts as missing. */
export function missingOutputs(entries: readonly DirEntry[], stem: string): OutputKind[] {
  const names = outputNames(stem)
  const sizes = new Map<string, number>()
  for (const e of entries) if (e.kind === 'file') sizes.set(e.name, e.size)
  return OUTPUT_KINDS.filter((kind) => !((sizes.get(names[kind]) ?? 0) > 0))
}

/** All three outputs of `stem` are there and not empty (decision V). */
export function outputsPresent(entries: readonly DirEntry[], stem: string): boolean {
  return missingOutputs(entries, stem).length === 0
}

/** Raw BINs under one sleeve folder that lack an output. Throws on a folder
 *  that cannot be read; the caller decides what a single bad folder costs. */
async function pendingInFolder(dest: DirLike, folder: string, unitId: string): Promise<PendingRaw[]> {
  const folderDir = await dest.subdir(folder, false)
  if (!(await folderDir.exists(RAW_SUBDIR))) return []
  const raw = await folderDir.subdir(RAW_SUBDIR, false)
  const beside = await folderDir.list()
  const out: PendingRaw[] = []
  for (const entry of await raw.list()) {
    if (entry.kind !== 'file' || !RAW_BIN_RE.test(entry.name)) continue
    const stem = stemOf(entry.name)
    const missing = missingOutputs(beside, stem)
    if (missing.length === 0) continue
    out.push({ folder, localName: entry.name, stem, size: entry.size, unitId, missing })
  }
  return out
}

function byFolderThenName(a: PendingRaw, b: PendingRaw): number {
  if (a.folder !== b.folder) return a.folder < b.folder ? -1 : 1
  return a.localName < b.localName ? -1 : a.localName > b.localName ? 1 : 0
}

/** Every raw BIN under `<dest>/sleeve-<unit>/raw/` that lacks at least one
 *  of its outputs, sorted by folder then name. A folder that cannot be read
 *  is skipped, not fatal; a destination that cannot be listed throws. */
export async function listPending(dest: DirLike): Promise<PendingRaw[]> {
  const out: PendingRaw[] = []
  for (const entry of await dest.list()) {
    if (entry.kind !== 'dir') continue
    const unitId = unitIdOfFolder(entry.name)
    if (unitId === null) continue
    try {
      out.push(...(await pendingInFolder(dest, entry.name, unitId)))
    } catch {
      // one unreadable sleeve folder must not hide the others
    }
  }
  return out.sort(byFolderThenName)
}
