# Deploying the dashboard to a VPS 

- [A. First-time setup of a brand-new server](#a-first-time-setup-of-a-brand-new-server)
- [B. Deploying a new version to a server that already runs the dashboard](#b-deploying-a-new-version)
- [C. Rolling back to the previous version](#c-rolling-back)
- [D. Pointing a domain at the dashboard with Cloudflare](#d-pointing-a-domain-at-the-dashboard-cloudflare)

Everything here is what `deploy/deploy.sh` and `deploy/provision.sh` actually do, written
out one command at a time.

## Before you start

### The two places you will type

| Where | How you get there | Prompt looks like |
|---|---|---|
| **Your PC, in Git Bash** | On Windows, right-click inside your clone of this repo in Explorer and choose "Open Git Bash here", or open Git Bash and type `cd /c/Users/<you>/GitHub/MVPDashboard`. | `you@PC MINGW64 ~/GitHub/MVPDashboard (main)` |
| **The VPS, over SSH** | From Git Bash, type `ssh mvpdash@<VPS_IP>` and press Enter. You are now typing on the server. Type `exit` to come back. | `mvpdash@<hostname>:~$` |

Every command block below starts with a line saying which one to use. Type each line,
press Enter, and read what comes back before typing the next line.

### Words to replace

Wherever you see these, put in your own values:

| Placeholder | Meaning | Example |
|---|---|---|
| `<VPS_IP>` | The public IPv4 address of the server | `203.0.113.10` |
| `<domain>` | The domain you own in Cloudflare | `example.com` |
| `dash.<domain>` | The hostname the dashboard lives at | `dash.example.com` |
| `<user>` / `<pass>` | A login from `SEED_USERS` in the server's `.env` | `trainer` / (secret) |

### The three port numbers, so they never get mixed up

| Port | What it is | Where it is set |
|---|---|---|
| **5005/udp** | The port the dashboard **listens** on for wearable data. It is the same on your PC and on the VPS. | `UDP_PORT` in `.env` (default 5005 in `.env.example` and in the code) |
| 5050 | The port a **factory-fresh knee sleeve tries to send to**. It is wrong for this dashboard and must be changed to 5005 on every sleeve, either on the Sleeve storage page ("Point at this dashboard") or in the sleeve's `CONFIG.TXT`. | Sleeve firmware default |
| 5010 | The **simulator's** default target, left over from a Docker Desktop workaround on one dev machine. Always pass `--target` explicitly. | `simulator/simulate.py` |

Also: 22/tcp is SSH, 80/tcp and 443/tcp are the website. The server's firewall allows
exactly those four (22, 80, 443, 5005/udp).

### What the server looks like when it is running

- One Ubuntu 24.04 server. Docker Compose runs five containers: `redis`, `ingest`,
  `api`, `db` (the database) and `caddy` (the web server). They restart by themselves
  after a reboot.
- The repo is cloned at `/home/mvpdash/MVPDashboard` (written `~/MVPDashboard`) as the
  user `mvpdash`, on the `main` branch. Deploying means: pull the latest `main`,
  rebuild the containers. Nothing is uploaded from your PC.
- The server's `.env` file is **not** in git. It holds the passwords and settings and
  survives every pull. Your PC's `.env` must never be copied to the server.
- The database lives in a Docker volume called `db_data`. Rebuilding containers does
  not touch it.
- Every time the `api` container starts it runs the SQL migrations (new ones only) and
  re-creates the login accounts from `SEED_USERS`.
- There is **no automatic backup**. Section B tells you when to take one by hand.

---

## A. First-time setup of a brand-new server

Skip this whole section if the dashboard already runs on the server.

### A1. Create the server

1. At your hosting provider, create a server with **Ubuntu 24.04**, at least 2 vCPU,
   4 GB RAM and a 40 GB disk.
2. When the provider asks for an **SSH key**, paste your public key. If you do not have
   one yet, make one first:

   **Your PC, in Git Bash**
   ```bash
   ssh-keygen -t ed25519
   ```
   Press Enter at every question. Then show the public key and copy the whole line:
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```
   You should see one line starting with `ssh-ed25519`.
3. Write down the server's public IPv4 address. That is `<VPS_IP>` from now on.
4. Check you can log in as root:

   **Your PC, in Git Bash**
   ```bash
   ssh root@<VPS_IP>
   ```
   The first time it asks `Are you sure you want to continue connecting?`: type `yes`.
   You should see a prompt like `root@ubuntu:~#`. Type `exit` and press Enter to leave.

### A2. Point the domain at the server

Do section D now, then come back here. Do not continue until
`nslookup dash.<domain>` shows `<VPS_IP>` (section D explains the check).

### A3. Prepare the server (run once)

This installs Docker, creates the `mvpdash` user, sets the firewall to allow only
22, 80, 443 and 5005/udp, and turns off SSH password logins. It copies root's SSH key to
`mvpdash`, so you will log in as `mvpdash` from now on with the same key.

**Your PC, in Git Bash, inside the repo folder**
```bash
ssh root@<VPS_IP> 'bash -s' < deploy/provision.sh
```
You should see green `==>` lines (`Base packages`, `Docker`, `Application user`,
`Firewall`, `SSH hardening`, `Unattended upgrades`, `Done`) and a final block starting
with `Provisioned.` It takes a few minutes. It is safe to run again if it stops halfway.

Then check the new user works:
```bash
ssh mvpdash@<VPS_IP>
```
You should see `mvpdash@...:~$`. Stay logged in for the next steps.

### A4. Get the code onto the server

**The VPS, over SSH**
```bash
git clone https://github.com/UpsidedownFalcon/MVPDashboard.git ~/MVPDashboard
cd ~/MVPDashboard
git branch --show-current
```
The last command must print `main`. (If the repository is private and git asks for a
username and password, use a GitHub personal access token as the password.)

### A5. Create the server's settings file

**The VPS, over SSH, inside `~/MVPDashboard`**

1. Start from the example file:
   ```bash
   cp .env.example .env
   ```
2. Make two random secrets. Run this twice and copy each output line somewhere safe:
   ```bash
   openssl rand -hex 32
   ```
3. Open the file in the editor:
   ```bash
   nano .env
   ```
   Use the arrow keys to move. Change these lines (find each with Ctrl+W, then type
   the key name and press Enter):

   | Line to change | Set it to | Why |
   |---|---|---|
   | `DOMAIN=dash.example.com` | `DOMAIN=dash.<domain>` | The web server requests its certificate for this name. |
   | `UDP_PORT=5005` | leave `5005` | Must match the firewall rule and every sleeve's `udp_port`. |
   | `UDP_PUBLIC_IP=` | leave blank if your Cloudflare record is **DNS only** (section D); put `<VPS_IP>` if it is **Proxied** | The address the Sleeve storage page writes into sleeves. Blank means the server looks up `DOMAIN` itself, which only works when the record is DNS only. |
   | `POSTGRES_PASSWORD=changeme` | the first random secret | Database password. |
   | `JWT_SECRET=changeme-...` | the second random secret | Login cookie signing. The api refuses to start with the placeholder. |
   | `SEED_USERS=trainer:changeme` | `SEED_USERS=<user>:<pass>` | The dashboard logins. Several: `a:pw1,b:pw2`. |
   | `EXPECTED_INPUT_HZ=640` | leave `640` | Must be present; the wearable rate. |

   Save and leave nano: press Ctrl+O, press Enter, then Ctrl+X.
4. Check the file has what it needs (values are hidden, only the keys print):
   ```bash
   grep -E '^(DOMAIN|UDP_PORT|UDP_PUBLIC_IP|POSTGRES_PASSWORD|JWT_SECRET|SEED_USERS|EXPECTED_INPUT_HZ)=' .env | cut -d= -f1
   ```
   You should see all seven names, one per line.

### A6. Start everything

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
docker compose up -d --build
```
This builds the containers on the server. The first time takes 5 to 10 minutes. When it
returns, check:
```bash
docker compose ps
```
You should see five lines: `api` with `(healthy)` in its STATUS, and `caddy`, `db`,
`ingest`, `redis` with `Up`. If `api` says `(health: starting)`, wait 30 seconds and
run `docker compose ps` again.

Watch the web server get its certificate:
```bash
docker compose logs caddy | grep -iE "certificate obtained|error" | tail -5
```
You want a line containing `certificate obtained successfully` for `dash.<domain>`.
If you see errors mentioning `acme` or `challenge`, the DNS record is not pointing at
this server yet (section D) or port 80 is not reachable. Press Ctrl+C if the command
seems stuck.

Now open `https://dash.<domain>` in a browser on your PC and sign in with `<user>` and
`<pass>`. Type `exit` in the SSH window when you are done.

Your sleeves must now be told to send to `<VPS_IP>` port `5005`: plug each one into a
PC running Chrome, open `https://dash.<domain>`, go to **Sleeve storage** and click
**Point at this dashboard** (README, "Sleeve storage (USB)").

---

## B. Deploying a new version

Use this whenever `main` on GitHub has commits the server does not have yet.

### B1. On your PC: make sure the version is on GitHub `main`

**Your PC, in Git Bash, inside the repo folder**

1. Check what branch you are on and that nothing is left uncommitted:
   ```bash
   git status
   ```
   You want `On branch main` and `nothing to commit, working tree clean`. If you are on
   a feature branch, merge it into `main` first (a pull request on GitHub is fine).
2. Push:
   ```bash
   git push origin main
   ```
3. Note the version you are about to deploy, so you can compare it on the server later:
   ```bash
   git log -1 --oneline
   ```
   Write down the 7-character code at the start of the line.

### B2. On the server: see what is there now

**Your PC, in Git Bash**
```bash
ssh mvpdash@<VPS_IP>
```
**The VPS, over SSH**
```bash
cd ~/MVPDashboard
git branch --show-current
git status --short
git log -1 --oneline
```
- The first command must print `main`. If it prints something else (for example
  nothing, after a rollback), type `git checkout main` and press Enter.
- The second command should print nothing. If it lists files, someone edited files on
  the server; do not continue until you know why (`git diff` shows the changes;
  `git checkout -- <file>` throws them away).
- The third line is the version currently deployed. Compare it with the code you wrote
  down in B1; they should differ, otherwise there is nothing to deploy.

### B3. On the server: take a database backup

Do this every time. It takes seconds and it is the only backup that exists.

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > ~/backup-$(date +%F-%H%M).sql.gz
ls -lh ~/backup-*.sql.gz | tail -3
```
You should see your new file with a size bigger than a few kilobytes.

### B4. On the server: fetch the new version

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
git pull
git log -1 --oneline
```
`git pull` prints the files that changed and ends with a line like
`N files changed`. The second command must now print the same 7-character code you
wrote down in B1. If `git pull` says `Already up to date.`, the server had it already.

### B5. On the server: check for new settings

New versions sometimes add keys to `.env.example`. Every key has a built-in default,
so a missing key never stops the server, but you should know about them.

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
diff <(grep -oE '^[A-Z_]+=' .env | sort) <(grep -oE '^[A-Z_]+=' .env.example | sort)
```
- No output: nothing new, go to B6.
- Lines starting with `>`: keys that exist in the example but not in your `.env`. For
  each one, read its comment in `.env.example` (`grep -B3 '^KEYNAME=' .env.example`)
  and decide. To keep the default, do nothing. To set it, open `nano .env`, add the
  line at the end, save with Ctrl+O, Enter, Ctrl+X.
- Lines starting with `<`: keys only in your `.env`. Fine, ignore them.

Two keys deserve a look after any pull:

| Key | Set it when |
|---|---|
| `UDP_PUBLIC_IP` | Your Cloudflare record is **Proxied** (orange cloud). Then it must be `<VPS_IP>`. With a DNS-only record leave it blank. |
| `UDP_PORT` | Never change it without also changing the firewall and every sleeve. Confirm it still says `5005`: `grep '^UDP_PORT=' .env`. |

### B6. On the server: does this version contain a migration that changes data?

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
git diff --name-only HEAD@{1} HEAD -- backend/migrations
```
- No output: no new migration, go to B7.
- One or more `.sql` files: open each one (`cat backend/migrations/<file>`) and read
  the comment at the top. A file that only creates tables or columns is routine. A file
  that contains `UPDATE` or `DELETE` changes existing rows; you already have the backup
  from B3, and the comment says what it changes and how to undo it per row.

### B7. On the server: rebuild and restart

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
docker compose up -d --build
```
This rebuilds the containers whose code changed and restarts them. Expect 2 to 6
minutes. The dashboard is unreachable for a few seconds at the end; open browser tabs
reconnect on their own; wearables keep sending and lose only those seconds.

(`deploy/deploy.sh`, run from your PC as `VPS=mvpdash@<VPS_IP> bash deploy/deploy.sh`,
does B4 and B7 in one go. Use it once you are comfortable; it skips B5 and B6.)

### B8. On the server: prove it worked

**The VPS, over SSH, inside `~/MVPDashboard`**

1. Containers:
   ```bash
   docker compose ps
   ```
   `api` must show `(healthy)`; `caddy`, `db`, `ingest`, `redis` must show `Up`.
   If `api` is `(health: starting)`, wait 30 seconds and try again. If it keeps
   restarting, read `docker compose logs --tail 50 api`; the last lines say why.
2. Migrations and logins:
   ```bash
   docker compose logs api | grep -E "applying|already applied|migrations complete|seeded"
   ```
   You should see one `applying <file>.sql` line per new migration (or `skip ... already
   applied` for old ones), then `migrations complete (N applied)` and
   `seeded N user(s)`.
3. The website answers:
   ```bash
   curl -s https://dash.<domain>/api/health/live
   ```
   You should see exactly `{"status":"ok"}`.
4. Data is arriving (only meaningful while at least one wearable is switched on):
   ```bash
   curl -s -c cj.txt -H "Content-Type: application/json" -d '{"username":"<user>","password":"<pass>"}' https://dash.<domain>/api/auth/login
   curl -s -b cj.txt https://dash.<domain>/api/health
   rm cj.txt
   ```
   The second reply is a long JSON. Look for `"rate_hz"` values around 640 per
   sensor; `0` or missing means no packets for that sensor.
5. The Sleeve storage page knows where sleeves should send:
   ```bash
   curl -s -c cj.txt -H "Content-Type: application/json" -d '{"username":"<user>","password":"<pass>"}' https://dash.<domain>/api/auth/login
   curl -s -b cj.txt https://dash.<domain>/api/config/udp-target
   rm cj.txt
   ```
   You should see `{"ip":"<VPS_IP>","port":5005,"source":"dns"}` (or `"env"` if you set
   `UDP_PUBLIC_IP`). If `"ip"` is `null`, the server could not look up `DOMAIN`: set
   `UDP_PUBLIC_IP=<VPS_IP>` in `.env`, then `docker compose up -d api`.
6. Tidy up old container images so the disk does not fill:
   ```bash
   docker image prune -f
   df -h /
   ```
   `Use%` for `/` should stay well under 80%.
7. Type `exit` to leave the server.

Finally, on your PC, open `https://dash.<domain>` in Chrome, sign in, and click through
Unit overview, a soldier's page and **Sleeve storage** (its "Open sleeve drive" button
must be enabled; it is only ever enabled on an https address in Chrome or Edge).

---

## C. Rolling back

Use this when a new version misbehaves and you want the previous one back quickly.

### C1. Find the previous version

**Your PC, in Git Bash**
```bash
ssh mvpdash@<VPS_IP>
```
**The VPS, over SSH**
```bash
cd ~/MVPDashboard
git log --oneline -5
```
The top line is what runs now. Pick the line below it (or the version you know was
good) and copy its 7-character code; call it `<GOOD>`.

### C2. Switch the server to it and rebuild

**The VPS, over SSH, inside `~/MVPDashboard`**
```bash
git checkout <GOOD>
docker compose up -d --build
docker compose ps
```
git prints a note about `detached HEAD`; that is expected. When `docker compose ps`
shows `api (healthy)`, the old version is running. Do the checks in B8 (steps 1, 3, 4).

### C3. Two things a rollback does not undo

- **Migrations stay applied.** The database keeps any new tables or columns and any
  rows a migration changed. Old code ignores columns it does not know, so this is
  normally harmless. If a migration changed rows you need back, the B3 backup is
  the way: `gunzip -c ~/backup-<date>.sql.gz` shows the SQL; ask before restoring
  wholesale, because that also throws away the metrics recorded since.
- **The server is now off `main`.** Before the next normal deploy, put it back:
  ```bash
  git checkout main
  ```
  Then follow section B. The fix for whatever went wrong belongs in a new commit on
  `main`, so the same bad version is not deployed again by accident.

---

## D. Pointing a domain at the dashboard (Cloudflare)

Do this before the first deploy (A2) or whenever the server's IP changes. The
dashboard's own certificate comes from Let's Encrypt through Caddy, so Cloudflare only
has to answer the DNS question "what is the IP of dash.<domain>".

1. Log in at https://dash.cloudflare.com and click your domain `<domain>`.
2. In the left menu open **DNS**, then **Records**.
3. Click **Add record** and fill in:

   | Field | Value |
   |---|---|
   | Type | `A` |
   | Name | `dash` (Cloudflare shows the full name `dash.<domain>` next to it) |
   | IPv4 address | `<VPS_IP>` |
   | Proxy status | **DNS only** (click the cloud so it turns grey; orange means Proxied) |
   | TTL | Auto |

4. Click **Save**.

Why DNS only: with the orange (Proxied) cloud, traffic goes through Cloudflare's
servers. Then Let's Encrypt's check on port 80 may fail, the raw-IP fallback stops
matching, and the dashboard would hand sleeves Cloudflare's address instead of the
server's. If you have a reason to proxy, you must also set `UDP_PUBLIC_IP=<VPS_IP>` in
the server's `.env` (section A5 or B5).

5. Check it took effect. New records usually answer within a minute or two.

   **Your PC, in Git Bash (or PowerShell)**
   ```bash
   nslookup dash.<domain>
   ```
   The `Address:` line under the `Name:` line must be `<VPS_IP>`. If it shows an
   address that is not your server, the record is still Proxied or has not
   propagated yet; wait and try again.

6. If the domain itself is not on Cloudflare yet: in Cloudflare choose **Add a site**,
   enter `<domain>`, pick the free plan, and change the nameservers at the place you
   bought the domain to the two Cloudflare gives you. That part can take up to a day;
   the A record above can be created straight away and starts working once the
   nameservers have switched.

---

## Rules that keep production safe

- `deploy/provision.sh` resets the firewall and turns off SSH password login.
  `deploy/deploy.sh` rebuilds production without asking. Run either only on purpose.
- Never copy your PC's `.env` to the server, and never load the local development
  database into it.
- Never run the simulator against the server with default ids: use `--base-id 100` or
  higher and a bounded `--duration`, then delete the test rows (README, Gotchas).
- Take the B3 backup before every deploy. Nothing else backs the database up.
