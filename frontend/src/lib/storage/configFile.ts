// Byte-level model of CONFIG.TXT (PLAN_msd_management 4.1). Two views:
//
// * interpretAsFirmware() sees the file exactly as config_load() does: fgets
//   pieces of at most 159 bytes, C isspace trimming, whole-line `#`/`;`
//   comments, first-'=' split, last duplicate wins, unknown keys ignored, a
//   BOM glued to the first key, strlen() truncation at a NUL.
// * applyEdits() rewrites nothing but the value span of the LAST physical
//   line holding each edited key (from just after '=' to the end of the
//   trimmed value) and appends `key=value\r\n` for keys the file lacks, so
//   comments, unknown keys, line endings and even invalid UTF-8 elsewhere
//   survive byte-for-byte. It never writes a BOM and never adds comments.
//
// The two views agree for every line of at most 159 bytes. For an over-long
// line applyEdits() replaces the whole physical value (the sane repair),
// while the firmware would have split it; verifyReadback() is the safety
// net that catches any remaining disagreement after a write.
import { concatBytes } from './io'
import { CONFIG_KEY_NAMES, isConfigKey, isCSpace, MAX_LINE_BYTES } from './configSchema'

const LF = 0x0a
const CR = 0x0d
const NUL = 0x00
const HASH = 0x23
const SEMICOLON = 0x3b
const EQUALS = 0x3d
const UTF8_BOM = [0xef, 0xbb, 0xbf] as const

/** Lossy decoding for display: invalid sequences become U+FFFD; a leading
 *  BOM is KEPT (ignoreBOM) because the firmware sees it as key bytes. */
const decoder = new TextDecoder('utf-8', { ignoreBOM: true })
const encoder = new TextEncoder()

export interface Line {
  /** First byte of the line content. */
  start: number
  /** One past the last content byte (before the EOL bytes). */
  end: number
  /** 0 = no terminator (last line), 1 = LF, 2 = CRLF. */
  eolLen: 0 | 1 | 2
}

/** Physical lines, split at LF only (fgets() stops at '\n'; a bare CR is
 *  ordinary content to the firmware, which is what trim_() then strips at
 *  the ends). A file ending in LF has no extra empty line. */
export function splitLines(bytes: Uint8Array): Line[] {
  const lines: Line[] = []
  let start = 0
  for (let i = 0; i < bytes.length; i++) {
    if (bytes[i] !== LF) continue
    const crlf = i > start && bytes[i - 1] === CR
    lines.push({ start, end: crlf ? i - 1 : i, eolLen: crlf ? 2 : 1 })
    start = i + 1
  }
  if (start < bytes.length) lines.push({ start, end: bytes.length, eolLen: 0 })
  return lines
}

export function hasBom(bytes: Uint8Array): boolean {
  return bytes.length >= UTF8_BOM.length && UTF8_BOM.every((b, i) => bytes[i] === b)
}

/** The file without its UTF-8 BOM (a copy); unchanged input when none. */
export function stripBom(bytes: Uint8Array): Uint8Array {
  return hasBom(bytes) ? bytes.slice(UTF8_BOM.length) : bytes
}

type Segment =
  | { kind: 'blank' | 'comment' | 'malformed' }
  | { kind: 'entry'; key: string; value: string; valueStart: number; valueEnd: number }

/** One fgets() piece (or one physical line) as config_load() parses it. */
function parseSegment(bytes: Uint8Array, start: number, end: number): Segment {
  for (let i = start; i < end; i++) {
    if (bytes[i] === NUL) {
      end = i
      break
    }
  }
  let s = start
  let e = end
  while (s < e && isCSpace(bytes[s])) s++
  while (e > s && isCSpace(bytes[e - 1])) e--
  if (s === e) return { kind: 'blank' }
  if (bytes[s] === HASH || bytes[s] === SEMICOLON) return { kind: 'comment' }
  let eq = -1
  for (let i = s; i < e; i++) {
    if (bytes[i] === EQUALS) {
      eq = i
      break
    }
  }
  if (eq < 0) return { kind: 'malformed' }
  let keyEnd = eq
  while (keyEnd > s && isCSpace(bytes[keyEnd - 1])) keyEnd--
  let valueStart = eq + 1
  while (valueStart < e && isCSpace(bytes[valueStart])) valueStart++
  return {
    kind: 'entry',
    key: decoder.decode(bytes.subarray(s, keyEnd)),
    value: decoder.decode(bytes.subarray(valueStart, e)),
    valueStart: eq + 1,
    valueEnd: e,
  }
}

export interface FirmwareEntry {
  /** The value the firmware would use (last occurrence, trimmed). */
  value: string
  /** Physical line index of that occurrence. */
  lineIndex: number
  occurrences: number
}

export interface FirmwareView {
  bom: boolean
  /** Every key=value the parser accepts, known or not, keyed as the
   *  firmware sees it (a BOM'd first key is "\ufeffwifi_ssid"). */
  entries: Map<string, FirmwareEntry>
  unknownKeys: string[]
  /** Physical lines with content but no '=' (the firmware logs and skips). */
  malformedLines: number[]
  /** Physical lines longer than MAX_LINE_BYTES: fgets() splits them and each
   *  piece is parsed as a line of its own. */
  overlongLines: number[]
  /** Keys that occur more than once (the last one wins). */
  duplicates: string[]
}

export function interpretAsFirmware(bytes: Uint8Array): FirmwareView {
  const entries = new Map<string, FirmwareEntry>()
  const unknownKeys: string[] = []
  const malformedLines: number[] = []
  const overlongLines: number[] = []
  const duplicates: string[] = []
  splitLines(bytes).forEach((line, index) => {
    const length = line.end - line.start
    const pieces = length > MAX_LINE_BYTES ? Math.ceil(length / MAX_LINE_BYTES) : 1
    if (pieces > 1) overlongLines.push(index)
    for (let p = 0; p < pieces; p++) {
      const pieceStart = line.start + p * MAX_LINE_BYTES
      const pieceEnd = Math.min(line.end, pieceStart + MAX_LINE_BYTES)
      const seg = parseSegment(bytes, pieceStart, pieceEnd)
      if (seg.kind === 'malformed') {
        if (malformedLines[malformedLines.length - 1] !== index) malformedLines.push(index)
        continue
      }
      if (seg.kind !== 'entry') continue
      const prev = entries.get(seg.key)
      if (prev) {
        prev.value = seg.value
        prev.lineIndex = index
        prev.occurrences++
        if (!duplicates.includes(seg.key)) duplicates.push(seg.key)
      } else {
        entries.set(seg.key, { value: seg.value, lineIndex: index, occurrences: 1 })
        if (!isConfigKey(seg.key)) unknownKeys.push(seg.key)
      }
    }
  })
  return { bom: hasBom(bytes), entries, unknownKeys, malformedLines, overlongLines, duplicates }
}

interface Patch {
  start: number
  end: number
  text: string
}

function keyOrder(key: string): number {
  const i = (CONFIG_KEY_NAMES as readonly string[]).indexOf(key)
  return i < 0 ? CONFIG_KEY_NAMES.length : i
}

/** Return a new byte array with `edits` applied (see the module header).
 *  Values are written verbatim: callers pass encodeValue() output. */
export function applyEdits(bytes: Uint8Array, edits: Record<string, string>): Uint8Array {
  const spans = new Map<string, { start: number; end: number }>()
  for (const line of splitLines(bytes)) {
    const seg = parseSegment(bytes, line.start, line.end)
    if (seg.kind === 'entry') spans.set(seg.key, { start: seg.valueStart, end: seg.valueEnd })
  }
  const patches: Patch[] = []
  const missing: string[] = []
  for (const [key, value] of Object.entries(edits)) {
    const span = spans.get(key)
    if (span) patches.push({ start: span.start, end: span.end, text: value })
    else missing.push(key)
  }
  patches.sort((a, b) => a.start - b.start)
  missing.sort((a, b) => keyOrder(a) - keyOrder(b))

  const parts: Uint8Array[] = []
  let pos = 0
  for (const patch of patches) {
    parts.push(bytes.subarray(pos, patch.start))
    parts.push(encoder.encode(patch.text))
    pos = patch.end
  }
  parts.push(bytes.subarray(pos))
  if (missing.length > 0) {
    // Like append_missing_(): never glue onto a last line that lacks its EOL.
    let tail = bytes.length > 0 && bytes[bytes.length - 1] !== LF ? '\r\n' : ''
    for (const key of missing) tail += `${key}=${edits[key]}\r\n`
    parts.push(encoder.encode(tail))
  }
  return concatBytes(parts)
}

/** After a write and re-read: does the firmware's view of `bytes` carry
 *  every intended value? */
export function verifyReadback(
  bytes: Uint8Array,
  intended: Record<string, string>,
): { ok: boolean; mismatches: string[] } {
  const view = interpretAsFirmware(bytes)
  const mismatches: string[] = []
  for (const [key, value] of Object.entries(intended)) {
    if (view.entries.get(key)?.value !== value) mismatches.push(key)
  }
  return { ok: mismatches.length === 0, mismatches }
}
