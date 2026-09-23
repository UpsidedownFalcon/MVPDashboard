// CRC32, IEEE 802.3 reflected form (polynomial 0xEDB88320): the checksum that
// zlib.crc32() and the sleeve firmware's crc32_ieee() compute. The
// LOG_NNNN.BIN file header (bytes 0..507) and every 4096 B block carry one.
// The init / update / final trio lets callers hash a file while streaming it;
// crc32Of() is the one-shot form.

const CRC32_POLY = 0xedb88320
const CRC32_INIT = 0xffffffff

const TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ CRC32_POLY : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

/** Fresh running state (the pre-inverted register). */
export function crc32Init(): number {
  return CRC32_INIT
}

/** Feed bytes into a running state; returns the new state (unsigned). */
export function crc32Update(state: number, bytes: Uint8Array): number {
  let c = state
  for (let i = 0; i < bytes.length; i++) c = TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return c >>> 0
}

/** Close a running state: the CRC32 as an unsigned 32-bit number. */
export function crc32Final(state: number): number {
  return (state ^ CRC32_INIT) >>> 0
}

/** One-shot CRC32 of a byte array. */
export function crc32Of(bytes: Uint8Array): number {
  return crc32Final(crc32Update(crc32Init(), bytes))
}
