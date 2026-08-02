# App Flow — user journeys & data journeys

| | |
|---|---|
| Status | Set in stone (mirrors [TRD.md](TRD.md); updates only if the TRD changes). Describes the **end state**: auth flows (§1.1, §3) activate in stage 3 — stages 1–2 run unauthenticated; data flows 2.2–2.4 activate in stage 2 (no DB in stage 1). See TRD §1.1 for the stage table. |
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
/ overview ─▶ GET /api/devices (cards) ─▶ open WS /ws/live
   │  cards update at 60Hz; status events flip online/offline badges
   ├─▶ click ✏ on card ─▶ inline edit ─▶ PATCH /api/devices/:id ─▶ name everywhere
   └─▶ click card ─▶ /device/:id
         ├─ GET /api/metrics/recent?device=:id&seconds=30  (chart backfill)
         ├─ WS stream splices in (live section)
         ├─ GET /api/metrics/windows?device=:id            (history cards)
         ├─ GET /api/forecasts/latest?device=:id           (forecast chart)
         └─ GET /api/insights?device=:id  (+ poll every 30s)
```

### 1.3 Device lifecycle (trainer's view)
```
wearable powers on ─▶ first packet ─▶ auto-registered (name = device ID)
  ─▶ card appears "online" ─▶ trainer renames to wearer
wearable silent > OFFLINE_AFTER_S ─▶ status event ─▶ card shows offline + last-seen
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

### 2.2 History: tick → window card
```
metrics (60Hz rows, 30d retention)
  ─continuous aggregate policy (in-DB, ~1min)─▶ metrics_1m (forever)
  ─GET /api/metrics/windows─▶ for each PAST_WINDOWS duration:
      SELECT avg(...) FROM metrics_1m WHERE bucket > now()-duration
  ─▶ window cards (frontend polls every 60s)
```

### 2.3 Prediction: history → forecast chart
```
every PREDICT_INTERVAL_S, per device:
  read composite from metrics_1m over PREDICT_TRAIN_WINDOW
  ─▶ predict.fit(history) ─▶ {horizon: (pred, ci_low, ci_high)}
  ─▶ INSERT forecasts (one row per horizon, keyed by made_at)
GET /api/forecasts/latest ─▶ newest made_at per device ─▶ forecast chart
```

### 2.4 Insights: trends + forecasts → feed
```
every INSIGHT_INTERVAL_S, per device:
  inputs: window aggregates (2.2) + latest forecasts (2.3)
  ─▶ rule list evaluates (each: predicate → severity, message, evidence)
  ─▶ cooldown check (INSIGHT_COOLDOWN_S per device+rule)
  ─▶ INSERT insights ─▶ GET /api/insights (frontend poll 30s) ─▶ feed + card chips
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
handshake and closes 4401 on expiry.
