FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BEDROCK_REGION=eu-north-1 SQL_AGENT_DB=/app/data/sample.db PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sql_agent ./sql_agent
# Build the sample database into the image; mount or replace it for real data.
RUN mkdir -p /app/data && python -m sql_agent --db /app/data/sample.db init-db \
    && useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "uvicorn sql_agent.web:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
