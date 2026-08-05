# PRD — Injury-Risk Prediction Dashboard (MVP)

| | |
|---|---|
| Status | Approved and **delivered** — all 3 stages built and deployed 2026-08-03 (see §7). The stage table is a record of what shipped, not a forecast; F1–F9 are live and the F1–F10 acceptance run (S3-T08) is the one open item. |
| Owner | bhavy@hippos.life |
| Date | 2026-08-02 |
| Related | [PLAN.md](PLAN.md) · [TRD.md](TRD.md) · [UIUX.md](UIUX.md) · [APPFLOW.md](APPFLOW.md) |

## 1. Problem

Trainers cannot see when a trainee's lower-limb loading pattern is drifting toward
injury risk. Wearable IMU devices (4 sensors on thighs/shins) already capture the raw
motion data, but there is no system that ingests it live, converts it into
biomechanically meaningful metrics, and tells the trainer — in the moment and ahead of
time — who is at risk and what to do about it.

## 2. Users

- **Trainer (primary):** watches multiple trainees at once during sessions; needs
  at-a-glance risk state per person, drill-down per person, and plain-language guidance.
  Logs in with a preset account. Not technical.
- **Operator/developer (secondary, us):** provisions devices, monitors data quality,
  maintains the system.

## 3. Goals (MVP)

1. Live view: for every online device, stream 5 primitive metrics + 1 composite metric
   at 60Hz with no perceptible latency.
2. History: per device, show each metric aggregated over 3 configurable past windows
   (testing: minutes; deployment: e.g. 1h / 1d / 3d).
3. Prediction: per device, show the forecast **composite** metric over multiple
   configurable future horizons, with uncertainty.
4. Insights: plain-language, actionable messages derived from past trends + forecasts,
   with severity levels.
5. Multi-device: all online devices visible simultaneously; devices appear automatically
   when they start streaming and are renameable to the wearer's name.
6. Access control: login page; preset username/password accounts only.
7. Publicly hosted: devices send UDP to a public IP; dashboard reachable over HTTPS
   on the owner's domain.

## 4. Non-goals (MVP)

- No self-service account signup, password reset, or role management.
- No per-user device scoping (all logged-in users see all devices) — *TBD later*.
- No raw high-rate (~640Hz) storage or replay; no video; no mobile app (responsive web only).
- No alerting/push notifications outside the dashboard — *possible later*.
- No medical claims — this is a training-load guidance tool, not a diagnostic device.
- No device management/firmware features; devices are configured out of band.

## 5. Features & acceptance criteria

| # | Feature | Acceptance criteria |
|---|---|---|
| F1 | Login | Preset accounts only; wrong password rejected; session persists across refresh; logout works; all dashboard routes and the live stream require auth. |
| F2 | Device grid | Every device that has streamed within the offline threshold (default 2s) shows as **online**; going silent flips the badge to offline without a page refresh. Unknown device IDs auto-appear. |
| F3 | Device rename | Default name = device ID; trainer edits it inline; name persists and shows everywhere. |
| F4 | Live charts | 6 metrics per device rendered at 60Hz; chart stays smooth with 5 devices online; on page load the last ~30s is backfilled, then live data splices in seamlessly. |
| F5 | Data quality | Per-device quality indicator (share of expected sensor samples arriving); visibly degrades when packet loss is simulated. |
| F6 | History windows | 3 past windows, durations from config only; each shows avg (and min/max for composite) of all 6 metrics; values match SQL spot-checks. |
| F7 | Forecasts | Composite-only forecast for each configured horizon, with confidence band, refreshed on the configured interval; chart shows recent actuals + forecast continuation. |
| F8 | Insights | Rules produce severity-tagged (info/warning/alert) plain-language messages with the evidence that fired them; the advice panel shows the ≤3 grouped actions currently standing, strongest first, while the event log at `/api/insights` stays newest-first; no duplicate spam (cooldown). |
| F9 | Hosting | Dashboard live at `https://<subdomain>.<domain>` with a valid certificate; a simulator run from a remote network appears live on the hosted dashboard. |
| F10 | Config | Windows, horizons, limb map, ports, thresholds, retention all changeable in one `.env` file + restart; no code edits. |

## 6. Set in stone vs. to be decided

**Set in stone (do not re-litigate in later sessions):**
- Feature set F1–F10 above; 5 primitives + 1 composite; composite-only forecasting.
- 3 past windows / N future horizons, both config-driven durations.
- Preset-account auth model; single shared view of all devices.
- 60Hz live rendering; devices auto-register; rename in UI.
- Packet format and sensor topology (see [TRD.md](TRD.md) §3).

**To be decided later** — status 2026-08-05: the first three are **delivered**;
what remains open on them is refinement with sports-scientist input:
- The biomechanical definitions of the 5 primitives + composite — **delivered**
  ([biomech/SPEC.md](biomech/SPEC.md)); real metric names/units are on the UI.
- Prediction model beyond the linear-regression stub — **delivered** (`trend-ols-1`
  + its bootstrap variant), and the 8-rule insight catalogue shipped; exact
  production window/horizon durations remain a config decision.
- Final visual design — **delivered** ([UIUX.md](UIUX.md) has been the binding
  spec since the S3-T01 design session, 2026-08-03).
- Preset user list; whether trainers ever get scoped device visibility.

## 7. Staged delivery & success measures

Features land in three user-mandated stages. The full feature set (§5) is unchanged —
only the order is staged.

| Stage | Scope | Features delivered | Success measure (stage exit) |
|---|---|---|---|
| **1 — Local biomech** | Real biomech model on live real-time data; local machine only (simulator + wearables on LAN). No deploy, no DB, no auth. | F4 (live 60Hz), F5 (quality), F2 partially (online/offline in debug viewer) — real metrics, not stubs | User signs off on real-time metric quality watching the debug viewer; 5 devices at the device's measured ~640Hz sustained with stable 60Hz output. |
| **2 — Deploy + intelligence** | VPS hosting, persistence, history windows, forecasts, insights. Crude disposable AI-generated frontend. **Fully public temporarily — accepted risk** (no login until stage 3; revisit if identifiable trainee data appears). | F2, F3, F6, F7, F8, F9, F10 (crude UI) | Simulator from a remote network → hosted dashboard shows live charts + populated history/forecast/insights over HTTPS on the real domain; real wearables then switched to the VPS IP. |
| **3 — Product** | Properly designed frontend (user's mockup + design session), login system, polish, hardening backlog. | F1, final F2–F8 UX | Full acceptance list F1–F10 passes end-to-end; public access ends (auth enforced everywhere). |
