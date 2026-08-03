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
if (-not (Test-Path .env)) { Copy-Item .env.example .env }   # never clobber an existing .env
uv sync --dev                        # creates .venv with Python 3.12 + deps

# 2. start the backend services (redis + ingest + api)
docker compose up -d --build

# 3. feed it data — replay recorded squats as N wearable devices
uv run python simulator/simulate.py --devices 5
#    knobs: --loss 5 --reorder 5 --jitter 20 --drift 200 --rate 640 --seed 1
#    --dead-sensors 0:1     simulate a failed sensor (biomech degradation ladder)
#    --target HOST:PORT     UDP destination        (default 127.0.0.1:5010,
#                           the local port workaround — see Gotchas; VPS uses 5005)
#    --base-id N            device_id of the first device, then N+1, N+2, …
#                           (default 30; use it to add devices without colliding
#                           with a run already streaming)
#    --duration SECONDS     stop after N seconds   (default: run until Ctrl-C)

# 4. watch it live
Start-Process http://localhost:8000/debug
```

Every device panel shows the composite + m1..m5 charts at 60Hz, quality %,
online badge, active flags and per-sensor input rates. All six metrics are
**0–100** ([docs/biomech/SPEC.md](docs/biomech/SPEC.md)).
`GET localhost:8000/api/health` is the first place to look when anything
misbehaves; it also carries the per-device `biomech` diagnostics block.

Expect **`m4` (control) and `m5` (balance) to be blank at first** — they need
60 s and 30 s of *movement* respectively before they emit, and they go blank
again whenever a leg loses a sensor. Blank means "no data", never zero. Both
carry an `unvalidated` flag: they have no real-data validation yet (SPEC §11.1).

Tests and the stage-1 validation matrix:

```powershell
docker compose --profile debug up -d                # exposes redis on 127.0.0.1:6379
uv run pytest backend/tests/                        # unit + integration tests
uv run pytest backend/tests/test_ws.py              # WS throughput test on its own
uv run python scripts/validate_stage1.py            # full matrix (--quick for a smoke run)
uv run python scripts/calibrate_capture.py CAP.bin  # anchor biomech constants to a REAL capture
#    --segments segs.txt   one `start_s end_s label` per line, to split by movement
#    --wire                22-byte UDP capture instead of the 21-byte SD log
```

Start the debug profile **before** pytest: `test_ws.py` needs Redis reachable on
`127.0.0.1:6379` and *skips* without it, so running the suite first quietly
leaves the WS throughput and tick-schema checks unrun.

The validation matrix takes **~17–20 minutes** in full mode (~4 min with
`--quick`) and is not read-only: scenario 5 deliberately **stops/starts the
`api` container and restarts `redis`** to prove ingest survives both. Don't run
it against anything you are using at the time.

### Real wearables on the LAN

Point the devices at your dev machine's LAN IP, UDP port 5005. Windows
Defender Firewall must allow inbound UDP 5005 (elevated prompt):

```powershell
netsh advfirewall firewall add rule name="MVPDash UDP 5005" dir=in action=allow protocol=UDP localport=5005
```

Find your LAN IP with `ipconfig` (Wi-Fi/Ethernet IPv4 address). Devices
auto-register on their first packet and appear on `/debug` within seconds.

### Gotchas

- **Never run the simulator against the production VPS with default IDs.** Its
  default `--base-id 30` collides with the real wearable fleet (the real device
  is 30), which mixes simulated rows into a real athlete's history — and
  `metrics` rows carry no marker to separate them again. For any test against
  prod: use `--base-id 100` (IDs ≥ 100 are never real hardware), always pass a
  bounded `--duration`, and delete the test devices' registry/metric rows
  afterwards. This bit us on 2026-08-02 (devices 30-32 from the S2-T10 WAN
  check; history wiped as cleanup).
- **UDP stops arriving after `docker compose up -d` recreates the ingest
  container** (Docker Desktop's UDP port proxy can go stale): run
  `docker compose restart ingest` once and traffic flows again.
- **Docker Desktop can wedge a UDP port permanently.** On this dev machine both
  5005 and 5010 reached a state where the port shows as bound
  (`0.0.0.0:5010->5010/udp`) but nothing reaches the container — verified by
  firing 21,600 simulator packets at it and receiving zero. It survives engine
  restarts and `wsl --shutdown`. Symptoms are indistinguishable from "the device
  isn't sending".

  The reliable workaround for a real-device session is to run **ingest natively
  on the host**, which removes Docker's UDP proxy from the path entirely. Leave
  everything else in Docker; it uses the same Redis via the `debug` profile:

  ```powershell
  docker compose stop ingest
  docker compose --profile debug up -d          # exposes redis on 127.0.0.1
  cd backend
  $env:REDIS_URL = 'redis://127.0.0.1:6379/0'
  uv run python -m ingest.main
  ```

  Don't rebuild the ingest container mid-session — that is what wedges the port.
- The api service is bound to `127.0.0.1:8000` on purpose (stage 1 is
  local-only); nothing except the UDP port is reachable from the LAN.

## Configuration

Everything is wired from a single root `.env` (copy [.env.example](.env.example),
which documents every key). No other config location exists on purpose.
