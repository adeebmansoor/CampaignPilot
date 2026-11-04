FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY campaignpilot ./campaignpilot
COPY ui ./ui
COPY .streamlit ./.streamlit
COPY data/knowledge ./data/knowledge

# run as a non-root user
RUN useradd --create-home app && mkdir -p /app/data && chown -R app /app
USER app

EXPOSE 8000
CMD ["uvicorn", "campaignpilot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
