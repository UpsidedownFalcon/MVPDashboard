// Framing shared by both decoder passes (agent-docs/03_PLAN_csv_summary 4.3):
// the 512 B file header, then every WHOLE 4096 B block of a LOG_NNNN.BIN as a
// zero-copy view with its index, streamed over the same readChunks() budget
// the transfer engine uses. A block that straddles two chunks is completed in
// a reused carry buffer (the pattern of scanner.ts BlockScanner.push); a
// trailing partial block is never yielded, only counted, which is exactly
// where bin2csv.py's block loop stops. A chunk frame after every read gives
// the consumer its progress cadence without knowing the chunk plan.
import { BLOCK_BYTES, FILE_HEADER_BYTES } from '../binFormat'
import { readChunks, type ByteSource } from '../io'

export interface HeaderFrame {
  kind: 'header'
  /** Exactly FILE_HEADER_BYTES; its own buffer, valid for the whole run. */
  bytes: Uint8Array
  /** File offset just past the header. */
  bytesDone: number
}

export interface BlockFrame {
  kind: 'block'
  /** 0 = the first block after the header (bin2csv.py's `idx`). */
  index: number
  /** Exactly BLOCK_BYTES: a view into the chunk, or the carry buffer, valid
   *  only until the generator is resumed. Copy it to keep it. */
  bytes: Uint8Array
  /** File offset just past this block. */
  bytesDone: number
}

export interface ChunkFrame {
  kind: 'chunk'
  /** Bytes of the source consumed so far; a progress point. */
  bytesDone: number
}

export type LogFrame = HeaderFrame | BlockFrame | ChunkFrame

export interface FramingEnd {
  /** Whole blocks yielded. */
  blocks: number
  /** Bytes after the last whole block, or every byte of a file shorter than
   *  the header (no header frame is yielded then). */
  trailingBytes: number
  bytesDone: number
}

/** Header, blocks and chunk ends of `src` in file order. Errors thrown by
 *  the source (and the abort of `signal`, checked before every read) come
 *  out of the iteration unchanged. */
export async function* logFrames(
  src: ByteSource,
  readChunkBytes: number,
  signal?: AbortSignal,
): AsyncGenerator<LogFrame, FramingEnd, undefined> {
  const header = new Uint8Array(FILE_HEADER_BYTES)
  let headerLen = 0
  const carry = new Uint8Array(BLOCK_BYTES)
  let carryLen = 0
  let blocks = 0
  let bytesDone = 0
  for await (const chunk of readChunks(src, readChunkBytes, signal)) {
    let off = 0
    if (headerLen < FILE_HEADER_BYTES) {
      const n = Math.min(FILE_HEADER_BYTES - headerLen, chunk.length)
      header.set(chunk.subarray(0, n), headerLen)
      headerLen += n
      off = n
      if (headerLen === FILE_HEADER_BYTES) yield { kind: 'header', bytes: header, bytesDone: bytesDone + n }
    }
    if (headerLen === FILE_HEADER_BYTES) {
      if (carryLen > 0) {
        const n = Math.min(BLOCK_BYTES - carryLen, chunk.length - off)
        carry.set(chunk.subarray(off, off + n), carryLen)
        carryLen += n
        off += n
        if (carryLen === BLOCK_BYTES) {
          yield { kind: 'block', index: blocks++, bytes: carry, bytesDone: bytesDone + off }
          carryLen = 0
        }
      }
      while (chunk.length - off >= BLOCK_BYTES) {
        yield { kind: 'block', index: blocks++, bytes: chunk.subarray(off, off + BLOCK_BYTES), bytesDone: bytesDone + off + BLOCK_BYTES }
        off += BLOCK_BYTES
      }
      if (off < chunk.length) {
        // carryLen is 0 here: a partially filled carry consumed the chunk above.
        carry.set(chunk.subarray(off), 0)
        carryLen = chunk.length - off
      }
    }
    bytesDone += chunk.length
    yield { kind: 'chunk', bytesDone }
  }
  return { blocks, trailingBytes: headerLen < FILE_HEADER_BYTES ? headerLen : carryLen, bytesDone }
}
