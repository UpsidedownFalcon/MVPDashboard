// Streaming port of sensor_stats.py's corrupt-timestamp filter
// (build_signals, lines 384-387):
//   rolling_med = pd.Series(ts).rolling(11, center=True, min_periods=1).median()
//   good = np.abs(ts - rolling_med) <= ts_outlier_us
// For index i the window is ts[max(0, i-5) .. min(N-1, i+5)]; its median is
// the middle value, or the mean of the two middle values when the window
// holds an even count (only near the two ends of the series). The decision
// for i exists once i+5 has been pushed, or at flush(), so decisions trail
// pushes by five samples: the caller drains them (available / shift) after
// every push and after flush. Per sample: a copy of at most 11 numbers and
// an insertion sort of it.

/** pandas rolling(11, center=True): five neighbours each side. */
export const TS_FILTER_WINDOW = 11
const HALF = (TS_FILTER_WINDOW - 1) / 2
/** Decisions not yet taken; at most HALF + 1 pile up between drains. */
const QUEUE_CAPACITY = 16

export class TsFilter {
  private readonly ring = new Float64Array(TS_FILTER_WINDOW)
  private readonly scratch = new Float64Array(TS_FILTER_WINDOW)
  private pushed = 0
  private decided = 0
  private readonly queue = new Uint8Array(QUEUE_CAPACITY)
  private qHead = 0
  private qLen = 0

  constructor(private readonly tsOutlierUs: number) {}

  /** Number of decisions ready to shift(). */
  get available(): number {
    return this.qLen
  }

  push(tUs: number): void {
    this.ring[this.pushed % TS_FILTER_WINDOW] = tUs
    this.pushed++
    while (this.decided + HALF < this.pushed) this.decide()
  }

  /** End of the series: decide the last five with their shorter windows. */
  flush(): void {
    while (this.decided < this.pushed) this.decide()
  }

  /** Oldest undelivered decision, in push order: true = kept, false = a
   *  corrupt timestamp. */
  shift(): boolean {
    if (this.qLen === 0) throw new Error('TsFilter: no decision available')
    const v = this.queue[this.qHead]
    this.qHead = (this.qHead + 1) % QUEUE_CAPACITY
    this.qLen--
    return v === 1
  }

  private decide(): void {
    const i = this.decided
    const lo = Math.max(0, i - HALF)
    const hi = Math.min(this.pushed - 1, i + HALF)
    const n = hi - lo + 1
    const s = this.scratch
    for (let k = 0; k < n; k++) s[k] = this.ring[(lo + k) % TS_FILTER_WINDOW]
    insertionSort(s, n)
    const med = n % 2 === 1 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
    const good = Math.abs(this.ring[i % TS_FILTER_WINDOW] - med) <= this.tsOutlierUs
    if (this.qLen === QUEUE_CAPACITY) throw new Error('TsFilter: decisions were not drained')
    this.queue[(this.qHead + this.qLen) % QUEUE_CAPACITY] = good ? 1 : 0
    this.qLen++
    this.decided++
  }
}

function insertionSort(a: Float64Array, n: number): void {
  for (let i = 1; i < n; i++) {
    const v = a[i]
    let j = i - 1
    while (j >= 0 && a[j] > v) {
      a[j + 1] = a[j]
      j--
    }
    a[j + 1] = v
  }
}

/** Plain reference of the same filter over a whole series (tests). */
export function tsFilterReference(ts: ArrayLike<number>, tsOutlierUs: number): boolean[] {
  const out: boolean[] = []
  for (let i = 0; i < ts.length; i++) {
    const lo = Math.max(0, i - HALF)
    const hi = Math.min(ts.length - 1, i + HALF)
    const w: number[] = []
    for (let k = lo; k <= hi; k++) w.push(ts[k])
    w.sort((a, b) => a - b)
    const n = w.length
    const med = n % 2 === 1 ? w[(n - 1) / 2] : (w[n / 2 - 1] + w[n / 2]) / 2
    out.push(Math.abs(ts[i] - med) <= tsOutlierUs)
  }
  return out
}
