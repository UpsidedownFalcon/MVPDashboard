# Implementation Plan — master index & agent briefing

| | |
|---|---|
| Status | Set in stone as the build order (revised 2026-08-02: staged, biomech-first, task-granular). All three stages shipped; work since then is tracked per change-set, not as new stages. |
| Task detail | [tasks/STAGE1.md](tasks/STAGE1.md) · [tasks/STAGE2.md](tasks/STAGE2.md) · [tasks/STAGE3.md](tasks/STAGE3.md) · [tasks/STAGE4.md](tasks/STAGE4.md) (demo frontend + military theme, 2026-09-12) |
| **Current work** | **Sleeve storage** — plan of record [`PLAN_msd_management.md`](../agent-docs/02_PLAN_msd_management.md) (approved 2026-09-23). Change-set 1 — the `/storage` page with a firmware-exact `CONFIG.TXT` editor and a verified log transfer, migration 006, `GET /api/config/udp-target`, and a sleeve's side seeded from its own `source_id` at registration (decision H) — **shipped 2026-09-23**; change-set 2 (a CSV byte-exact with `bin2csv.py`, a `.meta.json` and a plain-text summary ported from `sensor_stats.py` per transferred log, in a Web Worker beside `raw/`, plus the "CSV and summary" card with Convert missing and Retry) **built 2026-09-25** on branch `csv-summary`, plan of record [`03_PLAN_csv_summary.md`](../agent-docs/03_PLAN_csv_summary.md) (decisions O-V); no backend change. Previous change-set, shipped 2026-09-23: **unilateral knee sleeves** — [`PLAN_unilateral_devices.md`](../agent-docs/01_PLAN_unilateral_devices.md) (six work packages: common, ingest, api, simulator, frontend, docs): a second wearable kind on the same UDP port, dashboard-driven pairing, side and per-sleeve IMU full scale. Both touch the stable interfaces and config keys below, so read TRD §3/§4/§7 and BACKEND_SCHEMA §1/§3/§4/§5 before changing anything near them. |
| Related | [PLAN.md](PLAN.md) · [TRD.md](TRD.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) |

## The three stages (user-mandated order)

| Stage | Goal | Deployed? | Frontend | Auth |
|---|---|---|---|---|
| **1** | Real biomech model on live real-time data, tested locally | No — local only | Minimal `/debug` viewer (still served today, behind auth — S3-T05) | None |
| **2** | VPS deploy + history windows + predictions + insights | Yes — public VPS | Crude, disposable | **None — fully public (accepted interim risk)** |
| **3** | Designed product frontend, login, polish | Yes | Product UI from mockup + design session | Preset-account login |

Anti-rework rules (apply to every task):
- The end-state architecture ([TRD.md](TRD.md) §1) never changes — stages only decide
  **when components turn on** (TRD §1.1 table).
- Stable interfaces ([BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) §5) and config keys
  ([TRD.md](TRD.md) §7) may only change together with a doc update in the same task.
- Only the stage-2 crude UI was throwaway, and it has been deleted (S3-T07). Metric count is fixed: m1..m5 + composite.

## How to brief an agent on a task

Give the agent this, verbatim, plus the task ID:

> Read, in order: `docs/PLAN.md`, `docs/TRD.md`, `docs/BACKEND_SCHEMA.md`, then your
> task in `docs/tasks/STAGE<n>.md` (including its header block). Do exactly that
> task: respect its Files list, its Depends, and the stable interfaces. Do not
> change any interface, schema, config key, or another task's files without
> updating the corresponding doc in the same change. Finish by actually running the
> task's **Done check** and reporting its output. If the task conflicts with the
> docs or something is ambiguous, stop and ask — do not assume.

Tasks marked ⚑ need the user present (planning sessions, sign-offs, account
signups) — don't hand those to unattended agents.

## Master task list (chronological)

### Stage 1 — local real-time biomech ([tasks/STAGE1.md](tasks/STAGE1.md))

| ID | Task | Depends | Parallel OK with |
|---|---|---|---|
| S1-T01 | Repo scaffold (git, env template, tree) | — | — |
| S1-T02 | Backend package + config loader + duration parser | T01 | — |
| S1-T03 | Packet decoder/encoder (`common/packet.py`) + golden tests vs `example/` | T02 | T04 |
| S1-T04 | Compose file: redis + ingest + api skeletons (db/caddy profiled off) | T02 | T03 |
| S1-T05 | Simulator (replays `squats.bin` @640Hz, N devices, loss/reorder/jitter/drift) | T03 | — |
| S1-T06 | Ingest: UDP server + batch decode loop + stats | T04, T05 | — |
| S1-T07 | Ingest: timestamp unwrap + per-leg clock alignment + reboot detect | T06 | — |
| S1-T08 | Ingest: jitter buffer (reorder, late-drop) | T07 | — |
| S1-T09 | Ingest: 60Hz ticker + limb framing + quality + hold-last/suspend | T08 | — |
| S1-T10 | Ingest: stub biomech behind the stable interface | T09 | — |
| S1-T11 | Ingest: Redis publish (ticks + last_seen + stats) + final wiring | T10 | — |
| S1-T12 | Minimal api: WS fan-out + status events + `/debug` viewer + health | T11 (contract) | can scaffold ∥ T06–T11 |
| S1-T13 | Stage-1 stress/validation matrix + README quickstart | T12 | — |
| S1-T14 ⚑ | Biomech planning session → `docs/biomech/SPEC.md` | T13 | — |
| S1-T15 ⚑ | Real biomech implementation, perf guard, user sign-off (**stage exit**) | T14 | — |

### Stage 2 — deploy + intelligence ([tasks/STAGE2.md](tasks/STAGE2.md))

| ID | Task | Depends | Parallel OK with |
|---|---|---|---|
| S2-T01 | TimescaleDB service + migrations runner + full schema | stage 1 | T09 |
| S2-T02 | Metrics writer (COPY batches) + device auto-registration | T01 | — |
| S2-T03 | Query layer + REST: devices/rename, recent, windows | T02 | — |
| S2-T04 | Forecast job (linreg stub behind stable interface) + endpoint | T03 | T05 |
| S2-T05 | Insight rules engine + starter rules + endpoint | T03 | T04 |
| S2-T06 | Full `/api/health` | T02 | T04, T05 |
| S2-T07 | Crude disposable dashboard (all features, zero design) | T03 (T04/T05 for those panels) | — |
| S2-T08 | Caddy + production compose + local full-stack rehearsal | T07 | — |
| S2-T09 ⚑ | VPS provisioning + Cloudflare DNS-only + firewall | — | any |
| S2-T10 ⚑ | Deploy + WAN validation + wearable cutover (**stage exit**) | T08, T09 | — |

### Stage 3 — product ([tasks/STAGE3.md](tasks/STAGE3.md))

| ID | Task | Depends | Parallel OK with |
|---|---|---|---|
| S3-T01 ⚑ | Design session with `mockup/` → final UIUX.md | stage 2 | — |
| S3-T02 | Frontend foundation (typed api client, WS hook, tokens) | T01 | T05 |
| S3-T03 | Overview screen (grid, sparklines, rename, badges) | T02 | T05 |
| S3-T04 | Device detail (live, windows, forecast, insights) | T03 | T05 |
| S3-T05 | Auth backend (bcrypt+JWT cookie, guard everything incl. WS + `/debug`) | stage 2 | T02–T04 |
| S3-T06 | Login UI + route guards + session handling | T05, T02 | — |
| S3-T07 | Cutover to product UI + polish sweep | T03, T04, T06 | — |
| S3-T08 ⚑ | Full PRD F1–F10 acceptance run → `docs/ACCEPTANCE.md` (**MVP exit**) | T07 | — |
| S3-T10 | Stage-3 backend additions: migration 002 + `GET /api/metrics/history` (done 2026-08-03) | stage 2 | T02–T04 |
| S3-T09 | Hardening backlog (backups, HMAC, monitoring, prod durations, real models) | post-MVP | — |

## Repo layout (target end state)

```
MVPDashboard/
  .env.example  .gitattributes  .gitignore  docker-compose.yml  README.md
  pyproject.toml  uv.lock  .python-version   (uv workspace root: makes
                `uv run pytest backend/...` work from the repo root; the
                installable package itself is backend/ — S1-T02)
  docs/  (this suite + tasks/ + biomech/ + ACCEPTANCE.md)
  deploy/(Caddyfile, provision.sh, deploy.sh)
  example/      (existing sample data + parser — read-only reference)
  mockup/       (stage-3 input from user)
  scripts/validate_stage1.py   (S1-T13 validation matrix runner)
  scripts/kneesleeve/          (bin2csv.py, sensor_stats.py vendored verbatim
                                2026-09-25; the reference the CSV + summary
                                port is checked against; never edited)
  scripts/storage_goldens.py   (regenerates the convert goldens with them:
                                uv run --with matplotlib python scripts/storage_goldens.py)
  simulator/simulate.py
  backend/
    Dockerfile  pyproject.toml
    common/     config.py  durations.py  packet.py  redis_keys.py  scaling.py
                kinds.py  (2026-09-23: wearable kinds, unit ids, UnitConfig)
    ingest/     main.py  udp.py  state.py  align.py  jitter.py  ticker.py
                biomech.py  publish.py  unit_config.py
    api/        main.py  ws.py  writer.py  queries.py  auth.py  deps.py  debug.html
                unit_mirror.py  (sleeve registration + the api → Redis mirror)
                routes/(auth devices units metrics forecasts insights health config)
                jobs/(predict.py insights.py)  seed_users.py
    migrations/ 001_init.sql  002_insight_actions.sql
                003_insight_action_grouping.sql  004_insight_decisions.sql
                005_sleeve_units.sql  006_sleeve_side_backfill.sql  migrate.py
                (the runner applies them in filename order and records each in
                 schema_migrations; BACKEND_SCHEMA §1 carries the merged schema
                 and one paragraph per migration. 004 shipped 2026-08-07 with
                 Adopt/Override, 005 on 2026-09-23 with knee sleeves — this list
                 stopped at 003 until then — and 006, data-only, the same day
                 with sleeve storage: it seeds legacy NULL sides from the wire)
    tests/
  frontend/     (stage 2: crude → stage 3: product UI)
    src/lib/config.ts  metrics.ts  rig.ts  api.ts  ...   (UI constants, copy tables)
    src/lib/demo/            (the always-on synthetic soldiers, 2026-09-12)
    src/lib/storage/         (2026-09-23, sleeve storage: pure CONFIG.TXT model,
                              CRC32 + block scanner, verified-transfer engine,
                              page reducer and STORAGE_COPY; fsa.ts is the only
                              module touching the File System Access API)
    src/lib/storage/convert/ (2026-09-25, change-set 2: LOG -> CSV + meta.json
                              decoder byte-exact with bin2csv.py, streaming
                              statistics sink and the sensor_stats.py summary,
                              pipeline, worker protocol, destination scan; pure)
    src/lib/storage/convertQueue.ts  src/workers/convert.worker.ts
                             (one Web Worker, FIFO, one file at a time)
    src/lib/storage/fixtures/convert/  (8 synthetic BINs + goldens: *.csv.sha256,
                              *.meta.json, *.summary.txt, also for LOG_0010.head64)
    src/components/storage/  src/pages/Storage.tsx   (the /storage page; the
                              CSV and summary card is ConversionPanel.tsx)
    e2e/convert.html  e2e/convert.ts   (dev-only conversion dry run, npm run dev)
```

## Set in stone vs later

- **Set in stone:** stage order + exit criteria; the task decomposition and IDs;
  end-state architecture; stable interfaces; config keys; 5 primitives + composite;
  stage 2 public.
- **Deferred:** biomech spec (S1-T14), prediction model (S3-T09 backlog session),
  insight catalogue (backlog session), final UI design (S3-T01), production
  durations, HMAC packet auth.
