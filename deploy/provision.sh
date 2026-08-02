#!/usr/bin/env bash
# S2-T09 — prepare a fresh Ubuntu 24.04 VPS to run this stack.
#
# Run as root on the new box, once:
#     ssh root@<vps-ip> 'bash -s' < deploy/provision.sh
#
# Idempotent: safe to re-run. Does NOT clone the repo or start the stack — that is
# S2-T10, and it needs a prod .env that only the operator can write.
set -euo pipefail

APP_USER="${APP_USER:-mvpdash}"
UDP_PORT="${UDP_PORT:-5005}"

log() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

log "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git ufw unattended-upgrades

log "Docker"
# get.docker.com, NOT `apt install docker-compose-plugin`: that package name comes
# from Docker's own repository and does not exist in stock Ubuntu 24.04, where the
# equivalent is `docker-compose-v2`. The convenience script installs the engine and
# the compose plugin together and is version-stable across Ubuntu releases.
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
docker --version
docker compose version

log "Application user: ${APP_USER}"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$APP_USER"
fi
usermod -aG docker "$APP_USER"
# carry root's authorized_keys over so the operator can still get in as APP_USER
if [[ -f /root/.ssh/authorized_keys ]]; then
    install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
    install -m 600 -o "$APP_USER" -g "$APP_USER" \
        /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"
fi

log "Firewall"
# NOTE: ufw does NOT filter Docker-published ports — compose inserts them into the
# nat/DOCKER chains, which bypass ufw's INPUT filtering. These rules protect the
# host itself; what protects the stack is that db/redis publish no host ports and
# api binds 127.0.0.1. Verify that with `docker compose ps`, not with `ufw status`.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'ssh'
ufw allow 80/tcp comment 'http/acme'
ufw allow 443/tcp comment 'https'
ufw allow "${UDP_PORT}/udp" comment 'wearable ingest'
ufw --force enable
ufw status verbose

log "SSH hardening"
install -d -m 755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-mvpdash.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin prohibit-password
KbdInteractiveAuthentication no
EOF
sshd -t
systemctl reload ssh || systemctl reload sshd

log "Unattended upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades

log "Done"
cat <<EOF

Provisioned. Next (S2-T10), as ${APP_USER}:
  git clone <repo> ~/MVPDashboard && cd ~/MVPDashboard
  cp .env.example .env    # then work through the S2-T10 step-1 checklist:
                          #   UDP_PORT=${UDP_PORT}, EXPECTED_INPUT_HZ=640,
                          #   real DOMAIN / POSTGRES_PASSWORD / JWT_SECRET / SEED_USERS
  docker compose up -d --build

Do NOT copy the developer's .env — it carries a local UDP_PORT workaround.
Start on a fresh db_data volume; never restore the local dev database.
EOF
