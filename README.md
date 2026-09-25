# MVP Dashboard — Injury-Risk Prediction

A trainer-facing web dashboard that predicts when trainees are approaching injury.
1–5 wearable rigs — a bilateral unit, a single knee sleeve or two sleeves paired
into one soldier — stream raw IMU data over UDP to an ingest service; a Python
biomechanical pipeline converts it into constant 60Hz metric streams (5 primitives +
1 composite risk index per device); the dashboard shows live charts, historical
rolling windows, regression-based forecasts of the composite, and rules-based
insights.

**Status: all three stages are built and deployed** (2026-08-03) — local real-time
biomech, public VPS deployment with intelligence, and the designed product frontend
with login. Live at `https://<your-domain>`; the only stage-3 item still open is the
S3-T08 acceptance run. Since then: the demo frontend and military theme
(2026-09-12, [docs/tasks/STAGE4.md](docs/tasks/STAGE4.md)) and **unilateral
knee-sleeve support** (2026-09-23, [PLAN_unilateral_devices.md](agent-docs/01_PLAN_unilateral_devices.md)) —
a second wearable kind on the same UDP port, with dashboard-driven pairing, side
and per-sleeve IMU full scale (see “Unilateral knee sleeves” below); and
**2026-09-23: sleeve storage** (USB drive management —
[PLAN_msd_management.md](agent-docs/02_PLAN_msd_management.md)): the dashboard edits a plugged-in
sleeve's `CONFIG.TXT` and transfers its logs with verified copies (see "Sleeve
storage (USB)" below).

**Start here: read [docs/PLAN.md](docs/PLAN.md) first.** It anchors the full doc
suite (TRD, backend schema, app flow, implementation plan, and per-stage task lists).

Working on this repo with an AI coding agent? Start at
[agent-docs/README.md](agent-docs/README.md): the project context and every plan of
record live there, numbered in the order the work was done. The root `AGENTS.md` and
`CLAUDE.md` are pointers that load it automatically.

## Quickstart (full local stack)

Prereqs: Docker Desktop (WSL2 backend) and [uv](https://docs.astral.sh/uv/)
(`irm https://astral.sh/uv/install.ps1 | iex`). Then, from the repo root:

```powershell
# 1. one-time setup
if (-not (Test-Path .env)) { Copy-Item .env.example .env }   # never clobber an existing .env
uv sync --dev                        # creates .venv with Python 3.12 + deps

# 2. start the full stack (redis + ingest + api + db + caddy)
#    NOTE: caddy binds host :80 and :443 — free them first if something else uses them.
#    .env MUST carry a JWT_SECRET: the api refuses to start without one.
docker compose up -d --build

# 3. feed it data — replay recorded squats as N wearable devices
uv run python simulator/simulate.py --devices 5
#    knobs: --loss 5 --reorder 5 --jitter 20 --drift 200 --rate 640 --seed 1
#    --dead-sensors 0:1     simulate a failed sensor (biomech degradation ladder)
#    --soc 35 --soc-drain 2 battery: start at 35%, drain 2%/min (source 1 drains
#                           1.5x faster, so the UI's "lowest of the two MCUs" shows)
#    --target HOST:PORT     UDP destination        (default 127.0.0.1:5010,
#                           the local port workaround — see Gotchas; VPS uses 5005)
#    --base-id N            device_id of the first device, then N+1, N+2, …
#                           (default 30; use it to add devices without colliding
#                           with a run already streaming)
#    --duration SECONDS     stop after N seconds   (default: run until Ctrl-C)
#    --sleeves N            also emit N unilateral knee sleeves (sync 0xA6) —
#                           see "Unilateral knee sleeves" below

# 4. watch it live — the product dashboard, served by caddy
Start-Process http://localhost
#    Sign in with an account from SEED_USERS in .env (default: trainer / changeme).
#    Accounts are seeded by the api entrypoint on every start.
```

**Everything is behind the login cookie from stage 3 on** — `/debug` and
`/api/health` included. Only `POST /api/auth/login`, `POST /api/auth/logout` and
`GET /api/health/live` are open, so a cold `curl` to `/api/health` returns 401,
not data. To poke the API from a shell, log in first and keep the cookie:

```powershell
curl -s -c cj.txt -H "Content-Type: application/json" `
     -d '{"username":"trainer","password":"changeme"}' http://localhost/api/auth/login
curl -s -b cj.txt http://localhost/api/health      # now 200
```

Every device panel shows the composite + m1..m5 charts at 60Hz, an online badge,
active flags and a one-line sensor summary; the quality % and the per-sensor
input rates sit behind that summary's toggle on the detail page (STAGE4 R1,
2026-09-12). `m1..m4` and the composite are **0–100**; **`m5` is signed,
−100..+100** (+ = left-dominant, − = right)
([docs/biomech/SPEC.md](docs/biomech/SPEC.md)).
`GET /api/health` (cookie required) is the first place to look when anything
misbehaves; it also carries the per-device `biomech` diagnostics block.
`GET /api/health/live` needs no cookie and is the liveness probe.

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
auto-register on their first packet and appear on `/debug` within seconds. For
a knee sleeve, the step-by-step loop is "Local end-to-end test with one sleeve"
below.

### Unilateral knee sleeves

A second wearable kind streams into the **same** UDP port (2026-09-23): the NY
Knicks knee sleeve (firmware `NYKnicksDataLogger`). One sleeve is **one MCU on
one leg** with two IMUs, and it marks its datagrams with sync byte **0xA6**
instead of the bilateral unit's 0xA5. Sensor 1 is the **thigh**, sensor 2 the
**shin**, on every source. Downstream, one sleeve is a **unit** named
`u<device_id>-<source_id>` (e.g. `u31-0`), and a **rig** is what the dashboard
shows as one soldier: a bilateral unit, a single sleeve, or two sleeves paired.

**On the sleeve — `CONFIG.TXT` on its SD card.** Since 2026-09-23 the dashboard
edits it for you: plug the sleeve in and open **Sleeve storage** (see "Sleeve
storage (USB)" below). Any text editor still works over USB. Either way, **eject
the drive, then unplug the cable**: the sleeve re-reads the file about 2 s after
unplugging and starts a fresh session (a WiFi change also needs a power cycle).

```
# the machine running ingest
udp_ip=192.168.1.100
# MUST equal UDP_PORT in .env; the firmware default is 5050
udp_port=5005
device_id=31
# 0 = left leg, 1 = right leg: the dashboard seeds the sleeve's leg from this
source_id=0
# 2, 4, 8, 16 or 32
accel_fs_g=32
# 125, 250, 500, 1000, 2000 or 4000
gyro_fs_dps=4000
```

Comments must be **whole lines**: the firmware does not strip a trailing `# ...`
from a value, so an inline comment on `udp_ip` silently disables streaming, and a
leading zero makes a number octal. The Sleeve storage page writes neither.

`(device_id, source_id)` is a sleeve's whole identity on the wire, so **give
every sleeve in a session its own pair**: two sleeves sharing both values are
indistinguishable and their samples merge into one unit. Sleeve ids never
collide with bilateral ones — the `u` prefix namespaces them.

**In the dashboard** — the sleeve controls sit in the device-detail header and
appear for sleeve rigs only:

1. **Check the leg.** Since 2026-09-23 a new sleeve arrives with the leg its
   own `source_id` says (0 = left, 1 = right — set on the card or in Sleeve
   storage), and its summary reads `2 sensors | one leg | 6400Hz logging`.
   Change it here if the sleeve is worn on the other leg, or clear it (then
   `side not set`). The dashboard never guesses beyond what the sleeve itself
   declares.
2. **Set the full scale** if it differs from the firmware defaults
   (`+-32 g | +-4000 dps`). The datagram carries **no** scale, so this is
   configuration the dashboard owns; a wrong value scales every metric on that
   sleeve. Defaults for newly seen sleeves come from `UNILATERAL_ACCEL_FS_G` /
   `UNILATERAL_GYRO_FS_DPS` in `.env`.
3. **Pair two sleeves** into one soldier with four sensors and a working L/R
   balance: press `Pair with...` on the sleeve that should keep its name and
   history, pick the other sleeve, and give each one a leg. The joiner's own
   card disappears for as long as the pairing lasts and comes back, with its
   history intact, on `Unpair`.

Any of those three changes **hard-resets that soldier's biomech session** (dose,
baselines, calibration): the rig is a different shape, or its counts mean
something different, so the old numbers are not comparable.

A rig that instruments one leg carries the `one leg` flag: `m1`..`m4` run
normally and only `m5` (L/R balance) is blank, with "one leg" as its reason. It
is a shape, not a fault, and it is deliberately never reported as
`sensors missing`.

**Simulate sleeves without hardware.** Pass `--target` explicitly — the
simulator's default is the 5010 dev-box workaround (see Gotchas), not `UDP_PORT`:

```powershell
uv run python simulator/simulate.py --sleeves 2 --target 127.0.0.1:5005 --duration 600
#    --sleeve-base-id N      device_id of the first sleeve, then N+1, …
#                            (default: --base-id + --devices, so sleeves never
#                            collide with a bilateral run in the same command)
#    --sleeve-source-id 0|1  the source_id every sleeve transmits on (default 0)
#    --sleeve-accel-fs G     2, 4, 8, 16, 32          (default 32)
#    --sleeve-gyro-fs DPS    125 … 4000               (default 4000)
```

The capture is ±16 g / ±2000 dps, so its counts are multiplied once at load by
`16/accel_fs` and `2000/gyro_fs`: a sleeve represents the **same motion** as a
bilateral device, at its own full scale. `--loss`, `--jitter`, `--soc`,
`--dead-sensors` and the rest apply to sleeves too (`--dead-sensors 0:2` kills a
sleeve's shin).

Note `--devices` still defaults to **1**, so the command above streams a mixed
fleet: bilateral `30` plus sleeves `u31-0` and `u32-0`. Pass `--devices 0` for
sleeves only.

Check it arrived: `GET /api/health` → `ingest.global:recv:unilateral` should be
counting and `global:bad_sync` should stay flat.

### Sleeve storage (USB)

Since 2026-09-23 the dashboard manages a sleeve's SD card directly
([PLAN_msd_management.md](agent-docs/02_PLAN_msd_management.md); UI spec in
[docs/UIUX.md](docs/UIUX.md) §15). Plug the sleeve into the PC running the
browser, open **Sleeve storage** (sidebar, under Command; route `/storage`),
press `Open sleeve drive` and pick the **HIPPOSDATA** drive itself (the page
checks that it holds `CONFIG.TXT`). The page then:

- **edits `CONFIG.TXT`** exactly as the firmware parses it: a basic view (WiFi
  network and password, sleeve number, Left/Right leg, diagnostics log,
  streaming), a read-only "Streams to ip:port (this dashboard / not this
  dashboard)" line with a one-click `Point at this dashboard`, and a
  warning-gated Advanced view (`udp_ip`/`udp_port`, low-battery stop, IMU full
  scales, WiFi transmit power, battery calibration). Only the values you
  changed are rewritten, the file is read back and verified, and leading-zero
  numbers (octal to the firmware), duplicate keys and a byte-order mark are
  surfaced and repaired on save;
- **transfers `LOG_NNNN.BIN` / `.TXT`** to a folder you pick once, as
  `<folder>/sleeve-u<dev>-<src>/raw/LOG_NNNN.BIN` (the sleeve identity recorded
  in each file), and **deletes each file from the sleeve only after its copy
  verified** (the local copy is re-read: length, CRC32 and an identical block
  scan; blocks the scan called bad are re-read from the card byte for byte).
  Tick `Keep copies on the sleeve` to skip the delete. A failure, a cancel or
  an unplug mid-copy never deletes and never leaves a partial copy; a file
  already at the destination with the same CRC reads "Already transferred".
  `CONFIG.TXT` can never be listed, transferred or deleted.

Requirements and rules:

- **Chrome or Edge on a secure address** (`https://<your-domain>` or
  `http://localhost`, e.g. `npm run dev`): the page uses the File System Access
  API. Firefox, Safari and a plain-http LAN address show an "unsupported"
  message instead. The drive and destination handles are remembered; after a
  reload press `Reconnect` once (Chrome 122+ offers "Allow on every visit",
  after which it is automatic).
- **Eject, then unplug.** A host eject does not end the sleeve's session; the
  sleeve re-reads `CONFIG.TXT` about 2 s after the cable comes out and starts a
  new session. WiFi network or password changes also need a power cycle. The
  page says so after every save.
- **Speed**: the sleeve's USB link is full-speed, about 1 MB/s, so a 512 MB log
  takes about 9 minutes; the page shows rate and ETA and asks you not to unplug
  while a transfer runs.
- After a save that changed `accel_fs_g` / `gyro_fs_dps`, the dashboard's own
  record for that sleeve (`PATCH /api/units/u<dev>-<src>`) is updated to match
  and that soldier's session resets. Changing the sleeve number or leg makes
  the sleeve appear as a **new** soldier; pairing, leg and history stay with
  the old id.
- Change-set 2 (a CSV and a plain-text summary per transferred log) is planned,
  not built: [PLAN_msd_management.md](agent-docs/02_PLAN_msd_management.md) §5.

**Dry run without a sleeve**: `cd frontend; npm run dev`, then pick any local
folder holding copies of `CONFIG.TXT` and some `LOG_NNNN.{BIN,TXT}` — for
instance `frontend/src/lib/storage/fixtures/config_generated_1_2_0.txt` saved as
`CONFIG.TXT` and `LOG_0010.head64.bin` saved as `LOG_0010.BIN`. Then verify
with a real sleeve:

1. the picker offers the drive root and `Open sleeve drive` accepts it;
2. change a value, save, eject, unplug: the next `LOG_NNNN.TXT` on the card
   shows the new values on its `# cfg:` line;
3. transfer a 512 MB file and time it (about 9 min); re-plug and confirm it is
   gone from the card and present under `sleeve-u<dev>-<src>/raw/`;
4. unplug mid-copy: the card is intact, nothing was deleted, and no partial
   copy remains in the destination.

### Local end-to-end test with one sleeve

The full loop on a dev laptop: configure a sleeve from the dashboard, watch it
stream over the office Wi-Fi, then pull its logs. Written from the 2026-09-23
walk-through. Replace `<laptop-ip>` with the laptop's Wi-Fi IPv4 from `ipconfig`
(DHCP can change it between days) and `<your-wifi>` with the network the laptop
is on; the sleeve joins the same network.

1. **Tell the dashboard its own address.** In `.env`, under `UDP_PORT`, add
   `UDP_PUBLIC_IP=<laptop-ip>`. The page's `Point at this dashboard` button
   fills that address in; without it the api resolves `DOMAIN`, which is not
   your laptop.
2. **Allow inbound UDP 5005** once, with the firewall rule from
   "Real wearables on the LAN" above.
3. **Start the stack, but keep UDP out of Docker's proxy** (see Gotchas: it has
   wedged port 5005 on this machine before). Ignore Caddy's certificate errors
   for `DOMAIN` in the logs; the plain `http://:80` site still serves.

   ```powershell
   cd MVPDashboard
   docker compose up -d --build          # first build takes a few minutes
   docker compose stop ingest
   ```

   In a second window, run ingest natively and leave it open (it reads the
   same `.env`, binds UDP 5005 on the laptop, and uses the Redis the `debug`
   profile exposes):

   ```powershell
   cd MVPDashboard\backend
   $env:REDIS_URL = 'redis://127.0.0.1:6379/0'
   uv run python -m ingest.main
   ```

4. **Open Chrome at `http://localhost`** and sign in with an account from
   `SEED_USERS`. Localhost is a secure context, so the drive picker works.
5. **Plug the sleeve in.** Within a couple of seconds the LED pulses blue and a
   `HIPPOSDATA` drive appears in Explorer.
6. **Configure it from the page.** Sidebar: Command, `Sleeve storage`, then
   `Open sleeve drive`; in the picker choose the HIPPOSDATA drive itself (its
   root, e.g. `E:\`), not a folder inside it; accept "Allow on every visit" if
   offered. Fill in WiFi network `<your-wifi>` and its password, Sleeve number
   `1`, Leg `Left`, both checkboxes on. The UDP row reads
   `Streams to 192.168.1.100:5050 (not this dashboard)` on a factory card; click
   `Point at this dashboard` and it becomes `<laptop-ip>:5005 (this dashboard)`.
   `Save to sleeve`: a `CONFIG.TXT.crswap` shows briefly on the drive, then the
   green "Saved" panel. Notepad shows only those values changed.
7. **Eject, unplug, power-cycle.** Eject in Windows, pull the cable, then switch
   the sleeve off and on (or RESET): WiFi credentials are read at boot only;
   every other key would already apply after the unplug.
8. **Watch the LED**: green pulse is logging without WiFi, blue-green pulse is
   logging and streaming. Expect blue-green within about ten seconds of boot.
9. **See it live.** `Unit overview` shows soldier `u1-0` online within a few
   seconds; its page reads `2 sensors | one leg` with the leg already `Left`.
   The ingest window shows packets; `docker compose logs api` shows
   `registered sleeve unit(s): u1-0`.

   If nothing appears after 30 s, check in this order: the laptop is still on
   `<your-wifi>` with the same IPv4; the network allows client-to-client traffic
   (guest or isolated networks block it); the firewall rule exists; then prove
   the ingest path with a synthetic sleeve from a third window:

   ```powershell
   uv run python simulator/simulate.py --sleeves 1 --target 127.0.0.1:5005 --duration 30
   ```

   A simulated sleeve that shows up while the real one does not means the
   problem is on the network side.
10. **Transfer the logs.** After a few minutes, plug the sleeve back in: logging
    stops, the drive returns, the page reconnects and lists that session's
    `LOG_NNNN.BIN` and `.TXT`. `Choose destination folder` (e.g.
    `C:\Users\<you>\HipposLogs`). First run: tick `Keep copies on the sleeve`,
    `Transfer selected`, watch Copying, Verifying, then `Copied (kept on sleeve)`
    at about 1 MB/s; the files land in `HipposLogs\sleeve-u1-0\raw\` with the
    sizes shown on the drive. Second run with the box unticked: identical files
    read `Already transferred` and are removed from the sleeve; re-plugging
    shows an empty log table.
11. **Two safety checks worth doing once**: pull the cable mid-copy and confirm
    the card still has the file and the destination has no partial copy; and
    change a setting again to confirm the next `LOG_NNNN.TXT` header line
    (`# cfg:`) carries it.
12. **Stop**: Ctrl+C in the ingest window, then `docker compose stop`.

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

## Deploying to the VPS

Production is one Ubuntu VPS running this compose stack behind Caddy; a release
is `git pull` + `docker compose up -d --build` on the box, wrapped by
`deploy/deploy.sh`. [deploy/deploy.md](deploy/deploy.md) walks through it one
command at a time, saying where to type each one: first-time setup of a new
server, deploying a new version (with the backup, the `.env` key check and the
"prove it worked" steps), rolling back, and pointing a domain at the dashboard
with Cloudflare. Read it before running either script: `provision.sh` resets the
firewall and `deploy.sh` rebuilds production without asking.

## Configuration

Everything is wired from a single root `.env` (copy [.env.example](.env.example),
which documents every key). No other config location exists on purpose.

**`UDP_PUBLIC_IP`** (added 2026-09-23, blank by default): the IPv4 the knee
sleeves should stream to, served by `GET /api/config/udp-target` to the Sleeve
storage page's "Streams to ... (this dashboard)" line and its `Point at this
dashboard` button. Blank means the api resolves `DOMAIN` itself (3 s budget,
cached 60 s); set it explicitly when `DOMAIN` is behind a proxy or CDN, because
the resolved address would then not be this box. It must be a dotted-quad IPv4
or the api refuses to start.
