// In-memory DirLike for tests (PLAN_msd_management 4.1) with fault injection
// that mimics what Chrome's File System Access API throws: DOMExceptions by
// name (NotReadableError on a read after unplug, QuotaExceededError on a
// write, AbortError on close, NoModificationAllowedError on removeEntry).
// Faults registered on a directory apply to that name in every descendant,
// so a test can arm `dest.failWrite('LOG_0001.BIN')` before the engine has
// created `<folder>/raw/`. Sinks commit on close() only, like a .crswap.
import { concatBytes, type ByteSink, type ByteSource, type DirEntry, type DirLike } from './io'

type FaultKind = 'open' | 'read' | 'write' | 'close' | 'remove' | 'corrupt'

interface Fault {
  error: string
  /** read faults: reads up to this many bytes still succeed. */
  afterBytes: number
}

const encoder = new TextEncoder()
const decoder = new TextDecoder()

function domError(name: string, message: string): DOMException {
  return new DOMException(message, name)
}

class MemSource implements ByteSource {
  constructor(
    private readonly dir: MemDir,
    private readonly name: string,
    private readonly bytes: Uint8Array,
  ) {}

  get size(): number {
    return this.bytes.length
  }

  async read(offset: number, length: number): Promise<Uint8Array> {
    this.dir.checkUnplugged('read')
    const fault = this.dir.fault('read', this.name)
    if (fault && offset + length > fault.afterBytes) {
      throw domError(fault.error, `injected read failure on ${this.name}`)
    }
    return this.bytes.slice(offset, offset + length)
  }
}

class MemSink implements ByteSink {
  private readonly parts: Uint8Array[] = []
  private done = false

  constructor(
    private readonly dir: MemDir,
    private readonly name: string,
  ) {
    dir.openSinks++
  }

  async write(bytes: Uint8Array): Promise<void> {
    if (this.done) throw domError('InvalidStateError', 'sink is closed')
    this.dir.checkUnplugged('write')
    const fault = this.dir.fault('write', this.name)
    if (fault) throw domError(fault.error, `injected write failure on ${this.name}`)
    this.parts.push(bytes.slice())
  }

  async close(): Promise<void> {
    if (this.done) throw domError('InvalidStateError', 'sink is closed')
    this.done = true
    this.dir.openSinks--
    this.dir.checkUnplugged('close')
    const fault = this.dir.fault('close', this.name)
    if (fault) throw domError(fault.error, `injected close failure on ${this.name}`)
    const bytes = concatBytes(this.parts)
    if (this.dir.fault('corrupt', this.name) && bytes.length > 0) bytes[bytes.length - 1] ^= 0xff
    this.dir.files.set(this.name, bytes)
  }

  async abort(): Promise<void> {
    if (!this.done) this.dir.openSinks--
    this.done = true
    this.parts.length = 0
  }
}

export class MemDir implements DirLike {
  /** Writables created here and neither closed nor aborted yet (tests assert
   *  the engine never leaves one dangling). */
  openSinks = 0
  readonly files = new Map<string, Uint8Array>()
  readonly subdirs = new Map<string, MemDir>()
  private readonly faults = new Map<string, Fault>()
  private unplugged = false

  constructor(
    files: Record<string, Uint8Array | string> = {},
    private readonly parent: MemDir | null = null,
  ) {
    for (const [name, content] of Object.entries(files)) this.put(name, content)
  }

  // ----- test helpers ---------------------------------------------------

  put(name: string, content: Uint8Array | string): this {
    this.files.set(name, typeof content === 'string' ? encoder.encode(content) : content.slice())
    return this
  }

  bytes(name: string): Uint8Array | undefined {
    return this.files.get(name)
  }

  text(name: string): string | undefined {
    const b = this.files.get(name)
    return b === undefined ? undefined : decoder.decode(b)
  }

  /** Walk into nested subdirs (undefined when any is missing). */
  at(...names: string[]): MemDir | undefined {
    let dir: MemDir | undefined = this
    for (const n of names) dir = dir?.subdirs.get(n)
    return dir
  }

  /** open(name) throws `error` (default NotFoundError). */
  failOpen(name: string, error = 'NotFoundError'): this {
    return this.arm('open', name, error)
  }

  /** Reads of `name` beyond `afterBytes` throw `error` (default
   *  NotReadableError, what Chrome raises once the drive is gone). */
  failRead(name: string, afterBytes = 0, error = 'NotReadableError'): this {
    return this.arm('read', name, error, afterBytes)
  }

  failWrite(name: string, error = 'QuotaExceededError'): this {
    return this.arm('write', name, error)
  }

  /** close() throws `error` (default AbortError: Safe Browsing refused). */
  failClose(name: string, error = 'AbortError'): this {
    return this.arm('close', name, error)
  }

  failRemove(name: string, error = 'NoModificationAllowedError'): this {
    return this.arm('remove', name, error)
  }

  /** The committed file differs from what was written (last byte flipped). */
  corruptOnClose(name: string): this {
    return this.arm('corrupt', name, '')
  }

  /** The drive is gone: every operation on this tree throws. */
  unplug(): this {
    this.unplugged = true
    return this
  }

  replug(): this {
    this.unplugged = false
    return this
  }

  // ----- internals used by sources / sinks ------------------------------

  fault(kind: FaultKind, name: string): Fault | undefined {
    return this.faults.get(`${kind}:${name}`) ?? this.parent?.fault(kind, name)
  }

  checkUnplugged(op: string): void {
    if (this.isUnplugged()) throw domError(op === 'read' ? 'NotReadableError' : 'NotFoundError', 'drive unplugged')
  }

  private isUnplugged(): boolean {
    return this.unplugged || (this.parent?.isUnplugged() ?? false)
  }

  private arm(kind: FaultKind, name: string, error: string, afterBytes = 0): this {
    this.faults.set(`${kind}:${name}`, { error, afterBytes })
    return this
  }

  // ----- DirLike ----------------------------------------------------------

  async list(): Promise<DirEntry[]> {
    this.checkUnplugged('list')
    const entries: DirEntry[] = []
    for (const [name, bytes] of this.files) entries.push({ name, kind: 'file', size: bytes.length })
    for (const name of this.subdirs.keys()) entries.push({ name, kind: 'dir', size: 0 })
    return entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
  }

  async open(name: string): Promise<ByteSource> {
    this.checkUnplugged('open')
    const fault = this.fault('open', name)
    if (fault) throw domError(fault.error, `injected open failure on ${name}`)
    const bytes = this.files.get(name)
    if (bytes === undefined) throw domError('NotFoundError', `${name} not found`)
    return new MemSource(this, name, bytes)
  }

  async create(name: string): Promise<ByteSink> {
    this.checkUnplugged('create')
    return new MemSink(this, name)
  }

  async remove(name: string): Promise<void> {
    this.checkUnplugged('remove')
    const fault = this.fault('remove', name)
    if (fault) throw domError(fault.error, `injected remove failure on ${name}`)
    if (!this.files.delete(name) && !this.subdirs.delete(name)) {
      throw domError('NotFoundError', `${name} not found`)
    }
  }

  async subdir(name: string, create: boolean): Promise<DirLike> {
    this.checkUnplugged('subdir')
    let dir = this.subdirs.get(name)
    if (!dir) {
      if (!create) throw domError('NotFoundError', `${name} not found`)
      dir = new MemDir({}, this)
      this.subdirs.set(name, dir)
    }
    return dir
  }

  async exists(name: string): Promise<boolean> {
    this.checkUnplugged('exists')
    return this.files.has(name) || this.subdirs.has(name)
  }
}
