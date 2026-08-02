# Implementation Plan — master index & agent briefing

| | |
|---|---|
| Status | Set in stone as the build order (revised 2026-08-02: staged, biomech-first, task-granular). |
| Task detail | [tasks/STAGE1.md](tasks/STAGE1.md) · [tasks/STAGE2.md](tasks/STAGE2.md) · [tasks/STAGE3.md](tasks/STAGE3.md) |
| Related | [PLAN.md](PLAN.md) · [TRD.md](TRD.md) · [BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) |

## The three stages (user-mandated order)

| Stage | Goal | Deployed? | Frontend | Auth |
|---|---|---|---|---|
| **1** | Real biomech model on live real-time data, tested locally | No — local only | Minimal `/debug` viewer | None |
| **2** | VPS deploy + history windows + predictions + insights | Yes — public VPS | Crude, disposable | **None — fully public (accepted interim risk)** |
| **3** | Designed product frontend, login, polish | Yes | Product UI from mockup + design session | Preset-account login |

Anti-rework rules (apply to every task):
- The end-state architecture ([TRD.md](TRD.md) §1) never changes — stages only decide
  **when components turn on** (TRD §1.1 table).
- Stable interfaces ([BACKEND_SCHEMA.md](BACKEND_SCHEMA.md) §5) and config keys
  ([TRD.md](TRD.md) §7) may only change together with a doc update in the same task.
- Only the stage-2 crude UI is throwaway. Metric count is fixed: m1..m5 + composite.

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
| S1-T05 | Simulator (replays `squats.bin` @600Hz, N devices, loss/reorder/jitter/drift) | T03 | — |
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
  simulator/simulate.py
  backend/
    Dockerfile  pyproject.toml
    common/     config.py  durations.py  packet.py  redis_keys.py  scaling.py
    ingest/     main.py  udp.py  state.py  align.py  jitter.py  ticker.py
                biomech.py  publish.py
    api/        main.py  ws.py  writer.py  queries.py  auth.py  deps.py  debug.html
                routes/(auth devices metrics forecasts insights health)
                jobs/(predict.py insights.py)  seed_users.py
    migrations/ 001_init.sql  migrate.py
    tests/
  frontend/     (stage 2: crude → stage 3: product UI)
```

## Set in stone vs later

- **Set in stone:** stage order + exit criteria; the task decomposition and IDs;
  end-state architecture; stable interfaces; config keys; 5 primitives + composite;
  stage 2 public.
- **Deferred:** biomech spec (S1-T14), prediction model (S3-T09 backlog session),
  insight catalogue (backlog session), final UI design (S3-T01), production
  durations, HMAC packet auth.
