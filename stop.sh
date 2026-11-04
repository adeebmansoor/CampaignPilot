#!/usr/bin/env sh
# CampaignPilot - stop everything (data is kept for next time).
cd "$(dirname "$0")" && docker compose down --remove-orphans && echo "CampaignPilot stopped."
