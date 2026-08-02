#!/usr/bin/env bash
# Deploy (S2-T08/T10): push to GitHub first, then run this from the dev box.
# Usage: VPS=user@host ./deploy/deploy.sh
set -euo pipefail

: "${VPS:?set VPS=user@host}"

ssh "$VPS" 'cd ~/MVPDashboard && git pull && docker compose up -d --build && docker compose ps'
