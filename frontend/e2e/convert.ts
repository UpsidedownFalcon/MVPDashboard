// Conversion dry run without a sleeve or a picker (agent-docs/03 section 6):
// the chosen BIN is copied into the origin-private file system, whose handles
// are real FileSystemFileHandle / FileSystemDirectoryHandle objects, so the
// page exercises the real ConversionQueue, worker, createWritable() path and
// output layout. The CSV is then hashed slice by slice for comparison with
// bin2csv.py. Results land on window.__e2e for scripts/e2e drivers.
import { STORAGE_COPY, fill } from '../src/lib/storage/copy'
import { outputNames, stemOf } from '../src/lib/storage/convert/types'
import { ConversionQueue } from '../src/lib/storage/convertQueue'
import { Sha256, SHA256_ABC } from './sha256'

interface E2EState {
  status: string
  log: string[]
  progress?: { phase: string; pct: number; rows: number }
  startedAt?: number
  finishedAt?: number
  mainHeapPeak: number
  result?: {
    ok: boolean
    conversionMs: number
    csvBytes: number
    csvSha256: string
    metaText: string
    summary: string
    outputs: string[]
    error?: string
  }
}

declare global {
  interface Window {
    __e2e: E2EState
  }
}

const state: E2EState = { status: 'idle', log: [], mainHeapPeak: 0 }
window.__e2e = state
const logEl = document.getElementById('log') as HTMLPreElement

function log(line: string): void {
  state.log.push(line)
  logEl.textContent = state.log.join('\n')
}

function sampleHeap(): void {
  const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
  if (mem && mem.usedJSHeapSize > state.mainHeapPeak) state.mainHeapPeak = mem.usedJSHeapSize
}

async function hashFile(handle: FileSystemFileHandle): Promise<{ sha256: string; bytes: number }> {
  const file = await handle.getFile()
  const sha = new Sha256()
  const step = 8 * 1024 * 1024
  for (let off = 0; off < file.size; off += step) {
    sha.update(new Uint8Array(await file.slice(off, off + step).arrayBuffer()))
  }
  return { sha256: sha.hex(), bytes: file.size }
}

async function run(file: File): Promise<void> {
  const check = new Sha256()
  check.update(new TextEncoder().encode('abc'))
  if (check.hex() !== SHA256_ABC) throw new Error('sha256 self-check failed')

  state.status = 'copying'
  const root = await navigator.storage.getDirectory()
  try {
    await root.removeEntry('e2e', { recursive: true })
  } catch {
    // first run
  }
  const folder = await root.getDirectoryHandle('e2e', { create: true })
  const raw = await folder.getDirectoryHandle('raw', { create: true })
  const rawName = file.name
  const t0 = performance.now()
  const rawHandle = await raw.getFileHandle(rawName, { create: true })
  const w = await rawHandle.createWritable()
  await w.write(file)
  await w.close()
  log(`copied ${rawName} (${file.size} B) into OPFS in ${Math.round(performance.now() - t0)} ms`)

  const stem = stemOf(rawName)
  const names = outputNames(stem)
  const c = STORAGE_COPY.conversion
  const placement = {
    labels: {
      1: fill(c.placement.sided, { side: c.sides.left, segment: c.placement.thigh }),
      2: fill(c.placement.sided, { side: c.sides.left, segment: c.placement.shin }),
    },
    fallback: c.placement.notSet,
  }

  state.status = 'converting'
  state.startedAt = Date.now()
  const started = performance.now()
  const heapTimer = window.setInterval(sampleHeap, 250)
  const done = new Promise<{ ok: boolean; summary?: string; error?: string }>((resolve) => {
    const queue = new ConversionQueue({
      createWorker: () => new Worker(new URL('../src/workers/convert.worker.ts', import.meta.url), { type: 'module' }),
      resolve: async () => ({ raw: rawHandle, outDir: folder, outputsPresent: false }),
      onEvent: (e) => {
        if (e.type === 'progress') {
          state.progress = { phase: e.phase, pct: Math.round((100 * e.bytesDone) / Math.max(1, e.bytesTotal)), rows: e.rows }
        } else if (e.type === 'done') {
          resolve({ ok: true, summary: e.summary })
        } else if (e.type === 'failed') {
          resolve({ ok: false, error: `${e.code}: ${e.detail ?? ''}` })
        } else if (e.type === 'cancelled') {
          resolve({ ok: false, error: 'cancelled' })
        } else if (e.type === 'already-converted') {
          resolve({ ok: false, error: 'already-converted (unexpected)' })
        }
      },
    })
    queue.enqueue({ id: `e2e/${rawName}`, folder: 'e2e', localName: rawName, stem, sourceFile: rawName, placement })
    queue.start()
  })
  const outcome = await done
  const conversionMs = Math.round(performance.now() - started)
  window.clearInterval(heapTimer)
  sampleHeap()
  state.finishedAt = Date.now()
  log(`conversion ${outcome.ok ? 'done' : 'FAILED'} in ${conversionMs} ms ${outcome.error ?? ''}`)

  state.status = 'hashing'
  let csvSha256 = ''
  let csvBytes = 0
  let metaText = ''
  const outputs: string[] = []
  for await (const [name] of folder.entries()) outputs.push(name)
  if (outcome.ok) {
    const csv = await hashFile(await folder.getFileHandle(names.csv))
    csvSha256 = csv.sha256
    csvBytes = csv.bytes
    metaText = await (await (await folder.getFileHandle(names.meta)).getFile()).text()
    log(`csv ${csvBytes} B sha256 ${csvSha256}`)
  }
  state.result = {
    ok: outcome.ok,
    conversionMs,
    csvBytes,
    csvSha256,
    metaText,
    summary: outcome.summary ?? '',
    outputs: outputs.sort(),
    error: outcome.error,
  }
  state.status = 'finished'
  log(outcome.summary ?? '')
}

const input = document.getElementById('bin') as HTMLInputElement
input.addEventListener('change', () => {
  const file = input.files?.[0]
  if (!file) return
  run(file).catch((err: unknown) => {
    state.status = 'finished'
    state.result = {
      ok: false,
      conversionMs: 0,
      csvBytes: 0,
      csvSha256: '',
      metaText: '',
      summary: '',
      outputs: [],
      error: err instanceof Error ? `${err.name}: ${err.message}` : String(err),
    }
    log(`error: ${state.result.error}`)
  })
})
log('ready: choose a LOG_NNNN.BIN')
