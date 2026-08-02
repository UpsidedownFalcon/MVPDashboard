# MVP Dashboard — Injury-Risk Prediction

A trainer-facing web dashboard that predicts when trainees are approaching injury.
1–5 wearable devices stream raw IMU data over UDP to an ingest service; a Python
biomechanical pipeline converts it into constant 60Hz metric streams (5 primitives +
1 composite risk index per device); the dashboard shows live charts, historical
rolling windows, regression-based forecasts of the composite, and rules-based
insights. Built in three stages: local real-time biomech, public VPS deployment with
intelligence, then the designed product frontend with login.

**Start here: read [docs/PLAN.md](docs/PLAN.md) first.** It anchors the full doc
suite (TRD, backend schema, app flow, implementation plan, and per-stage task lists).

## Quickstart (stage 1: local real-time pipeline)

Prereqs: Docker Desktop (WSL2 backend) and [uv](https://docs.astral.sh/uv/)
(`irm https://astral.sh/uv/install.ps1 | iex`). Then, from the repo root:

```powershell
# 1. one-time setup
Copy-Item .env.example .env          # single source of config truth (edit as needed)
uv sync --dev                        # creates .venv with Python 3.12 + deps

# 2. start the backend services (redis + ingest + api)
docker compose up -d --build

# 3. feed it data — replay recorded squats as N wearable devices
uv run python simulator/simulate.py --devices 5
#    knobs: --loss 5 --reorder 5 --jitter 20 --drift 200 --rate 600 --seed 1

# 4. watch it live
Start-Process http://localhost:8000/debug
```

Every device panel shows the composite + m1..m5 charts at 60Hz, quality %,
online badge and per-sensor input rates. `GET localhost:8000/api/health` is the
first place to look when anything misbehaves.

Tests and the stage-1 validation matrix:

```powershell
uv run pytest backend/tests/                        # unit + integration tests
docker compose --profile debug up -d                # exposes redis on 127.0.0.1:6379
uv run pytest backend/tests/test_ws.py              # WS throughput test (needs ^)
uv run python scripts/validate_stage1.py            # full matrix (~15 min; --quick for a smoke run)
```

### Real wearables on the LAN

Point the devices at your dev machine's LAN IP, UDP port 5005. Windows
Defender Firewall must allow inbound UDP 5005 (elevated prompt):

```powershell
netsh advfirewall firewall add rule name="MVPDash UDP 5005" dir=in action=allow protocol=UDP localport=5005
```

Find your LAN IP with `ipconfig` (Wi-Fi/Ethernet IPv4 address). Devices
auto-register on their first packet and appear on `/debug` within seconds.

### Gotchas

- **UDP stops arriving after `docker compose up -d` recreates the ingest
  container** (Docker Desktop's UDP port proxy can go stale): run
  `docker compose restart ingest` once and traffic flows again.
- The api service is bound to `127.0.0.1:8000` on purpose (stage 1 is
  local-only); nothing except UDP 5005 is reachable from the LAN.

## Configuration

Everything is wired from a single root `.env` (copy [.env.example](.env.example),
which documents every key). No other config location exists on purpose.
