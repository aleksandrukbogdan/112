FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir vosk==0.3.45 piper-tts==1.3.0 \
      fastapi==0.115.6 "uvicorn[standard]==0.34.0" python-multipart==0.0.20

COPY models/vosk /models/vosk
COPY models/piper /models/piper

COPY app.py .
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8090/health || exit 1
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8090"]
