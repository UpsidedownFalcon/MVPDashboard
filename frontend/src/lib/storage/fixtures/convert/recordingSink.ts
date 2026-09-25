// Test-only SampleSink that records every call, so tests can assert the
// decoder hands the sink the same sample sequence in both passes and calls
// start / pass1Done exactly once, in order. Not a test itself.
import type { FileHeader } from '../../binFormat'
import type { SampleSink, ValueTables } from '../../convert/types'

/** Numbers per recorded sample: sensor id, t_us, six counts. */
export const RECORD_WIDTH = 8

export class RecordingSink implements SampleSink {
  header: FileHeader | null = null
  tables: ValueTables | null = null
  starts = 0
  pass1DoneCalls = 0
  readonly pass1Seq: number[] = []
  readonly pass2Seq: number[] = []

  start(header: FileHeader, tables: ValueTables): void {
    this.starts++
    this.header = header
    this.tables = tables
  }

  pass1(sid: number, tUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void {
    if (this.starts !== 1 || this.pass1DoneCalls !== 0) throw new Error('pass1 out of order')
    this.pass1Seq.push(sid, tUs, ax, ay, az, gx, gy, gz)
  }

  pass1Done(): void {
    if (this.starts !== 1) throw new Error('pass1Done before start')
    this.pass1DoneCalls++
  }

  pass2(sid: number, tUs: number, ax: number, ay: number, az: number, gx: number, gy: number, gz: number): void {
    if (this.pass1DoneCalls !== 1) throw new Error('pass2 before pass1Done')
    this.pass2Seq.push(sid, tUs, ax, ay, az, gx, gy, gz)
  }

  /** Samples seen in pass 1. */
  get samples(): number {
    return this.pass1Seq.length / RECORD_WIDTH
  }

  /** True when pass 2 replayed pass 1 exactly. */
  samePasses(): boolean {
    if (this.pass1Seq.length !== this.pass2Seq.length) return false
    for (let i = 0; i < this.pass1Seq.length; i++) if (this.pass1Seq[i] !== this.pass2Seq[i]) return false
    return true
  }

  /** The i-th sample of pass 1 as an object (tests read a few by hand). */
  sample(i: number): { sid: number; tUs: number; counts: number[] } {
    const at = i * RECORD_WIDTH
    return { sid: this.pass1Seq[at], tUs: this.pass1Seq[at + 1], counts: this.pass1Seq.slice(at + 2, at + RECORD_WIDTH) }
  }
}
