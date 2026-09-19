#!/usr/bin/env bash
# generate_and_stage.sh - pulls fresh sources and renders a new broadcast to
# a STAGING file (output/broadcast-staging.mp4), WITHOUT touching the live
# file the Cine News channel is currently playing from. Use promote_news.sh
# to swap it in when you're ready to go live (immediately, or on a delay -
# see cron_setup.md for the 6:00 / 12:00 / 18:00 edition schedule). Cron
# starts this render ahead of airtime; promote_news.sh performs the cutover.
set -euo pipefail
cd /home/jnicolas/cinevault-genchannel
VP=/home/jnicolas/barker-suite/.venv/bin/python
SEED=$(date +%s)
STAGING=broadcast-staging.mp4

echo "[$(date -Is)] gathering live sources (two weather checks, unique sports, stocks, traffic, news and entertainment)..."
$VP broadcast_generator_v3.py --minutes 35 --seed "$SEED"

echo "[$(date -Is)] rendering to staging ($STAGING)..."
$VP render_broadcast_v3.py --out-name "$STAGING"

echo "[$(date -Is)] staged: output/$STAGING"
