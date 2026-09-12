#!/usr/bin/env bash
# Deploy Persist to Fly.io. Run after `flyctl auth login`.
#
# Secrets are read from .env and pushed with `fly secrets`, never baked into the
# image and never committed - the repo is public and .env is gitignored.
#
# The deployed instance keeps MAIL_REDIRECT_TO set. A public URL that anyone can
# file through, with autonomous sending enabled and no redirect, is how a
# fabricated complaint reaches a real Director. Unset it only when a real
# grievance from a real person is ready.
set -euo pipefail
cd "$(dirname "$0")/.."

APP="${FLY_APP:-persist-grievance}"

get() { grep -E "^$1=" .env | cut -d= -f2- | tr -d '\r'; }

echo "==> app: $APP"
flyctl status --app "$APP" >/dev/null 2>&1 || {
  echo "==> creating app"
  flyctl apps create "$APP" --machines
}

echo "==> volume (1GB, created once)"
flyctl volumes list --app "$APP" 2>/dev/null | grep -q persist_data || \
  flyctl volumes create persist_data --size 1 --region bom --app "$APP" --yes

echo "==> secrets"
flyctl secrets set --app "$APP" --stage \
  GROQ_API_KEY="$(get GROQ_API_KEY)" \
  ANAKIN_API_KEY="$(get ANAKIN_API_KEY)" \
  SMTP_HOST="$(get SMTP_HOST)" \
  SMTP_PORT="$(get SMTP_PORT)" \
  SMTP_USER="$(get SMTP_USER)" \
  SMTP_PASSWORD="$(get SMTP_PASSWORD)" \
  SMTP_FROM="$(get SMTP_FROM)" \
  MAIL_REDIRECT_TO="$(get MAIL_REDIRECT_TO)" \
  CONSOLE_PASSWORD="$(get CONSOLE_PASSWORD)" \
  LLM_PROVIDER=groq \
  DRY_RUN=false \
  AUTO_APPROVE_THROUGH_RUNG=2 \
  TIME_SCALE=1.0 \
  TICK_SECONDS=300 \
  PUBLIC_BASE_URL="https://$APP.fly.dev"

echo "==> deploy"
flyctl deploy --app "$APP" --ha=false

echo
echo "live at https://$APP.fly.dev"
flyctl status --app "$APP" | head -20
