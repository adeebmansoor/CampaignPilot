#!/usr/bin/env sh
# CampaignPilot - one-command start for Mac/Linux:  ./start.sh
set -e
cd "$(dirname "$0")"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop, wait for 'Engine running', then run ./start.sh again."
  exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Add your Gemini key after GOOGLE_API_KEY=, then run ./start.sh again."
  exit 1
fi

echo "Starting CampaignPilot (the first start takes a few minutes)..."
docker compose up -d --build --remove-orphans

printf "Waiting for the app"
i=0
until curl -fs http://localhost:8000/health >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 90 ]; then echo; echo "Still not ready. Check: docker compose logs app"; exit 1; fi
  printf "."; sleep 2
done
echo
echo "CampaignPilot is running at http://localhost:8000"
(command -v open >/dev/null && open http://localhost:8000) || (command -v xdg-open >/dev/null && xdg-open http://localhost:8000) || true
echo "Stop it with ./stop.sh"
