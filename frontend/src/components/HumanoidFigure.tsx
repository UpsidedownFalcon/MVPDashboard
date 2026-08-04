// The brand's "live human telemetry" signature (UIUX §10) — a genuinely 3D
// wireframe/point-cloud figure on canvas, rotating slowly about Y with real
// perspective projection, glowing sensor nodes and depth-sorted draw order.
//
// Canvas + hand-rolled projection rather than three.js: the scene is ~200
// points and a dozen bones, and three.js would add ~600 KB to the bundle for
// geometry this simple. No new dependency.
//
// The props API is UNCHANGED from the SVG version, so Device.tsx and
// Overview.tsx keep working untouched: per-limb liveness colours, `active`
// (only animate while streaming) and the m5 `emphasis` side all carry over.
//
// prefers-reduced-motion freezes the rotation and the pulses (the figure still
// renders, just static).

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

// --- skeleton, in model space (y up, x right, z toward viewer) --------------
const HEAD: V3 = [0, 1.62, 0]
const NECK: V3 = [0, 1.44, 0]
const CHEST: V3 = [0, 1.24, 0]
const WAIST: V3 = [0, 0.98, 0]
const HIP_L: V3 = [-0.14, 0.92, 0]
const HIP_R: V3 = [0.14, 0.92, 0]
const KNEE_L: V3 = [-0.16, 0.5, 0.02]
const KNEE_R: V3 = [0.16, 0.5, 0.02]
const ANKLE_L: V3 = [-0.17, 0.06, 0]
const ANKLE_R: V3 = [0.17, 0.06, 0]
const SHOULDER_L: V3 = [-0.22, 1.38, 0]
const SHOULDER_R: V3 = [0.22, 1.38, 0]
const ELBOW_L: V3 = [-0.3, 1.06, 0.02]
const ELBOW_R: V3 = [0.3, 1.06, 0.02]
const HAND_L: V3 = [-0.28, 0.74, 0.04]
const HAND_R: V3 = [0.28, 0.74, 0.04]

/** Torso/arm bones — structural, drawn dim. */
const FRAME: [V3, V3][] = [
  [NECK, CHEST], [CHEST, WAIST], [WAIST, HIP_L], [WAIST, HIP_R],
  [CHEST, SHOULDER_L], [CHEST, SHOULDER_R],
  [SHOULDER_L, ELBOW_L], [ELBOW_L, HAND_L],
  [SHOULDER_R, ELBOW_R], [ELBOW_R, HAND_R],
]

/** Instrumented bones: the four sensor segments, drawn bright. */
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

/** Torso point cloud — sampled once, gives the body volume without a mesh. */
function buildCloud(): V3[] {
  const pts: V3[] = []
  let seed = 7
  const rnd = () => {
    // deterministic LCG: the cloud must not shimmer between renders
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296
  }
  for (let i = 0; i < 150; i++) {
    const t = rnd()
    const y = 0.95 + t * 0.5 // waist -> neck
    const taper = 0.17 + 0.1 * Math.sin(t * Math.PI)
    const ang = rnd() * Math.PI * 2
    pts.push([Math.cos(ang) * taper, y, Math.sin(ang) * taper * 0.55])
  }
  return pts
}
const CLOUD = buildCloud()

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

      // --- torso point cloud, depth-sorted so far points sit behind ---------
      const cloud = CLOUD.map(project).sort((a, b) => a.z - b.z)
      for (const pt of cloud) {
        const depth = (pt.z + 0.3) / 0.6 // 0 = back, 1 = front
        ctx.globalAlpha = 0.1 + 0.32 * Math.max(0, Math.min(1, depth))
        ctx.fillStyle = ACCENT_DEEP
        ctx.beginPath()
        ctx.arc(pt.x, pt.y, 0.9 * pt.persp, 0, Math.PI * 2)
        ctx.fill()
      }
      ctx.globalAlpha = 1

      // --- structural frame -------------------------------------------------
      ctx.lineCap = 'round'
      for (const [a, b] of FRAME) {
        const pa = project(a)
        const pb = project(b)
        ctx.globalAlpha = 0.35
        ctx.strokeStyle = 'rgba(255,255,255,0.28)'
        ctx.lineWidth = 1.6 * pa.persp
        ctx.beginPath()
        ctx.moveTo(pa.x, pa.y)
        ctx.lineTo(pb.x, pb.y)
        ctx.stroke()
      }
      ctx.globalAlpha = 1

      // head ring
      const ph = project(HEAD)
      ctx.strokeStyle = 'rgba(255,255,255,0.35)'
      ctx.lineWidth = 1.6
      ctx.beginPath()
      ctx.arc(ph.x, ph.y, 0.13 * scale * ph.persp, 0, Math.PI * 2)
      ctx.stroke()

      // --- instrumented limbs ----------------------------------------------
      for (const bone of LIMB_BONES) {
        const state = p.limbs ? p.limbs[bone.limb] : 'good'
        const color = state ? STATE_COLOR[state] : 'rgba(255,255,255,0.18)'
        const emph = !p.emphasis || p.emphasis === bone.side ? 1 : 0.55
        const pa = project(bone.a)
        const pb = project(bone.b)

        // glow underlay
        ctx.globalAlpha = 0.16 * emph
        ctx.strokeStyle = color
        ctx.lineWidth = 11 * pa.persp
        ctx.beginPath()
        ctx.moveTo(pa.x, pa.y)
        ctx.lineTo(pb.x, pb.y)
        ctx.stroke()

        // core
        ctx.globalAlpha = 0.92 * emph
        ctx.lineWidth = 3 * pa.persp
        ctx.beginPath()
        ctx.moveTo(pa.x, pa.y)
        ctx.lineTo(pb.x, pb.y)
        ctx.stroke()
      }
      ctx.globalAlpha = 1

      // --- data particles travelling down each instrumented bone ------------
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
            ctx.arc(pp.x, pp.y, 1.7 * pp.persp, 0, Math.PI * 2)
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
          ctx.arc(n.p.x, n.p.y, (3 + ring * 16) * n.p.persp, 0, Math.PI * 2)
          ctx.stroke()
        }

        // node glow + core
        ctx.globalAlpha = 0.3 * emph
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(n.p.x, n.p.y, (5 + pulse * 2.5) * n.p.persp, 0, Math.PI * 2)
        ctx.fill()

        ctx.globalAlpha = emph
        ctx.beginPath()
        ctx.arc(n.p.x, n.p.y, 3 * n.p.persp, 0, Math.PI * 2)
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
