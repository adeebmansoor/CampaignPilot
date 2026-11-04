#!/bin/sh
# Single-container entry point (Docker image default and Hugging Face Spaces):
# load the warehouse + index the knowledge base once, then serve the app.
python -m campaignpilot.pipeline.run || echo "WARNING: pipeline failed; the app will start but data may be missing" >&2
exec uvicorn campaignpilot.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
