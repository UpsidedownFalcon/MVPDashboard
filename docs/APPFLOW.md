# App Flow — user journeys & data journeys

| | |
|---|---|
| Status | Set in stone (mirrors [TRD.md](TRD.md); updates only if the TRD changes). **This end state is the CURRENT state** — all three stages shipped 2026-08-03, so the auth flows (§1.1, §3) and data flows 2.2–2.4 are live, not pending. See TRD §1.1. |
| Related | [UIUX.md](UIUX.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) |

## 1. User flows

### 1.1 Login
```
visit any route ─▶ has valid JWT cookie? ──yes──▶ requested page
        │no
        ▼
     /login ─▶ POST /api/auth/login ─▶ 401: inline error, stay
                       │200 (Set-Cookie: httpOnly JWT)
                       ▼
                      /  (overview)
```
Logout: `POST /api/auth/logout` (clears cookie) → `/login`. Expired cookie: any 401
from REST or a WS close with code 4401 → redirect `/login`.

### 1.2 Watch live → drill down → rename
```
/ overview ─▶ GET /api/devices (cards) ─▶ open the app-wide WS /ws/live
   │  charts render at 60Hz from the rAF buffer; numeric readouts refresh from a
   │  250 ms snapshot; status events flip online/offline badges
   ├─▶ click ✏ on card ─▶ inline edit ─▶ PATCH /api/devices/:id ─▶ name everywhere
   └─▶ click card ─▶ /device/:id
         ├─ GET /api/metrics/recent?device=:id&seconds=30  (chart backfill)
         ├─ WS stream splices in (live section)
         ├─ GET /api/metrics/windows?device=:id            (window meta, poll 60s)
         ├─ GET /api/metrics/history?device=:id&window=&buckets=  (History tab, poll 60s)
         ├─ GET /api/forecasts/latest?device=:id           (Projections tab, poll 60s;
         │                                                  `provisional` arrives on the wire
         │                                                  while bootstrapping but is not
         │                                                  surfaced — demo posture 2026-08-05,
         │                                                  UIUX §4)
         ├─ GET /api/insights/timeline?device=:id          (Insights tab — the advice TIMELINE
         │                                                  since 2026-08-06: live + stored
         │                                                  insights bucketed over PAST_WINDOWS,
         │                                                  ≤3 actions per bucket, poll 10s.
         │                                                  Survives page reloads; /current
         │                                                  remains as the pure live view)
         └─ GET /api/insights?device=:id&limit=100         (evidence join for those cards,
                                                            same 10s. The Overview chip uses
                                                            the same route at limit=5 / 30s)
```
The WS is **one app-wide connection** opened by `LiveProvider` on entering the
authed shell — it carries all devices (the server's `?devices=` filter is unused).

### 1.3 Device lifecycle (trainer's view)
```
wearable powers on ─▶ first packet ─▶ auto-registered (name = device ID)
  ─▶ card appears "online" ─▶ trainer renames to wearer
wearable silent > OFFLINE_AFTER_S ─▶ status event ─▶ the card badge flips to offline
  (last-seen shows on the detail overlay only)
  ─▶ the card and sidebar entry STAY — offline badge, frozen live column, and the
     stored history/projections/insights all remain browsable (user decision
     2026-08-06, reversing the 2026-08-02 "silent >10s disappears" rule;
     OFFLINE_HIDE_MS survives only as the calibration badge's re-arm threshold)
wearable returns ─▶ online again (same identity, same name)
```

## 2. Data flows

### 2.1 Hot path: packet → pixel (target ≪ 250ms end-to-end)
```
sensor sample ─UDP─▶ ingest: raw deque (bounded)
  ─batch decode (numpy)─▶ sync/CRC check (drop+count)
  ─▶ per-sensor unwrap ts ─▶ per-(device,source) clock offset → server time
  ─▶ jitter buffer (50ms reorder window)
  ─▶ 60Hz ticker: gather ~10 samples/limb → frames{limb: [n,6]}
  ─▶ biomech.compute → m1..m5, composite (+quality)
  ─▶ Redis PUBLISH ticks
        ├─▶ api: WS hub → per-client bounded queue → browser → uPlot (rAF)
        └─▶ api: write buffer → asyncpg COPY every 1s → metrics hypertable
```

### 2.2 History: tick → the History-tab period stats
```
metrics (60Hz rows, 30d retention)
  ─continuous aggregate policy (in-DB, ~1min)─▶ metrics_1m (forever)
  ─GET /api/metrics/windows─▶ for each PAST_WINDOWS duration:
      windows <= 5m read the RAW `metrics` table; larger ones read `metrics_1m`.
      (`metrics_1m` is materialized-only, so its newest 1-2 min do not exist yet —
       up to 40% of a 5m window. The raw table has no such lag.)
  ─▶ the History-tab period selector, trend arrow and coverage chip
     (frontend polls every 60s)
```

### 2.3 Prediction: history → forecast chart
```
every PREDICT_INTERVAL_S (60s), per device:
  BOOTSTRAP path (until metrics_1m holds >=10 buckets for this device):
    read PREDICT_BOOTSTRAP_BUCKET_S (15s) buckets off the RAW hypertable over
    PREDICT_BOOTSTRAP_WINDOW, project PREDICT_BOOTSTRAP_HORIZONS capped by the
    observed span -> model_version 'trend-ols-boot-1', response `provisional: true`.
    First forecast lands in ~2.5-3.5 min instead of 15-20.
  STEADY path:
  read composite from metrics_1m over PREDICT_TRAIN_WINDOW
  ─▶ predict.fit(history) ─▶ {horizon: (pred, ci_low, ci_high)}
  ─▶ INSERT forecasts (one row per horizon, keyed by made_at)
GET /api/forecasts/latest ─▶ newest made_at per device ─▶ forecast chart
```

### 2.4 Insights: trends + forecasts → feed
```
every INSIGHT_INTERVAL_S (15s), per device:
  inputs: the INSIGHT_LIVE_WINDOW read (30s off the RAW table — this is what every
          rule means by "now") + window aggregates (2.2) + latest forecasts (2.3)
  ─▶ rule list evaluates (each: predicate → severity, message, evidence)
  ─▶ cooldown check (INSIGHT_COOLDOWN_S per device+rule, severity-ranked)
  ─▶ INSERT insights (append-only EVENT log)
       ├─▶ GET /api/insights            ─▶ Overview chip + the evidence join
       ├─▶ GET /api/insights/current    ─▶ the STATE view: rows inside INSIGHT_HOLD_S
       │     grouped on action_id, newest row per rule, ranked, cut to
       │     INSIGHT_MAX_ACTIONS (3)
       └─▶ GET /api/insights/timeline   ─▶ the TIMELINE view (2026-08-06): the same
             rows bucketed by age over PAST_WINDOWS (live / 5m / 30m / 2h with the
             shipped config), each bucket grouped exactly like /current and cut to
             INSIGHT_MAX_ACTIONS, newest-first ─▶ the Insights tab's advice stack
```

### 2.5 Online/offline status
```
ingest: every 1s → SET last_seen:dev:{id} = server ts (+ per-sensor keys)
        no packets for OFFLINE_AFTER_S ─▶ ticker suspends (no stale ticks)
api:    watches last_seen keys → WS event {type:"status", dev, online, last_seen}
        GET /api/devices merges DB registry + live last_seen
```

### 2.6 Degradation modes (by design — see TRD §1 backpressure rule)
| Failure | Effect | Never affected |
|---|---|---|
| DB slow/down | api write buffer caps (~60s) then drops oldest + counts; window/forecast queries error visibly | live WS stream, ingest |
| api down | no dashboard; ticks published meanwhile are lost (gap visible later) | ingest keeps processing |
| ingest down | devices' data lost while down; dashboard shows all offline | history browsing, api |
| Redis down | live+status stop until back (compose restarts it) | DB contents |
| browser tab slow | that client's queue drops oldest | other clients, server |
| packet loss/reorder | quality % drops; jitter buffer reorders within 50ms | tick cadence (holds last) |

## 3. Auth flow detail (REST + WS)
JWT (HS256) in httpOnly Secure SameSite=Lax cookie, set by login, `JWT_EXPIRE_HOURS`
lifetime. Browser sends it automatically on same-origin REST **and the WS upgrade**
(no token in JS/localStorage). api validates on every request; WS validates at
handshake and re-checks expiry every 60 s mid-connection, closing 4401.
