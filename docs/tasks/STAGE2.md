# Stage 2 tasks — VPS + persistence + history/predictions/insights + crude UI

> Scope: everything becomes publicly hosted; historical windows, forecasts and
> insights land; frontend is a **crude, disposable** AI-generated UI. **No auth this
> stage (user-accepted interim risk).**
> Required reading: [../PLAN.md](../PLAN.md), [../TRD.md](../TRD.md) §§1,5–8,
> [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) (all), [../APPFLOW.md](../APPFLOW.md) §2.
> Precondition: stage-1 exit signed off (S1-T15).

---

## S2-T01 — Database service + migrations + schema

**Goal:** TimescaleDB running with the full schema.
**Depends:** stage 1 complete.
**Files:** `docker-compose.yml` (activate `db`), `backend/migrations/001_init.sql`,
`backend/migrations/migrate.py`, `backend/tests/test_migrations.py`.

Steps:
1. Compose: move `db` out of the stage2 profile — `timescale/timescaledb:latest-pg17`,
   volume `dbdata`, healthcheck `pg_isready`, env from `.env`, **no host port**
   (add `127.0.0.1:5432:5432` under the `debug` profile only). `api` gets
   `depends_on: db: condition: service_healthy`.
2. `001_init.sql`: exactly [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §1 (users,
   devices, metrics hypertable + index, retention placeholder, `metrics_1m` cagg +
   policy, forecasts, insights). Use `{{METRICS_RETENTION}}` placeholder.
3. `migrate.py`: asyncpg; creates `schema_migrations(filename, applied_at)`; applies
   `NNN_*.sql` in order inside a transaction (cagg statements outside — Timescale
   requires it; split on a `-- NOTRANSACTION` marker); substitutes
   `{{METRICS_RETENTION}}` from config. Idempotent. Run automatically by the api
   container entrypoint before uvicorn (simplest ops), also runnable manually.
4. Add `asyncpg`, `pandas` to pyproject.
5. Test (needs db up): migrate twice → second run no-op; insert a metrics row;
   `metrics_1m` refreshes (call `refresh_continuous_aggregate` manually in test).

**Done check:** fresh volume → `compose up` → `\dt` shows all tables; re-up is a
no-op; hypertable + policies visible in `timescaledb_information` views.

## S2-T02 — Metrics writer + device auto-registration

**Goal:** every tick lands in the DB without ever blocking the live path.
**Depends:** ⛓ S2-T01.
**Files:** `backend/api/writer.py`, wire into `api/main.py` lifespan,
`backend/tests/test_writer.py`.

Steps:
1. Second consumer of the existing Redis subscription (share the hub's fan-in):
   append ticks to a list; every 1s flush via
   `copy_records_to_table('metrics', records=...)` on the asyncpg pool.
2. Buffer cap = 60s of ticks (`60 × OUTPUT_HZ × devices` est.); beyond → drop
   oldest, `db_dropped += 1`. Flush failure ⇒ keep buffer, retry next second,
   log once per state change (not per second).
3. Device auto-registration: maintain an in-memory set of known device_ids
   (loaded at startup); unseen id ⇒
   `INSERT INTO devices (device_id, display_name) VALUES ($1,$1) ON CONFLICT DO NOTHING`.
4. Counters (`db_buffer`, `db_dropped`, `rows_written`) exposed for `/api/health`.
5. Test: fake ticks through a real db — rows appear, batch size right; stop db
   container mid-test → no exception escapes, counters climb, recovery works.

**Done check:** live with simulator: `SELECT count(*) FROM metrics` grows at
~60×N/s; `docker compose stop db` for 30s → `/debug` stream unaffected; restart →
writing resumes (gap visible in data, counted).

## S2-T03 — Query layer + REST: devices, recent, windows

**Goal:** the read API for history ([../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §3
shapes, exactly).
**Depends:** ⛓ S2-T02.
**Files:** `backend/api/queries.py`, `backend/api/routes/devices.py`,
`backend/api/routes/metrics.py`, `backend/tests/test_routes_metrics.py`.

Steps:
1. `queries.py` (all parameterized asyncpg):
   - `recent(device, seconds)` → compact arrays from `metrics`.
   - `windows(device)` → for each `PAST_WINDOWS` duration: avgs of m1..m5,
     composite avg/min/max, quality, `n`; **source table rule:** window < 5 min ⇒
     query `metrics` directly, else `metrics_1m`. Trend: compare composite avg vs
     the preceding equal-length window → `up|down|flat` (±2% dead-band).
   - `devices()` → registry rows merged with Redis `last_seen`/quality (online =
     last_seen within `OFFLINE_AFTER_S`) + per-sensor stats from `ingest:stats`.
2. Routes per schema doc: `GET /api/devices`, `PATCH /api/devices/{id}`
   (display_name, validated non-empty ≤64 chars), `GET /api/metrics/recent`,
   `GET /api/metrics/windows`. 404 unknown device. No auth this stage.
3. Tests against seeded db: window maths hand-verified (insert known rows, assert
   avgs/trend); rename round-trip; unknown device 404.

**Done check:** with simulator running ≥10 min: `curl /api/metrics/windows?device=30`
returns 3 windows whose values match a hand-run SQL query; rename via curl persists
across api restart.

## S2-T04 — Forecast job + endpoint

**Goal:** composite-only predictions per configured horizon, stored and queryable.
**Depends:** ⛓ S2-T03. **∥** S2-T05.
**Files:** `backend/api/jobs/predict.py`, `backend/api/routes/forecasts.py`,
`backend/tests/test_predict.py`.

Steps:
1. `fit(history: DataFrame, horizons) -> dict[timedelta, Forecast]` — the **stable
   interface**. Stub implementation: numpy `polyfit(deg=1)` on (minutes_from_start,
   composite avg from `metrics_1m`); `pred = clip(intercept + slope·(t_end+h))`;
   `ci = ±1.96·residual_std·sqrt(1 + h/train_span)`. `model_version='linreg-stub-1'`.
   *(Real model: dedicated session later — only this function changes.)*
2. Job loop in api lifespan: every `PREDICT_INTERVAL_S`, per device with ≥10
   buckets in `PREDICT_TRAIN_WINDOW`: fit → INSERT one `forecasts` row per horizon
   (same `made_at`). Log skips. Exceptions caught per device (one bad device never
   kills the loop).
3. `GET /api/forecasts/latest?device=` → newest `made_at` group, schema-doc shape;
   empty-state shape if none.
4. Test: seed synthetic rising composite → forecast increases with horizon, CI
   widens; endpoint shape validated.

**Done check:** test config (`FUTURE_HORIZONS=2m,5m,10m`, `PREDICT_INTERVAL_S=30`):
first forecasts ≤1 min after enough data; values sane vs. the visible trend.

## S2-T05 — Insight rules engine + endpoint

**Goal:** actionable, non-spammy messages from windows + forecasts.
**Depends:** ⛓ S2-T03. **∥** S2-T04.
**Files:** `backend/api/jobs/insights.py`, `backend/api/routes/insights.py`,
`backend/tests/test_insights.py`.

Steps:
1. Rule framework: `Rule(rule_id, severity, evaluate(ctx) -> Evidence|None,
   message(ctx, evidence) -> str)`; `ctx` = device (with display_name), window
   aggregates, latest forecasts, thresholds from config. `RULES: list[Rule]` is the
   **extension point** — catalogue TBD in a later session.
2. Starter rules (thresholds via env `INSIGHT_WARN_THRESHOLD=0.7`,
   `INSIGHT_ALERT_THRESHOLD=0.85`):
   - `composite_high`: shortest-window composite avg ≥ warn/alert threshold →
     "…sustained high load — consider reducing intensity."
   - `rising_risk`: mid-window trend `up` AND any forecast ≥ alert threshold →
     "…risk projected to reach {pred:.2f} within {horizon} — schedule rest."
   - `data_quality` (info): quality < 0.8 over shortest window → "check sensor fit."
3. Loop every `INSIGHT_INTERVAL_S`: evaluate per device; insert only if no same
   (device, rule_id) insight within `INSIGHT_COOLDOWN_S`; store evidence JSONB.
4. `GET /api/insights?device=&limit=` newest first.
5. Tests: rule predicates on synthetic ctx; cooldown suppression; message contains
   display_name (uses rename).

**Done check:** lower thresholds live → insight fires exactly once per cooldown
window; endpoint returns it with evidence.

## S2-T06 — Full health endpoint

**Goal:** one URL that answers "is everything OK and if not where".
**Depends:** ⛓ S2-T02 (+ counters from T04/T05 when merged).
**Files:** `backend/api/routes/health.py`.

Steps: assemble per schema doc §3: db ping, redis ping, `ingest:stats` dump,
writer counters, ws counters, job last-run timestamps + last error string.
**Done check:** all-green normally; stop db → `db:false` + writer counters visible;
stop simulator → per-sensor rates decay to 0.

## S2-T07 — Crude dashboard (disposable)

**Goal:** every stage-2 feature visible in a browser; zero design effort.
**Depends:** ⛓ S2-T03; better after T04+T05 (can stub their panels).
**Files:** `frontend/` (Vite + React + TS, minimal), Caddy serves `frontend/dist`.

Steps:
1. Scaffold Vite React TS; deps: `uplot`, `@tanstack/react-query`. Keep it ugly on
   purpose — this UI is replaced in stage 3; **do not** invest in components or
   styling beyond function. Reuse of `/debug`'s chart wiring is fine.
2. One page, sections per device (from `GET /api/devices`, refetch 10s):
   name + inline rename (PATCH), online badge, quality, live composite + m1..m5
   charts (WS + `recent` backfill), 3 window cards (poll 60s), forecast list/chart
   with CI numbers (poll 60s), insights feed (poll 30s).
3. Vite dev proxy `/api` `/ws` → `localhost:8000`; `npm run build` → `dist/`.

**Done check:** PRD §5 features F2–F8 all visible and functioning in one tab
against the simulator (login F1 excluded by design this stage).

## S2-T08 — Caddy + production compose

**Goal:** the stack is one `docker compose up -d` on any Linux box.
**Depends:** ⛓ S2-T07.
**Files:** `deploy/Caddyfile`, compose (activate `caddy`), `deploy/deploy.sh`,
`backend/Dockerfile` (multi-stage build addition for frontend optional — simpler:
build frontend in a `node:20` build stage of a `frontend/Dockerfile` whose output
is a shared volume, or just build locally and commit `dist/` — choose the
Caddyfile-simplest: multi-stage caddy image that builds `frontend/` then serves it).

Steps:
1. Caddyfile per TRD §8: site `{$DOMAIN}` → static `frontend/dist` via
   `try_files`, `/api/*` and `/ws/*` → `api:8000` (WS upgrade works via
   `reverse_proxy` automatically). Plus `:80` local fallback site for LAN testing
   without the domain.
2. Compose: `caddy` service with 80/443 published, volumes for caddy data/config;
   `restart: unless-stopped` everywhere; remove the stage2 profile markers (whole
   stack is now default-on except `debug` profile).
3. `deploy/deploy.sh`: `ssh $VPS 'cd ~/MVPDashboard && git pull && docker compose up -d --build && docker compose ps'`.
4. Local full-stack rehearsal: `docker compose up -d --build` on the dev machine,
   browse `http://localhost` (Caddy :80 fallback) — everything works without Vite dev server.

**Done check:** local rehearsal green: crude UI at `http://localhost`, live data,
history, forecasts, insights, `/api/health` — all through Caddy.

## S2-T09 — VPS provisioning  ⚑ USER REQUIRED

**Goal:** a hardened empty server with DNS pointing at it.
**Depends:** ⛓ none (can run ∥ any stage-2 task; needed before T10).

Steps:
1. User: create Hetzner account; CX22-class VPS, Ubuntu 24.04, add SSH public key
   (key-only from the start). Note the public IPv4.
2. User: Cloudflare DNS **A record, DNS-only (grey cloud)** `dash.<domain>` → VPS IP.
   (Grey cloud is required: UDP can't proxy, and Caddy needs direct ACME.)
3. Agent (over SSH, scripted in `deploy/provision.sh`):
   `apt update && apt install -y docker.io docker-compose-plugin` (or get.docker.com),
   create non-root user with docker group, disable SSH password auth
   (`PasswordAuthentication no`), `ufw allow 22/tcp 80/tcp 443/tcp 5005/udp && ufw enable`,
   unattended-upgrades on.
4. `git clone` the repo (private → deploy key or HTTPS token — user provides).

**Done check:** `ssh vps docker run hello-world` works; `ufw status` shows exactly
the four rules; `dig dash.<domain>` returns the VPS IP; password SSH rejected.

## S2-T10 — Deploy + WAN validation  ⚑ stage-2 exit

**Goal:** the real thing, publicly reachable; wearables cut over.
**Depends:** ⛓ S2-T08 + S2-T09.

Steps:
1. Create prod `.env` on the VPS: real `POSTGRES_PASSWORD`/`JWT_SECRET`
   (`openssl rand -hex 32`), `DOMAIN=dash.<domain>`; windows/horizons stay at test
   values until the user flips them.
2. `docker compose up -d --build`; watch Caddy obtain the certificate.
3. WAN test from the Windows machine:
   `python simulator/simulate.py --target <VPS_IP>:5005 --devices 3 --loss 3` →
   `https://dash.<domain>` shows live charts from another network; after 10+ min,
   windows/forecasts/insights populate; `/api/health` clean; `quality` reflects loss.
4. Resilience: `reboot` the VPS → stack self-heals (`restart: unless-stopped`);
   confirm data resumes.
5. User points real wearables at `<VPS_IP>:5005`; verify auto-registration + rename.
6. Record: follow-up backlog item — nightly `pg_dump` off-box (stage 3 / P3.4).

**Done check (stage-2 exit):** PRD §7 stage-2 success measure met end-to-end.
