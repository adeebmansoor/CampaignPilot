@echo off
rem CampaignPilot - stop everything (your data is kept for next time).
cd /d "%~dp0"
docker compose down --remove-orphans
echo CampaignPilot stopped.
timeout /t 3 >nul
