FROM python:3.12-slim
WORKDIR /app
# fonts-dejavu — кириллица в PDF-отчётах и сертификатах
RUN apt-get update && apt-get install -y --no-install-recommends curl fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ ./api/
COPY web/ ./web/
COPY data/ ./data/
ENV DATA_DIR=/app/data DB_PATH=/app/state/t112.db BACKUP_DIR=/app/state/backup PYTHONUNBUFFERED=1
VOLUME /app/state
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["uvicorn","api.main:app","--host","0.0.0.0","--port","8080","--workers","1"]
