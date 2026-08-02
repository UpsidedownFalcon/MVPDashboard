// The brand's "live human telemetry" signature (UIUX §10). Rebuilt SVG — no
// SMIL: sensor pings are CSS keyframes, data particles ride CSS offset-path.
// The compact variant is data-driven: sensor dots take liveness colors and the
// whole figure only animates while the device streams. Decorative parts are
// aria-hidden; reduced-motion stops every loop (theme.css).

export type LimbState = 'good' | 'warning' | 'critical'

const LIMB_ANCHORS: Record<string, { x: number; y: number }> = {
  left_thigh: { x: 63, y: 178 },
  left_shin: { x: 56, y: 252 },
  right_thigh: { x: 97, y: 178 },
  right_shin: { x: 104, y: 252 },
}

const LEG_LEFT = 'M66 132 C 62 185, 58 235, 53 298'
const LEG_RIGHT = 'M94 132 C 98 185, 102 235, 107 298'

const STATE_VAR: Record<LimbState, string> = {
  good: 'var(--accent)',
  warning: 'var(--status-warning)',
  critical: 'var(--status-critical)',
}

interface Props {
  variant: 'hero' | 'compact'
  /** limb name -> liveness; omitted limbs render dim. Hero passes nothing and
   *  gets the all-accent showcase. */
  limbs?: Record<string, LimbState>
  /** animations run (device streaming / hero always). */
  active?: boolean
  /** ambient leg emphasis; never a directional claim (SPEC §5.5). */
  emphasis?: 'left' | 'right' | null
}

export default function HumanoidFigure({ variant, limbs, active = true, emphasis }: Props) {
  const hero = variant === 'hero'
  const limbState = (name: string): LimbState | null =>
    limbs ? (limbs[name] ?? null) : 'good'

  const legOpacity = (side: 'left' | 'right') => {
    if (!emphasis) return 0.55
    return emphasis === side ? 0.75 : 0.4
  }

  return (
    <div
      className={`figure figure-${variant} ${active ? 'is-active' : 'is-idle'}`}
      role="img"
      aria-label="Athlete wearing four leg sensors streaming live motion data"
    >
      {hero && <div className="figure-glow" aria-hidden />}
      {hero && <div className="figure-ring" aria-hidden />}
      <svg viewBox="0 0 160 320" aria-hidden>
        <defs>
          <linearGradient id="legGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#21F3FC" stopOpacity="0.9" />
            <stop offset="1" stopColor="#2BBECD" stopOpacity="0.25" />
          </linearGradient>
        </defs>

        {/* body */}
        <circle cx="80" cy="24" r="14" fill="var(--surface-2)" stroke="var(--border-hover)" />
        <rect x="73" y="40" width="14" height="12" rx="5" fill="var(--surface-2)" />
        <path
          d="M56 52 L104 52 L100 132 L60 132 Z"
          fill="var(--surface-2)"
          stroke="var(--border-hover)"
          strokeWidth="0.6"
        />
        <path d="M56 54 L34 96 L30 130" stroke="var(--surface-3)" strokeWidth="5" strokeLinecap="round" fill="none" />
        <path d="M104 54 L126 96 L130 130" stroke="var(--surface-3)" strokeWidth="5" strokeLinecap="round" fill="none" />

        {/* legs: wide ambient glow under a gradient core */}
        {[
          { d: LEG_LEFT, side: 'left' as const },
          { d: LEG_RIGHT, side: 'right' as const },
        ].map(({ d, side }) => (
          <g key={side}>
            <path d={d} stroke="var(--accent)" strokeWidth="16" strokeLinecap="round" fill="none" opacity="0.06" />
            <path
              d={d}
              stroke="url(#legGrad)"
              strokeWidth="9"
              strokeLinecap="round"
              fill="none"
              opacity={legOpacity(side)}
            />
          </g>
        ))}

        {/* data particles flowing down each leg (CSS offset-path) */}
        {active &&
          [LEG_LEFT, LEG_RIGHT].flatMap((d, leg) =>
            [0, 1, 2].map((i) => (
              <circle
                key={`${leg}-${i}`}
                className="figure-particle"
                r={2.2 - i * 0.5}
                fill="var(--accent)"
                style={{
                  offsetPath: `path("${d}")`,
                  animationDuration: `${leg ? 2.2 : 2.6}s`,
                  animationDelay: `${i * 0.7 + leg * 0.35}s`,
                }}
              />
            )),
          )}

        {/* sensor nodes (thigh + shin, both legs) */}
        {Object.entries(LIMB_ANCHORS).map(([name, { x, y }], i) => {
          const state = limbState(name)
          const color = state ? STATE_VAR[state] : 'var(--ink-3)'
          return (
            <g key={name}>
              {active && state === 'good' && (
                <circle
                  className="figure-ping"
                  cx={x}
                  cy={y}
                  r="7"
                  stroke={color}
                  fill="none"
                  style={{ animationDelay: `${i * 0.4}s` }}
                />
              )}
              <circle
                className={active && state === 'good' ? 'figure-node' : ''}
                cx={x}
                cy={y}
                r="4.4"
                fill={color}
                opacity={state ? 1 : 0.35}
                style={{ animationDelay: `${i * 0.4}s` }}
              >
                <title>{name.replace('_', ' ')}</title>
              </circle>
            </g>
          )
        })}

        {hero && (
          <>
            <text x="34" y="181" className="figure-label" textAnchor="end">
              THIGH
            </text>
            <text x="30" y="255" className="figure-label" textAnchor="end">
              SHIN
            </text>
            <text x="126" y="181" className="figure-label">
              THIGH
            </text>
            <text x="130" y="255" className="figure-label">
              SHIN
            </text>
          </>
        )}
      </svg>
    </div>
  )
}

export { LIMB_ANCHORS }
