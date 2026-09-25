// TIME_SYNC anchors, as bin2csv.py uses them: scan_syncs() collects
// (esp_us, unix_us) from every valid sync block and sorts the tuples;
// pick_sync(base) is the latest anchor whose esp_us <= the block's
// base_ts_us, else the FIRST one (extrapolating backwards). Values stay
// bigint (u64 on the card); the decoder guards the < 2^53 conversion.
import { SYNC_OFF_ESP_US, SYNC_OFF_UNIX_US, viewOf } from '../binFormat'
import type { SyncAnchor } from './types'

/** The anchor of a block already known to be a valid TIME_SYNC block. */
export function collectSync(block: Uint8Array): SyncAnchor {
  const v = viewOf(block)
  return { espUs: v.getBigUint64(SYNC_OFF_ESP_US, true), unixUs: v.getBigUint64(SYNC_OFF_UNIX_US, true) }
}

/** Python's tuple order: by espUs, then unixUs. */
export function compareSync(a: SyncAnchor, b: SyncAnchor): number {
  if (a.espUs !== b.espUs) return a.espUs < b.espUs ? -1 : 1
  if (a.unixUs !== b.unixUs) return a.unixUs < b.unixUs ? -1 : 1
  return 0
}

/** A sorted copy (Python `sorted(syncs)`). */
export function sortSyncs(anchors: readonly SyncAnchor[]): SyncAnchor[] {
  return [...anchors].sort(compareSync)
}

/** bin2csv.pick_sync over a SORTED list: the last anchor with
 *  espUs <= baseTsUs, else the first. Binary search, so a file with many
 *  sync blocks costs nothing per block. Throws on an empty list (the decoder
 *  leaves unix_us empty instead of calling this). */
export function pickSync(sorted: readonly SyncAnchor[], baseTsUs: bigint): SyncAnchor {
  if (sorted.length === 0) throw new RangeError('pickSync: no anchors')
  let lo = 0
  let hi = sorted.length // invariant: every index < lo has espUs <= base; every index >= hi has espUs > base
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (sorted[mid].espUs <= baseTsUs) lo = mid + 1
    else hi = mid
  }
  return lo === 0 ? sorted[0] : sorted[lo - 1]
}
