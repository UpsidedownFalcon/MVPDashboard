# Deploying to the VPS

Step-by-step for the operator. Everything here is what `deploy/deploy.sh` and
`deploy/provision.sh` actually do, plus the checks that prove a release landed.
Written 2026-09-24 for the sleeve-storage release (agent-docs/02_PLAN_msd_management.md); the
"every release" part applies to any later one.

## How production is laid out

- One Ubuntu 24.04 VPS (stage 2, S2-T09) running the whole stack with Docker
  Compose: `redis`, `ingest`, `api`, `db` (TimescaleDB), `caddy`. All services
  are `restart: unless-stopped`, so a reboot brings them back.
- Caddy terminates TLS for `DOMAIN` (Let's Encrypt) on 443, serves the built
  frontend and proxies `/api` and `/ws` to `api`, which binds `127.0.0.1` only.
  Port 80 also serves the plain-http site, which is how you reach the box by raw
  IP when ACME is broken. `db` and `redis` publish no host ports.
- Wearables send UDP to `<VPS_IP>:UDP_PORT` (5005). `ufw` allows 22, 80, 443 and
  5005/udp; remember that Docker-published ports bypass ufw, so what protects the
  stack is the port layout above, not `ufw status`.
- The repo is checked out at `~/MVPDashboard` as the app user `mvpdash`, on
  `main`. A deploy is `git pull` + `docker compose up -d --build` on the box:
  images are built there, not pushed. Database data lives in the `db_data`
  volume and survives rebuilds.
- Every api start runs the SQL migrations (`migrations/*.sql`, recorded in
  `schema_migrations`) and re-seeds the `SEED_USERS` accounts. There is no
  automatic backup (a nightly `pg_dump` is still a backlog item).

## First time only: a new box

Skip this if the VPS already runs the stack.

1. DNS: an **A record, DNS-only** (grey cloud on Cloudflare) `dash.<domain>` to
   the VPS IP. A proxied (orange-cloud) record breaks two things: ACME on port 80
   and, since this release, the address the dashboard hands to sleeves (see
   `UDP_PUBLIC_IP` below).
2. Provision, once, from the dev box over SSH. This installs Docker via
   `get.docker.com`, creates `mvpdash`, **resets ufw** to the four rules above and
   **disables SSH password login**; make sure your key is in root's
   `authorized_keys` first, it is copied to `mvpdash`:

   ```bash
   ssh root@<VPS_IP> 'bash -s' < deploy/provision.sh
   ```

3. Clone the repo as the app user and create the production `.env` from the
   example. **Never copy the developer `.env`**: it carries local workarounds.

   ```bash
   ssh mvpdash@<VPS_IP>
   git clone https://github.com/UpsidedownFalcon/MVPDashboard.git ~/MVPDashboard
   cd ~/MVPDashboard && cp .env.example .env && nano .env
   ```

   Checklist for `.env` (from S2-T10, plus this release):

   | Key | Value | Why |
   |---|---|---|
   | `DOMAIN` | `dash.<domain>` | Caddy's certificate; the example value would fail ACME. |
   | `UDP_PORT` | `5005` | Must equal the ufw rule and every device's `udp_port`. |
   | `UDP_PUBLIC_IP` | blank with a DNS-only A record; the VPS IP if `DOMAIN` is proxied | What the Sleeve storage page's "Point at this dashboard" writes into `CONFIG.TXT`. Blank means the api resolves `DOMAIN` itself. Must be a dotted-quad IPv4 or the api refuses to start. |
   | `EXPECTED_INPUT_HZ` | `640` | Omitting it depresses `quality` about 6 % and trips an insight. |
   | `POSTGRES_PASSWORD`, `JWT_SECRET` | `openssl rand -hex 32` each | Both ship as placeholders; the api refuses to start without a `JWT_SECRET`. |
   | `SEED_USERS` | real `user:pass` pairs | The only way into the dashboard; re-seeded on every start. |

4. Start on a **fresh `db_data` volume** (never restore the dev database: metric
   rows carry no model version and would be mixed irrecoverably):

   ```bash
   docker compose up -d --build && docker compose ps
   docker compose logs -f caddy      # watch it obtain the certificate, then Ctrl+C
   ```

## Every release

### 1. On the dev box

1. Gates green: `cd frontend; npm run build; npm test` and
   `uv run pytest backend/tests/` with the debug DB up.
2. Commit, merge into `main`, push. The VPS tracks `main`; confirm with
   `ssh mvpdash@<VPS_IP> 'cd ~/MVPDashboard && git branch --show-current'`.
3. If the release adds `.env` keys, add them to the production `.env` before
   deploying. This lists keys present in the example but missing on the box:

   ```bash
   ssh mvpdash@<VPS_IP> 'cd ~/MVPDashboard && git fetch -q && comm -13 \
     <(grep -oE "^[A-Z_]+=" .env | sort) \
     <(git show origin/main:.env.example | grep -oE "^[A-Z_]+=" | sort)'
   ```

   Every key has a code default, so a missing key never stops the api; it only
   means the default applies. For the sleeve-storage release the one new key is
   `UDP_PUBLIC_IP` (see the table above).
4. If the release contains a migration that changes data, take a dump first.
   This release does (`006_sleeve_side_backfill.sql`, see below):

   ```bash
   ssh mvpdash@<VPS_IP> 'cd ~/MVPDashboard && docker compose exec -T db sh -c \
     "pg_dump -U \$POSTGRES_USER \$POSTGRES_DB" | gzip > ~/backup-$(date +%F).sql.gz'
   ```

### 2. Deploy

From Git Bash on the dev box:

```bash
VPS=mvpdash@<VPS_IP> bash deploy/deploy.sh
```

That is exactly `ssh $VPS 'cd ~/MVPDashboard && git pull && docker compose up -d
--build && docker compose ps'`. The frontend and backend images are rebuilt on
the box (a few minutes on a small VPS); `api` and `caddy` restart, so the
dashboard is unavailable for a moment and open WebSocket clients reconnect on
their own. Migrations run as `api` starts. `ingest` restarts too; devices keep
sending and nothing is lost beyond the seconds it is down.

### 3. Prove it landed

```bash
ssh mvpdash@<VPS_IP>
cd ~/MVPDashboard
docker compose ps                                   # api "healthy", caddy/ingest/db/redis "Up"
docker compose logs --tail 60 api | grep -iE "migrat|seed|error"
#   expect the new migration file(s) named as applied, "seeded N user(s)", no errors
curl -s https://dash.<domain>/api/health/live        # {"status":"ok"}
docker compose logs --tail 20 ingest                # packets and rigs, if devices are on
docker image prune -f && df -h                      # old images pile up on a 40 GB disk
```

Then in a browser: sign in, open `/api/health` (per-sensor `rate_hz` is the
real ingest check, `docker compose ps` says nothing about packets), and open
**Sleeve storage** in Chrome over `https://dash.<domain>`: the "Open sleeve
drive" button must be enabled (it is disabled on a non-https address or in
other browsers). The UDP target the page offers is what
`GET /api/config/udp-target` returns; check it from a shell with the login
cookie:

```bash
curl -s -c cj.txt -H "Content-Type: application/json" \
  -d '{"username":"<user>","password":"<pass>"}' https://dash.<domain>/api/auth/login
curl -s -b cj.txt https://dash.<domain>/api/config/udp-target
#   {"ip":"<VPS_IP>","port":5005,"source":"env"} or "dns"; "unresolved" with ip null
#   means DOMAIN did not resolve inside the container: set UDP_PUBLIC_IP.
```

### 4. Release notes for the sleeve-storage deploy (2026-09-24)

- **Migration 006 changes data.** Sleeves registered before this release with no
  leg set (`side IS NULL`, unpaired) get the leg their wire `source_id` implies:
  0 left, 1 right. Paired units and explicitly set legs are untouched. Record the
  state before and after:

  ```bash
  docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "SELECT unit_id, rig_id, wire_source_id, side FROM sleeve_units ORDER BY unit_id;"'
  ```

  It is reversible per unit through the dashboard (Device page, the leg control)
  or `PATCH /api/units/<unit_id>` with `{"side": null}`. Within 60 s of the
  change the api mirrors it to ingest and that sleeve's biomech session resets
  once, as any leg change does.
- **New sleeves now register with a leg**, so "side not set" only appears when an
  operator clears it.
- **Sleeves in the field** need no config change: `udp_ip`/`udp_port` on their
  cards already point at this box. From now on users can change WiFi and the
  target from the dashboard instead of Notepad; remind them to eject, then
  unplug, and to power-cycle after a WiFi change.
- No Redis key, tick or packet format changed; `.env` gains only `UDP_PUBLIC_IP`.

## Rollback

Prefer a revert that keeps the box on `main`:

```bash
git revert <release-merge-sha> && git push       # on the dev box
VPS=mvpdash@<VPS_IP> bash deploy/deploy.sh
```

For an emergency without a revert, pin the box to the previous commit and
rebuild (it will say "detached HEAD"; a later `deploy.sh` needs
`git checkout main` first):

```bash
ssh mvpdash@<VPS_IP> 'cd ~/MVPDashboard && git checkout <previous-sha> && docker compose up -d --build'
```

Applied migrations stay applied; they are recorded in `schema_migrations` and
never re-run. Migration 006 is data-only and harmless under the previous code
(it always accepted a set or a NULL side). If a sleeve's leg must go back to
"not set", clear it as described above.

## Rules that keep production safe

- `provision.sh` resets the firewall and disables password SSH. `deploy.sh`
  rebuilds production with no confirmation. Run either only on purpose.
- Never copy the developer `.env` to the box; never restore the dev database.
- Never run the simulator against production with default ids: use
  `--base-id 100` or higher and a bounded `--duration`, then delete the test
  rows (README, Gotchas).
- Take a `pg_dump` before any release whose migration touches data. There is
  no automatic backup.
