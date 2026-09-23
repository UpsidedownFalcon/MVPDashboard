// The file-operations seam (PLAN_msd_management 4.1). Everything above this
// line talks to ByteSource / ByteSink / DirLike only, so the browser adapter
// (fsa.ts, File System Access API), the in-memory test double (memDir.ts)
// and a future native helper are interchangeable.
import { BLOCK_BYTES, FILE_HEADER_BYTES } from './binFormat'

export interface ByteSource {
  /** Size in bytes at open time. */
  readonly size: number
  /** Read `length` bytes at `offset`; may return fewer at end of file. */
  read(offset: number, length: number): Promise<Uint8Array>
}

export interface ByteSink {
  write(bytes: Uint8Array): Promise<void>
  /** Commit. Until it resolves the target may not exist (Chrome swaps a
   *  .crswap into place on close). */
  close(): Promise<void>
  /** Discard everything written so far. */
  abort(): Promise<void>
}

export type DirEntryKind = 'file' | 'dir'

export interface DirEntry {
  name: string
  kind: DirEntryKind
  /** Bytes for files, 0 for directories. */
  size: number
}

export interface DirLike {
  list(): Promise<DirEntry[]>
  open(name: string): Promise<ByteSource>
  create(name: string): Promise<ByteSink>
  remove(name: string): Promise<void>
  subdir(name: string, create: boolean): Promise<DirLike>
  exists(name: string): Promise<boolean>
}

/** Chunk sizes for a read budget of `chunkBytes`: the first chunk carries the
 *  512 B file header plus whole 4096 B blocks, every later chunk whole blocks
 *  only, so a block straddles a chunk boundary only when the file itself is
 *  not block-aligned. At least one block per chunk. */
export function chunkPlan(chunkBytes: number): { first: number; rest: number } {
  const blocks = Math.max(1, Math.floor((chunkBytes - FILE_HEADER_BYTES) / BLOCK_BYTES))
  return { first: FILE_HEADER_BYTES + blocks * BLOCK_BYTES, rest: blocks * BLOCK_BYTES }
}

/** Stream a source as slices sized by chunkPlan(). Each read is awaited
 *  before the next one is issued (backpressure lives in the consumer);
 *  `signal` is checked before every read. A zero-length read ends the
 *  stream early (the consumer compares bytes seen against the size). */
export async function* readChunks(
  src: ByteSource,
  chunkBytes: number,
  signal?: AbortSignal,
): AsyncGenerator<Uint8Array, void, undefined> {
  const plan = chunkPlan(chunkBytes)
  const size = src.size
  let offset = 0
  let want = plan.first
  while (offset < size) {
    signal?.throwIfAborted()
    const bytes = await src.read(offset, Math.min(want, size - offset))
    if (bytes.length === 0) return
    yield bytes
    offset += bytes.length
    want = plan.rest
  }
}

/** Concatenate byte arrays (used by small-file comparisons and tests). */
export function concatBytes(parts: Uint8Array[]): Uint8Array {
  let total = 0
  for (const p of parts) total += p.length
  const out = new Uint8Array(total)
  let pos = 0
  for (const p of parts) {
    out.set(p, pos)
    pos += p.length
  }
  return out
}

/** Byte-for-byte equality. */
export function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

/** The DOMException or Error name, '' for anything else. The transfer engine
 *  classifies failures by it (NotAllowedError, NotFoundError, ...), so there
 *  is exactly one copy of this mapping. */
export function errorName(err: unknown): string {
  if (err && typeof err === 'object' && 'name' in err && typeof err.name === 'string') return err.name
  return ''
}

/** "Name: message" for detail fields and logs. */
export function errorDetail(err: unknown): string {
  if (err instanceof Error) return err.message ? `${err.name}: ${err.message}` : err.name
  return String(err)
}
