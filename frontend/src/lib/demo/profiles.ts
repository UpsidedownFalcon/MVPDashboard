// The five demo soldiers as DATA (STAGE4 R2, Appendix C). Names, envelopes,
// battery and the advice episodes are all here; changing a story is a table
// edit. Everything downstream (lib/demo/signal, live, api, insights) is a pure
// function of these profiles plus session-relative seconds.
//
// Time base: `s` = seconds since the synthetic session started. The session
// start is captured ONCE per page load as "2 h 05 min ago", so on every reload
// the picture relative to "now" is identical and a tab left open never jumps.


export const SESSION_AGE_S = 2 * 3600 + 5 * 60
export const SESSION_START_MS = Date.now() - SESSION_AGE_S * 1000

/** Hard-coded on purpose (user decision 2026-09-12): the shipped .env values.
 *  Real devices read these labels from the API; demo soldiers cannot. */
export const DEMO_WINDOWS = ['5m', '30m', '2h'] as const
export const DEMO_HORIZONS = ['10m', '30m', '1h'] as const
export const DEMO_MODEL_VERSION = 'trend-ols-1'

export type SeriesKey = 'm1' | 'm2' | 'm3' | 'm4' | 'm5' | 'c'
export const SERIES_KEYS: readonly SeriesKey[] = ['m1', 'm2', 'm3', 'm4', 'm5', 'c']

export interface Keyframe {
  s: number
  v: number
}

export interface Texture {
  /** +- amplitude of the slow wander added to the envelope */
  amp: number
  /** seconds per noise knot */
  period: number
}

export type DemoRuleId =
  | 'load_spike'
  | 'accumulated_load'
  | 'residual_load'
  | 'impact_deviation'
  | 'movement_quality'
  | 'composite_high'
  | 'rising_risk'

/** A window in session time during which a rule is evaluated every `every_s`
 *  seconds (the backend's INSIGHT_COOLDOWN_S). A row is emitted only when the
 *  rule's own precondition holds at that instant, so evidence and severity
 *  are always consistent with the numbers on the charts. */
export interface Episode {
  rule_id: DemoRuleId
  /** deviation rules: which primitive moved */
  metric?: 'm1' | 'm2' | 'm3' | 'm4' | 'm5'
  from_s: number
  /** null = still firing */
  to_s: number | null
  every_s?: number
}

export interface DemoProfile {
  id: string
  rank: number
  display_name: string
  seed: number
  qualityBase: number
  flags: string[]
  /** battery % at session start and its drain per hour */
  soc0: number
  socDrainPerHour: number
  envelopes: Record<SeriesKey, Keyframe[]>
  texture: Record<SeriesKey, Texture>
  episodes: Episode[]
}

const kf = (...pairs: [number, number][]): Keyframe[] => pairs.map(([s, v]) => ({ s, v }))

const CALM_TEXTURE: Record<SeriesKey, Texture> = {
  c: { amp: 1.5, period: 5 },
  m1: { amp: 4, period: 1.2 },
  m2: { amp: 4, period: 1.0 },
  m3: { amp: 0.3, period: 20 },
  m4: { amp: 1.5, period: 3 },
  m5: { amp: 1.2, period: 8 },
}

const HARD_TEXTURE: Record<SeriesKey, Texture> = {
  c: { amp: 2.5, period: 6 },
  m1: { amp: 5, period: 1.2 },
  m2: { amp: 5, period: 1.0 },
  m3: { amp: 0.4, period: 20 },
  m4: { amp: 2, period: 3 },
  m5: { amp: 1.5, period: 8 },
}

export const DEMO_PROFILES: readonly DemoProfile[] = [
  {
    // Climbing into high risk: accumulated load still rising, sustained high
    // composite, projections above the alert line. Three live cards.
    id: 'demo-1',
    rank: 1,
    display_name: 'SGT Alvarez',
    seed: 101,
    qualityBase: 0.95,
    flags: [],
    soc0: 75,
    socDrainPerHour: 6,
    envelopes: {
      c: kf([0, 25], [3600, 40], [6300, 70], [6900, 86], [7500, 89], [9000, 90]),
      m1: kf([0, 30], [3600, 45], [6300, 62], [7500, 68], [9000, 70]),
      m2: kf([0, 28], [3600, 42], [6300, 60], [7500, 66], [9000, 68]),
      // an early bump widens the 2 h spread, so the alert phase starts only
      // ~7 min ago rather than the whole last half hour
      m3: kf([0, 4], [2400, 18], [3900, 12], [5400, 14], [6300, 22], [6780, 32], [7080, 52], [7500, 82], [9000, 86]),
      m4: kf([0, 8], [6000, 14], [7500, 26], [9000, 30]),
      m5: kf([0, 4], [7500, 9], [9000, 10]),
    },
    texture: HARD_TEXTURE,
    episodes: [
      { rule_id: 'load_spike', from_s: 4200, to_s: 4800 },
      { rule_id: 'accumulated_load', metric: 'm3', from_s: 5400, to_s: null },
      { rule_id: 'rising_risk', from_s: 6300, to_s: null },
      { rule_id: 'composite_high', from_s: 6600, to_s: null },
      { rule_id: 'residual_load', from_s: 6900, to_s: null },
    ],
  },
  {
    // Steady moderate; two short impact steps (landing height) 20 min and
    // 3 min ago. Calibration carried over from a previous session.
    id: 'demo-2',
    rank: 2,
    display_name: 'CPL Nguyen',
    seed: 202,
    qualityBase: 0.96,
    flags: ['carried_over'],
    soc0: 93,
    socDrainPerHour: 5,
    envelopes: {
      c: kf([0, 18], [3000, 21], [6000, 19], [7500, 20], [9000, 20]),
      m1: kf(
        [0, 32], [6240, 33], [6300, 52], [6420, 52], [6480, 34],
        [7260, 34], [7320, 54], [7440, 54], [7500, 36], [9000, 35],
      ),
      m2: kf([0, 30], [9000, 31]),
      m3: kf([0, 3], [3600, 14], [7500, 26], [9000, 28]),
      m4: kf([0, 10], [9000, 12]),
      m5: kf([0, -3], [9000, -4]),
    },
    texture: CALM_TEXTURE,
    episodes: [
      { rule_id: 'impact_deviation', metric: 'm1', from_s: 6300, to_s: 6420 },
      { rule_id: 'impact_deviation', metric: 'm1', from_s: 7320, to_s: 7440 },
    ],
  },
  {
    // Low and calm all session. One old movement-control blip in the 2h
    // bucket; the live bucket is deliberately empty ("Nothing to flag").
    id: 'demo-3',
    rank: 3,
    display_name: 'PFC Okafor',
    seed: 303,
    qualityBase: 0.97,
    flags: [],
    soc0: 98,
    socDrainPerHour: 4,
    envelopes: {
      c: kf([0, 5], [3600, 7], [7500, 6], [9000, 6]),
      m1: kf([0, 18], [9000, 19]),
      m2: kf([0, 16], [9000, 17]),
      m3: kf([0, 2], [7500, 9], [9000, 10]),
      m4: kf([0, 6], [2340, 6], [2400, 20], [2520, 20], [2580, 7], [9000, 7]),
      m5: kf([0, 2], [9000, 2]),
    },
    texture: CALM_TEXTURE,
    episodes: [{ rule_id: 'movement_quality', metric: 'm4', from_s: 2400, to_s: 2520 }],
  },
  {
    // Elevated after a hard block 25 min ago, now easing (5 m trend down);
    // carrying more load on the right.
    id: 'demo-4',
    rank: 4,
    display_name: 'SSG Brooks',
    seed: 404,
    qualityBase: 0.94,
    flags: [],
    soc0: 60,
    socDrainPerHour: 7,
    envelopes: {
      c: kf([0, 20], [4200, 28], [6000, 62], [6900, 48], [7500, 34], [9000, 30]),
      m1: kf([0, 35], [6000, 60], [7500, 42], [9000, 40]),
      m2: kf([0, 33], [6000, 58], [7500, 40], [9000, 38]),
      m3: kf([0, 4], [4200, 20], [6000, 48], [7500, 44], [9000, 40]),
      m4: kf([0, 12], [6000, 22], [7500, 16], [9000, 15]),
      // a two-minute right-side excursion 3-5 min ago fires movement_quality
      m5: kf([0, -6], [6000, -20], [7140, -18], [7200, -34], [7320, -34], [7380, -19], [7500, -18], [9000, -17]),
    },
    texture: HARD_TEXTURE,
    episodes: [
      { rule_id: 'load_spike', from_s: 5400, to_s: 6000 },
      { rule_id: 'movement_quality', metric: 'm5', from_s: 7200, to_s: 7320 },
    ],
  },
  {
    // Just stepped up a band in the last five minutes: a fresh "ease off"
    // card in the live bucket. m4/m5 still learning their baseline.
    id: 'demo-5',
    rank: 5,
    display_name: 'SPC Ramirez',
    seed: 505,
    qualityBase: 0.93,
    flags: ['warming_up'],
    soc0: 58,
    socDrainPerHour: 6,
    envelopes: {
      c: kf([0, 12], [7200, 12], [7500, 22], [9000, 26]),
      m1: kf([0, 26], [7200, 26], [7500, 40], [9000, 42]),
      m2: kf([0, 24], [7200, 24], [7500, 38], [9000, 40]),
      m3: kf([0, 2], [7200, 12], [7500, 16], [9000, 20]),
      m4: kf([0, 9], [9000, 10]),
      m5: kf([0, 3], [9000, 3]),
    },
    texture: CALM_TEXTURE,
    episodes: [{ rule_id: 'load_spike', from_s: 7440, to_s: null }],
  },
]

const BY_ID = new Map(DEMO_PROFILES.map((p) => [p.id, p]))

export function profileFor(id: string): DemoProfile | undefined {
  return BY_ID.get(id)
}

/** Session-relative seconds for a wall-clock instant. */
export function sessionSeconds(nowMs: number): number {
  return (nowMs - SESSION_START_MS) / 1000
}
