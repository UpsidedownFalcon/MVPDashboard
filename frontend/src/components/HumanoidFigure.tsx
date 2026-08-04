// The brand's "live human telemetry" signature (UIUX §10) — a genuinely 3D
// human figure rendered entirely as a point cloud on canvas, rotating slowly
// about Y with real perspective projection and per-point depth sorting.
//
// The body is sampled once from anatomically proportioned primitives (tapered
// capsules + ellipsoids) with a deterministic LCG, so the cloud never shimmers
// between renders. The four instrumented segments (thigh/shin × L/R) are lit
// in the limb's liveness colour; the rest of the body is dim brand teal.
//
// Canvas + hand-rolled projection rather than three.js: ~1,850 points don't
// justify ~600 KB of bundle. No new dependency.
//
// The props API is UNCHANGED from previous versions, so Device.tsx and
// Overview.tsx keep working untouched: per-limb liveness colours, `active`
// (only animate while streaming) and the m5 `emphasis` side all carry over.
//
// prefers-reduced-motion freezes the rotation, shimmer and pulses (the figure
// still renders, just static).

import { useEffect, useRef } from 'react'

export type LimbState = 'good' | 'warning' | 'critical'

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

type V3 = readonly [number, number, number]

const ACCENT = '#21F3FC'
const ACCENT_DEEP = '#2BBECD'
const STATE_COLOR: Record<LimbState, string> = {
  good: ACCENT,
  warning: '#FAB219',
  critical: '#D03B3B',
}

// --- joint landmarks, in model space (y up, x right, z toward viewer) -------
const HIP_L: V3 = [-0.14, 0.9, 0]
const HIP_R: V3 = [0.14, 0.9, 0]
const KNEE_L: V3 = [-0.16, 0.5, 0.02]
const KNEE_R: V3 = [0.16, 0.5, 0.02]
const ANKLE_L: V3 = [-0.17, 0.07, 0]
const ANKLE_R: V3 = [0.17, 0.07, 0]

/** Instrumented segments: the four sensor-carrying bones. */
const LIMB_BONES: { limb: string; a: V3; b: V3; side: 'left' | 'right' }[] = [
  { limb: 'left_thigh', a: HIP_L, b: KNEE_L, side: 'left' },
  { limb: 'left_shin', a: KNEE_L, b: ANKLE_L, side: 'left' },
  { limb: 'right_thigh', a: HIP_R, b: KNEE_R, side: 'right' },
  { limb: 'right_shin', a: KNEE_R, b: ANKLE_R, side: 'right' },
]

/** Where each sensor sits along its bone. */
const NODES: { limb: string; at: V3; side: 'left' | 'right' }[] = [
  { limb: 'left_thigh', at: mid(HIP_L, KNEE_L), side: 'left' },
  { limb: 'left_shin', at: mid(KNEE_L, ANKLE_L), side: 'left' },
  { limb: 'right_thigh', at: mid(HIP_R, KNEE_R), side: 'right' },
  { limb: 'right_shin', at: mid(KNEE_R, ANKLE_R), side: 'right' },
]

function mid(a: V3, b: V3): V3 {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2]
}

// --- point-cloud body --------------------------------------------------------

interface CloudPoint {
  // model space
  x: number
  y: number
  z: number
  /** 'body' or an instrumented limb key ('left_thigh' …). */
  part: string
  side: 'left' | 'right' | null
  /** per-point shimmer phase so the cloud breathes without blinking. */
  phase: number
  /** per-point size variance. */
  size: number
  // projected, mutated every frame
  px: number
  py: number
  pz: number
  persp: number
}

/**
 * Sample the body surface once. Deterministic LCG: the cloud must not shimmer
 * between renders or mounts.
 */
function buildBody(): CloudPoint[] {
  const pts: CloudPoint[] = []
  let seed = 7
  const rnd = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }

  const push = (x: number, y: number, z: number, part: string, side: 'left' | 'right' | null) => {
    pts.push({
      x, y, z, part, side,
      phase: rnd() * Math.PI * 2,
      size: 0.8 + rnd() * 0.5,
      px: 0, py: 0, pz: 0, persp: 1,
    })
  }

  /** Points on an ellipsoid surface (head, shoulder caps, hands). */
  const blob = (c: V3, rx: number, ry: number, rz: number, count: number) => {
    for (let i = 0; i < count; i++) {
      const v = 2 * rnd() - 1
      const ang = rnd() * Math.PI * 2
      const s = Math.sqrt(1 - v * v)
      push(c[0] + Math.cos(ang) * s * rx, c[1] + v * ry, c[2] + Math.sin(ang) * s * rz, 'body', null)
    }
  }

  /**
   * Points on a tapered capsule from `a` to `b`. `zSquash` flattens the
   * cross-section front-to-back (torso); `bulge` swells the middle (muscle).
   */
  const capsule = (
    a: V3, b: V3, r0: number, r1: number, count: number,
    opts: { part?: string; side?: 'left' | 'right' | null; zSquash?: number; bulge?: number } = {},
  ) => {
    const { part = 'body', side = null, zSquash = 1, bulge = 0 } = opts
    const ax = b[0] - a[0]
    const ay = b[1] - a[1]
    const az = b[2] - a[2]
    const len = Math.hypot(ax, ay, az) || 1
    const ux = ax / len, uy = ay / len, uz = az / len
    // frame ⊥ axis: n1 = normalize(cross([0,0,1], u)) ≈ world x for these
    // near-vertical segments, n2 = cross(u, n1) ≈ world z
    let n1x = -uy
    let n1y = ux
    const n1z = 0
    const n1len = Math.hypot(n1x, n1y) || 1
    n1x /= n1len; n1y /= n1len
    // n2 = cross(u, n1)
    const n2x = uy * n1z - uz * n1y
    const n2y = uz * n1x - ux * n1z
    const n2z = ux * n1y - uy * n1x
    for (let i = 0; i < count; i++) {
      const u = rnd()
      const ang = rnd() * Math.PI * 2
      const r = (r0 + (r1 - r0) * u) * (1 + bulge * Math.sin(Math.PI * u))
      const co = Math.cos(ang) * r
      const si = Math.sin(ang) * r * zSquash
      push(
        a[0] + ax * u + n1x * co + n2x * si,
        a[1] + ay * u + n1y * co + n2y * si,
        a[2] + az * u + n1z * co + n2z * si,
        part, side,
      )
    }
  }

  // head + neck
  blob([0, 1.6, 0], 0.095, 0.12, 0.1, 130)
  capsule([0, 1.46, 0], [0, 1.53, 0], 0.05, 0.045, 30, { zSquash: 0.9 })

  // torso: chest widest at the shoulder line, waist narrow, pelvis re-widens
  capsule([0, 1.2, 0], [0, 1.41, 0], 0.15, 0.185, 270, { zSquash: 0.6 })
  capsule([0, 1.0, 0], [0, 1.2, 0], 0.125, 0.15, 190, { zSquash: 0.6 })
  capsule([0, 0.86, 0], [0, 1.0, 0], 0.15, 0.127, 180, { zSquash: 0.66 })

  // shoulders + arms + hands
  blob([-0.21, 1.37, 0], 0.06, 0.055, 0.055, 45)
  blob([0.21, 1.37, 0], 0.06, 0.055, 0.055, 45)
  capsule([-0.23, 1.33, 0], [-0.3, 1.06, 0.02], 0.048, 0.04, 70, { bulge: 0.1 })
  capsule([0.23, 1.33, 0], [0.3, 1.06, 0.02], 0.048, 0.04, 70, { bulge: 0.1 })
  capsule([-0.3, 1.06, 0.02], [-0.28, 0.76, 0.05], 0.038, 0.028, 60, { bulge: 0.12 })
  capsule([0.3, 1.06, 0.02], [0.28, 0.76, 0.05], 0.038, 0.028, 60, { bulge: 0.12 })
  blob([-0.28, 0.71, 0.06], 0.035, 0.05, 0.03, 25)
  blob([0.28, 0.71, 0.06], 0.035, 0.05, 0.03, 25)

  // instrumented legs — denser, lit by liveness colour at draw time
  capsule(HIP_L, KNEE_L, 0.078, 0.052, 160, { part: 'left_thigh', side: 'left', bulge: 0.1 })
  capsule(HIP_R, KNEE_R, 0.078, 0.052, 160, { part: 'right_thigh', side: 'right', bulge: 0.1 })
  capsule(KNEE_L, ANKLE_L, 0.05, 0.032, 140, { part: 'left_shin', side: 'left', bulge: 0.22 })
  capsule(KNEE_R, ANKLE_R, 0.05, 0.032, 140, { part: 'right_shin', side: 'right', bulge: 0.22 })

  // feet
  capsule([-0.17, 0.045, 0.01], [-0.17, 0.025, 0.14], 0.035, 0.027, 40, { zSquash: 0.9 })
  capsule([0.17, 0.045, 0.01], [0.17, 0.025, 0.14], 0.035, 0.027, 40, { zSquash: 0.9 })

  return pts
}

const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

export default function HumanoidFigure({ variant, limbs, active = true, emphasis }: Props) {
  const hostRef = useRef<HTMLCanvasElement>(null)
  // keep the latest props in a ref so the rAF loop never restarts on a tick
  const propsRef = useRef({ limbs, active, emphasis, variant })
  propsRef.current = { limbs, active, emphasis, variant }

  useEffect(() => {
    const canvas = hostRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // per-instance cloud: projection fields are mutated every frame, and two
    // mounted figures must not fight over one shared array
    const body = buildBody()

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let raf = 0
    let yaw = -0.35
    let t = 0
    let last = performance.now()

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const r = canvas.getBoundingClientRect()
      canvas.width = Math.max(1, Math.round(r.width * dpr))
      canvas.height = Math.max(1, Math.round(r.height * dpr))
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw)
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      const p = propsRef.current
      const animate = p.active && !reduced
      if (animate) {
        yaw += dt * 0.42 // slow, ~15 s per turn
        t += dt
      }

      const r = canvas.getBoundingClientRect()
      const W = r.width
      const H = r.height
      if (W < 2 || H < 2) return
      ctx.clearRect(0, 0, W, H)

      // camera: perspective projection, figure centred and fitted to height
      const scale = H / 2.05
      const cx = W / 2
      const cy = H * 0.94
      const camZ = 3.2
      const cos = Math.cos(yaw)
      const sin = Math.sin(yaw)
      // point radii track the canvas so compact stays crisp, not cluttered
      const sizeScale = Math.max(0.55, Math.min(1, H / 380))

      const project = (v: V3) => {
        const x = v[0] * cos - v[2] * sin
        const z = v[0] * sin + v[2] * cos
        const persp = camZ / (camZ - z)
        return {
          x: cx + x * scale * persp,
          y: cy - v[1] * scale * persp,
          z,
          persp,
        }
      }

      // --- soft glow underlay beneath each instrumented segment -------------
      // (sits under the points: reads as light, never as a drawn line)
      ctx.lineCap = 'round'
      for (const bone of LIMB_BONES) {
        const state = p.limbs ? p.limbs[bone.limb] : 'good'
        if (!state) continue
        const emph = !p.emphasis || p.emphasis === bone.side ? 1 : 0.55
        const pa = project(bone.a)
        const pb = project(bone.b)
        ctx.globalAlpha = 0.1 * emph
        ctx.strokeStyle = STATE_COLOR[state]
        ctx.lineWidth = 12 * pa.persp * sizeScale
        ctx.beginPath()
        ctx.moveTo(pa.x, pa.y)
        ctx.lineTo(pb.x, pb.y)
        ctx.stroke()
      }
      ctx.globalAlpha = 1

      // --- the body: project every point, depth-sort, draw back to front ----
      for (const pt of body) {
        const x = pt.x * cos - pt.z * sin
        const z = pt.x * sin + pt.z * cos
        const persp = camZ / (camZ - z)
        pt.px = cx + x * scale * persp
        pt.py = cy - pt.y * scale * persp
        pt.pz = z
        pt.persp = persp
      }
      body.sort((a, b) => a.pz - b.pz)

      for (const pt of body) {
        const depth = clamp01((pt.pz + 0.35) / 0.7) // 0 = far, 1 = near
        let alpha: number
        if (pt.part === 'body') {
          ctx.fillStyle = ACCENT_DEEP
          alpha = 0.09 + 0.3 * depth
        } else {
          const state = p.limbs ? p.limbs[pt.part] : 'good'
          if (state) {
            ctx.fillStyle = STATE_COLOR[state]
            alpha = 0.3 + 0.55 * depth
          } else {
            // limb the device never mapped: present, but visibly dark
            ctx.fillStyle = '#ffffff'
            alpha = 0.08 + 0.12 * depth
          }
          if (p.emphasis && p.emphasis !== pt.side) alpha *= 0.55
        }
        if (animate) alpha *= 0.82 + 0.22 * Math.sin(t * 1.5 + pt.phase)
        ctx.globalAlpha = alpha
        const rad = pt.size * (0.7 + 0.55 * depth) * pt.persp * sizeScale
        ctx.beginPath()
        ctx.arc(pt.px, pt.py, rad, 0, Math.PI * 2)
        ctx.fill()
      }
      ctx.globalAlpha = 1

      // --- data particles travelling down each instrumented segment ---------
      if (animate) {
        for (let bi = 0; bi < LIMB_BONES.length; bi++) {
          const bone = LIMB_BONES[bi]
          const state = p.limbs ? p.limbs[bone.limb] : 'good'
          if (state !== 'good') continue
          for (let k = 0; k < 3; k++) {
            const u = ((t * 0.55 + bi * 0.21 + k * 0.34) % 1)
            const pos: V3 = [
              bone.a[0] + (bone.b[0] - bone.a[0]) * u,
              bone.a[1] + (bone.b[1] - bone.a[1]) * u,
              bone.a[2] + (bone.b[2] - bone.a[2]) * u,
            ]
            const pp = project(pos)
            ctx.globalAlpha = 0.85 * (1 - Math.abs(u - 0.5) * 1.2)
            ctx.fillStyle = '#ffffff'
            ctx.beginPath()
            ctx.arc(pp.x, pp.y, 1.8 * pp.persp * sizeScale, 0, Math.PI * 2)
            ctx.fill()
          }
        }
        ctx.globalAlpha = 1
      }

      // --- sensor nodes, depth-sorted --------------------------------------
      const nodes = NODES.map((n, i) => ({ ...n, p: project(n.at), i })).sort(
        (a, b) => a.p.z - b.p.z,
      )
      for (const n of nodes) {
        const state = p.limbs ? p.limbs[n.limb] : 'good'
        const color = state ? STATE_COLOR[state] : 'rgba(255,255,255,0.25)'
        const emph = !p.emphasis || p.emphasis === n.side ? 1 : 0.55
        const pulse = animate && state === 'good'
          ? 0.5 + 0.5 * Math.sin(t * 2.4 - n.i * 0.9)
          : 0.6

        // sonar ring
        if (animate && state === 'good') {
          const ring = ((t * 0.55 + n.i * 0.25) % 1)
          ctx.globalAlpha = 0.4 * (1 - ring) * emph
          ctx.strokeStyle = color
          ctx.lineWidth = 1.2
          ctx.beginPath()
          ctx.arc(n.p.x, n.p.y, (3 + ring * 16) * n.p.persp * sizeScale, 0, Math.PI * 2)
          ctx.stroke()
        }

        // node glow + core
        ctx.globalAlpha = 0.3 * emph
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(n.p.x, n.p.y, (5 + pulse * 2.5) * n.p.persp * sizeScale, 0, Math.PI * 2)
        ctx.fill()

        ctx.globalAlpha = emph
        ctx.beginPath()
        ctx.arc(n.p.x, n.p.y, 3 * n.p.persp * sizeScale, 0, Math.PI * 2)
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }

    raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [])

  const hero = variant === 'hero'
  return (
    <div
      className={`figure figure-${variant} ${active ? 'is-active' : 'is-idle'}`}
      role="img"
      aria-label="Athlete wearing four leg sensors streaming live motion data"
    >
      {hero && <div className="figure-glow" aria-hidden />}
      {hero && <div className="figure-ring" aria-hidden />}
      <canvas ref={hostRef} className="figure-canvas" aria-hidden />
    </div>
  )
}
