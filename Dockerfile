FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    LLM_PROVIDER=gemini TOOLS_MODE=mcp QDRANT_URL=local PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY campaignpilot ./campaignpilot
COPY scripts ./scripts
# Knowledge docs live outside /app/data so a shared data volume starts empty
# (avoids a first-start race when several containers mount it at once).
COPY data/knowledge ./knowledge
ENV KNOWLEDGE_DIR=/app/knowledge

# Non-root user with uid 1000 (required by Hugging Face Spaces).
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8000'))"

# Default: everything in one container (SQLite + local Qdrant), used by Hugging Face Spaces.
# docker-compose.yml runs Postgres and Qdrant as separate services instead.
CMD ["sh", "scripts/start-app.sh"]
