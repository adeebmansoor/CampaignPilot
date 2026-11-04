@echo off
rem CampaignPilot - one-click start for Windows. Double-click this file.
cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running.
  echo Open Docker Desktop, wait until it says "Engine running", then run start.bat again.
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Paste your Gemini key after GOOGLE_API_KEY=, save, close Notepad, then run start.bat again.
  notepad .env
  exit /b 1
)

echo Starting CampaignPilot. The first start takes a few minutes...
docker compose up -d --build --remove-orphans
if errorlevel 1 (
  echo Docker could not start CampaignPilot. Scroll up to see the error.
  pause
  exit /b 1
)

echo Waiting for the app to be ready...
set /a tries=0
:wait
set /a tries+=1
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://localhost:8000/health; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto ready
if %tries% geq 90 goto slow
timeout /t 2 /nobreak >nul
goto wait

:slow
echo The app is taking longer than usual. Check the logs with: docker compose logs app
pause
exit /b 1

:ready
echo CampaignPilot is running at http://localhost:8000
start "" http://localhost:8000
echo To stop it, double-click stop.bat
timeout /t 5 >nul
