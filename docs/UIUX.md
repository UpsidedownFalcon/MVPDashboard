# UI/UX Design — Dashboard

| | |
|---|---|
| Status | **DRAFT** — this document specifies the **stage 3 product UI**. Structure/behavior set in stone; visual design TBD pending the user's React mockup (dropped in `mockup/`, refined in the stage-3 design session). Before stage 3, the UI is: stage 1 = a minimal `/debug` live-chart page (no design, no login); stage 2 = a crude disposable AI-generated dashboard (functional only, fully public, no login). Neither needs to follow this spec. |
| Related | [PRD.md](PRD.md) · [APPFLOW.md](APPFLOW.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) |

## 1. Screen inventory — SET IN STONE

| Route | Screen | Purpose |
|---|---|---|
| `/login` | Login | preset-account sign-in; the only unauthenticated route |
| `/` | Overview (device grid) | all devices at a glance, live |
| `/device/:id` | Device detail | one trainee: live + history + forecast + insights |

Responsive web (desktop-first; usable on a tablet at the gym). Dark theme default
(*final palette TBD with mockup*).

## 2. Login — SET IN STONE (styling TBD)

Centered card: app name, username, password, sign-in button, inline error on failure
("Invalid username or password" — never which one). Redirects to `/` on success;
already-authed visits to `/login` redirect to `/`. Header everywhere else shows
username + logout.

## 3. Overview screen — behavior SET IN STONE, layout draft

```
┌ Header: [App name]      [conn ●] [user ▾ logout] ┐
├──────────────────────────────────────────────────┤
│  ┌─ DeviceCard ────────────┐ ┌─ DeviceCard ────┐ │
│  │ ✏ Asha K.     ● online  │ │ ✏ dev-31  ● off │ │
│  │ quality ▮▮▮▮▯  98%      │ │ last seen 4m ago│ │
│  │ ┌ live composite chart ┐│ │ (charts frozen) │ │
│  │ │   (uPlot, 60Hz)      ││ │                 │ │
│  │ └──────────────────────┘│ │                 │ │
│  │ m1 m2 m3 m4 m5 (live #) │ │                 │ │
│  │ ⚠ 1 active insight      │ │                 │ │
│  └─────────[open ▸]────────┘ └─────────────────┘ │
└──────────────────────────────────────────────────┘
```

- One card per **registered device**; online cards sort first. Online/offline badge
  flips live (WS `status` events, no refresh). New devices appear automatically on
  first packet.
- Card content: editable name (✏ inline: click → input → Enter saves via
  `PATCH /api/devices/:id`, Esc cancels), online badge, quality %, a single live
  **composite** sparkline chart (60Hz), the 5 primitive current values as numbers,
  highest-severity active insight chip. Click anywhere → device detail.
- Header connection dot = WS state (green connected / amber reconnecting).

## 4. Device detail — behavior SET IN STONE, layout draft

```
┌ ← back   ✏ Asha K.  ● online  quality 98%        ┐
├───────────────────────────────────────────────────┤
│ LIVE (60Hz)                                       │
│ [composite — large uPlot chart, last 30–60s]      │
│ [primitives m1..m5 — compact stacked uPlot charts]│
├───────────────────────────────────────────────────┤
│ HISTORY          ┌ per configured window ┐        │
│ [5m card][30m card][2h card]   (from config)      │
│  each: composite avg (min–max), m1..m5 avgs,      │
│  trend arrow vs previous equal window             │
├───────────────────────────────────────────────────┤
│ FORECAST (composite only)                         │
│ [ECharts: recent actual line + forecast points    │
│  per horizon with CI band; "made at" timestamp]   │
├───────────────────────────────────────────────────┤
│ INSIGHTS                                          │
│ [feed, newest first: severity chip + message +    │
│  time + evidence expander]                        │
└───────────────────────────────────────────────────┘
```

- Window cards and forecast horizons are **rendered from config** (labels like "past
  5m" / "next 30m" generated from the duration strings) — the UI never hardcodes them.
- Metric labels come from one frontend constants file (`lib/metrics.ts`) mapping
  `m1..m5, composite` → display names/units. Names are now **fixed** by biomech
  SPEC §5: `m1` Impact, `m2` Loading Rate, `m3` Accumulated Load, `m4` Movement
  Control, `m5` L/R Balance, `composite` Injury Risk — all 0–100, `m1..m5`
  nullable. Nulls render as **gaps, never 0** (SPEC §9).

## 5. Live chart behavior — SET IN STONE

- uPlot, canvas-rendered, one rolling buffer per device (default 60s @ 60Hz = 3,600
  points; append + shift, redraw on rAF). No SVG chart libs for live data.
- On mount: REST backfill (`/api/metrics/recent?seconds=30`) then splice WS stream;
  small (~250ms) render delay absorbs network jitter for a smooth line.
- Hidden tab (`visibilitychange`): pause rendering, drop incoming to newest; on
  return, re-backfill and resume (avoids browser-throttling artifacts).
- WS drop/reconnect: exponential backoff 1s→10s; charts freeze, connection dot amber;
  on reconnect, backfill gap via REST.
- Offline device: charts freeze with "offline — last seen HH:MM:SS" overlay.

## 6. Severity & status vocabulary — SET IN STONE (colors draft)

| State | Chip | Color (draft) |
|---|---|---|
| info | ℹ info | blue |
| warning | ⚠ warning | amber |
| alert | ⛔ alert | red |
| online / offline | ● | green / gray |
| quality | % + 5-bar meter | green ≥90, amber 60–90, red <60 |

**Biomech flags — MUST be rendered** (biomech SPEC §10 makes this the UI's only job
in the calibration story; the tick's `f` field carries them, BACKEND_SCHEMA §2):

| Flag | Means | Weight |
|---|---|---|
| `cal_failed` | a sensor is motionless but disagrees with gravity — hardware fault | alert |
| `degraded_sensors` | fewer sensors than mapped, or one never streamed: the affected metric is **never** coming | alert |
| `saturated` | clipped window; `m1`/`m2` suppressed | alert |
| `uncalibrated` | running on defaults; `m4`/`m5` carry per-sensor gain bias | warning |
| `partial` | a required sensor went inactive mid-session | warning |
| `no_shank` | impact falls back to all limbs | warning |
| `carried_over` | calibrated from a previous session, not measured today | info |
| `warming_up` | `m4`/`m5` inside their 60 s / 30 s warm-up — a value *is* coming | muted |
| `unvalidated` | `m4`/`m5` have no real-data validation yet (SPEC §11.1) | muted |

`warming_up` and `degraded_sensors` must never look alike: one promises a value,
the other says none is coming. The uncalibrated→calibrated transition is a visible
step change in `m4`/`m5` and should be marked as such, not shown as a change in the
athlete (SPEC §3.8).

## 7. Empty & error states — SET IN STONE

- No devices yet: "Waiting for devices… point wearables at `<IP>:<UDP_PORT>`".
- No insights: "No insights yet — all metrics in normal range."
- History windows without enough data: card shows "collecting… (n min of 5m)".
- Forecast before first prediction run: "First forecast in ~N min."
- API errors: non-blocking toast + retry; auth failures redirect to `/login`.

## 8. To be decided in the frontend session (with `mockup/`)

- Visual identity: palette, typography, spacing, logo, light/dark.
- Exact card layout/composition vs. the mockup's ideas; any additional views.
- Real metric names/units/ranges once biomech is specified; chart y-axis scaling policy.
- Whether primitives get their own forecast display (currently: composite only — PRD).
- Mobile layout refinements.
