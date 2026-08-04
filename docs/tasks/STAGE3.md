# Stage 3 tasks — product frontend, login, polish

> Scope: replace the crude UI with the designed product, add the login system,
> close the acceptance list. Required reading: [../UIUX.md](../UIUX.md) (becomes the
> binding spec after S3-T01), [../BACKEND_SCHEMA.md](../BACKEND_SCHEMA.md) §3,
> [../APPFLOW.md](../APPFLOW.md) §§1,3, [../PRD.md](../PRD.md) §5.
> Precondition: stage-2 exit (system live on the VPS with real data flowing).

---

## S3-T01 — Design session  ⚑ USER REQUIRED

**Goal:** turn [../UIUX.md](../UIUX.md) from draft into the binding visual spec.
**Depends:** stage 2 complete.

Steps:
1. User drops their rough React mockup code into `mockup/`.
2. Dedicated session: reconcile mockup with UIUX.md — decide palette, typography,
   spacing scale, light/dark, logo/name, card layouts, chart styling, and the real
   metric display names/units (now known from `docs/biomech/SPEC.md`).
3. Rewrite UIUX.md as the final spec (remove DRAFT status, delete the §8 TBD list,
   add a design-tokens section: CSS variables for colors/spacing/typography).
   Before writing any chart styling decisions, consult the `dataviz` skill for
   accessible chart color/interaction choices.

**Done check:** user approves the updated UIUX.md in that session.

> **DONE 2026-08-03** — design session held; UIUX.md rewritten as the binding spec
> (brand tokens from `mockup/visual_guidelines`, validated chart palette, screen
> specs). Decisions: 5 primitives confirmed; new `GET /api/metrics/history`
> endpoint + `insights.action`/`rationale` fields (BACKEND_SCHEMA.md updated,
> implemented by S3-T10); Inter as UI typeface (TT Hoves is trial-licensed, logo
> assets only); offline devices hidden >10 s; per-sensor detail on both screens;
> forecasts stay composite-only. User approved the plan and mandated auto-execution
> through S3-T07 + deploy.

## S3-T02 — Frontend foundation

**Goal:** clean app skeleton the screens plug into (replaces crude UI codebase —
fresh `frontend/`, keep the old one until S3-T07 cutover, e.g. `frontend-crude/`).
**Depends:** ⛓ S3-T01.
**Files:** `frontend/` new Vite+React+TS app: `src/lib/api.ts`, `src/lib/ws.ts`,
`src/lib/auth.tsx` (stub until T05), `src/lib/metrics.ts`, `src/lib/config.ts`,
`src/theme.css`, router shell.

Steps:
1. Scaffold; deps: `react-router-dom`, `@tanstack/react-query`, `uplot`, `echarts`.
2. `lib/api.ts`: typed fetch wrapper for every route in BACKEND_SCHEMA §3
   (shared TS types file mirroring the JSON shapes); on 401 → redirect `/login`
   (inert until auth exists).
3. `lib/ws.ts`: reconnecting WS hook (backoff 1s→10s), tick/status demux,
   per-device subscriber API, connection-state signal for the header dot.
4. `lib/metrics.ts`: metric id → {label, unit, range, color} from the design tokens
   + biomech SPEC names.
5. `theme.css`: design tokens from S3-T01.

**Done check:** app builds; `/` renders shell with live connection dot against the
real backend.

## S3-T03 — Overview screen (device grid)

**Goal:** UIUX §3 for real.
**Depends:** ⛓ S3-T02.
**Files:** `src/pages/Overview.tsx`, `src/components/DeviceCard.tsx`.
*(As built: `StatusBadge` / `QualityMeter` / `RenameInline` / `SensorDots` / `SeverityChip` /
`FlagChips` all live together in `src/components/bits.tsx`; the sparkline is `LiveChart.tsx`
rendered at sparkline size, not a separate component.)*

Steps: device grid per UIUX §3 — online-first sort, live composite sparkline
(60Hz, uPlot, 30s buffer), 5 primitive live numbers, quality meter, highest-severity
insight chip, inline rename (Enter/Esc semantics, optimistic update + rollback),
click-through to detail. Empty state per UIUX §7.
**Done check:** matches UIUX §3 behaviors point-by-point with 5 live devices;
rename persists; badges flip ≤2s.

## S3-T04 — Device detail screen

**Goal:** UIUX §4 for real (live left column + Insights/History/Projections tabs).
**Depends:** ⛓ S3-T03 (shares components), ⛓ S3-T10 (history endpoint + insight fields).
**Files:** `src/pages/Device.tsx`, `src/components/LiveChart.tsx`,
`HistoryBars.tsx`, `ForecastChart.tsx`, `Tabs.tsx`.
*(As built: the insight panel is `InsightsPanel.tsx` with helpers in `lib/evidence.ts`.
`InsightFeed.tsx` and `ActionPanel.tsx` were interim components, since deleted.)*

Steps: live column — data-driven humanoid figure + current-risk hero + large
composite chart + stacked primitives (backfill-then-splice per UIUX §5, ~250ms
render delay, rAF batched); right column pill tabs per UIUX §4: Insights
(action-first cards + evidence expander), History (six small-multiple bar charts
from `GET /api/metrics/history`, period selector from `PAST_WINDOWS`, table-view
toggle), Projections (composite-only ECharts: recent actuals + per-horizon points
with CI band + made-at stamp, table-view toggle); offline overlay with last-seen.
**Done check:** UIUX §§4–7 behaviors verified live, incl. hidden-tab pause/resume
and WS-reconnect backfill (dev-tools network throttle test).

## S3-T05 — Auth backend

**Goal:** preset-account login enforced on every route and the WS.
**Depends:** ∥ S3-T02+ (backend work, parallel to frontend). ⛓ stage 2.
**Files:** `backend/api/auth.py`, `backend/api/deps.py`,
`backend/api/routes/auth.py`, `backend/api/seed_users.py`,
`backend/tests/test_auth.py`; pyproject: add `bcrypt`, `pyjwt` (bcrypt directly,
not `passlib[bcrypt]` — passlib is unmaintained and breaks against bcrypt ≥ 4.1).

Steps:
1. `seed_users.py`: idempotent, parses `SEED_USERS="alice:pw1,bob:pw2"` → bcrypt
   upserts; run by api entrypoint after migrations; user sets real accounts in
   prod `.env`.
2. `auth.py`: `login()` (verify bcrypt, mint HS256 JWT `sub`,`role`,`exp` =
   `JWT_EXPIRE_HOURS`, set cookie httpOnly+Secure+SameSite=Lax, name `session`);
   `logout()` clears; naive in-memory rate limit (5 fails/min/IP → 429).
3. `deps.py`: `require_user` dependency → applied to **every** REST route except
   `/api/auth/login`, `/api/health/live`; WS handshake validates the same cookie,
   closes 4401 when missing/expired (also mid-connection on expiry check each 60s).
4. Keep `/debug` behind auth too (it shows live data).
5. Tests: happy path, wrong password, expired token, cookie-less WS rejected,
   rate limit.

**Done check:** `pytest` green; curl without cookie → 401 on every data route;
with cookie → 200; WS without cookie closes 4401.

## S3-T06 — Login UI + auth wiring

**Goal:** UIUX §2; the app is unusable until signed in.
**Depends:** ⛓ S3-T05 + S3-T02.
**Files:** `src/pages/Login.tsx`, `src/lib/auth.tsx` (real), route guards.

Steps: login card per UIUX §2 (inline error, no username/password distinction);
auth context from `GET /api/auth/me`; guard all routes → redirect `/login`;
header user menu + logout; 401-anywhere → login redirect; WS 4401 → login redirect.
**Done check:** full flow in browser: fresh session → login → dashboard → logout;
refresh keeps session; expired cookie bounces to login.

## S3-T07 — Cutover + polish pass

**Goal:** product UI replaces crude UI in production.
**Depends:** ⛓ S3-T03, S3-T04, S3-T06.

Steps:
1. Point Caddy/compose at the new `frontend/dist`; delete `frontend-crude/`.
2. Polish sweep per UIUX §7: all empty/error states, toasts, favicons/title,
   responsive check on tablet width, dark-theme contrast pass (consult `dataviz`
   skill guidance for chart colors).
3. Re-run README quickstart from scratch on a clean machine to confirm docs match reality.

**Done check:** production URL serves the product UI; nothing references the crude app.

> **T02–T07 + T10 DONE 2026-08-03** (single auto-execution session, user-mandated):
> new `frontend/` product app (Vite+React+TS; sidebar shell, overview hero +
> projection-first panels, detail live column + Insights/History/Projections
> tabs, login); auth enforced on every route/WS (bcrypt direct + pyjwt, cookie
> `session`, WS 4401, JWT_SECRET required at startup, seed_users in compose
> entrypoint); migration 002 + `/api/metrics/history`; crude UI deleted and the
> caddy image now bakes the product build. `pytest`: 221 passed. Deployed to the
> VPS the same day. Remaining for stage-3 exit: S3-T08 acceptance run.

## S3-T08 — Full acceptance run  ⚑ stage-3 / MVP exit

**Goal:** every PRD acceptance criterion demonstrated.
**Depends:** ⛓ S3-T07.

Steps: walk PRD §5 F1–F10 one by one against the production deployment with the
simulator (loss/reorder on) AND real wearables; record pass/fail + evidence in
`docs/ACCEPTANCE.md` (created by this task). Fix and re-run any failures.
**Done check:** all ten green in `docs/ACCEPTANCE.md`; user sign-off.

## S3-T10 — Stage-3 backend additions (history endpoint + insight actions)

**Goal:** the two API extensions the S3-T01 design requires
(BACKEND_SCHEMA §1 migration 002, §3 `/api/metrics/history`).
**Depends:** ⛓ stage 2. ∥ parallel to S3-T02+ frontend work.
**Files:** `backend/migrations/002_insight_actions.sql`,
`backend/api/routes/metrics.py` (history route), `backend/common/queries.py`
(bucketed query helper), `backend/api/jobs/insights.py` (rules emit
`action`/`rationale`), `backend/tests/test_history.py`, tests for insight fields.

Steps:
1. Migration 002: `ALTER TABLE insights ADD COLUMN action TEXT, ADD COLUMN rationale TEXT;`
   (idempotent guard, runner picks it up after 001).
2. `GET /api/metrics/history?device&window&buckets` per BACKEND_SCHEMA §3: validate
   `window ∈ PAST_WINDOWS` (400 otherwise), `buckets` 1–96 default 24; bucket with
   `time_bucket(span, …)` over `metrics_1m` (or `metrics` when window < 5m — same
   source rule as `/windows`); rows→fixed-length bucket array, missing buckets `null`.
3. Starter rules updated to emit `action` (imperative) + `rationale` (why, with the
   numbers); `message` unchanged as the standalone summary. Ongoing stage-2 insight
   refinement extends the same fields.
4. Tests: window validation, bucket math vs SQL spot-check, null buckets, insight
   rows carry the new fields end-to-end.

**Done check:** `pytest` green; `curl /api/metrics/history?device=X&window=<PAST_WINDOWS[0]>`
returns the documented shape with correct bucket count against live data.

## S3-T09 — Hardening backlog (scheduled, not gating MVP)

- Nightly `pg_dump` → off-box storage (Hetzner storage box / B2) + restore test.
- Per-packet truncated HMAC on UDP + device allow-list (needs firmware change — TBD).
- Flip `PAST_WINDOWS`/`FUTURE_HORIZONS` to production durations (user decision).
- Basic uptime monitoring (e.g. UptimeRobot on `/api/health/live`) + disk alerts.
- Real prediction model session (replaces `predict.fit` stub).
- Insight rule catalogue session (extends `RULES`).
