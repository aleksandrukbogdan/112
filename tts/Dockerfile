FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-docker.txt /tmp/requirements-docker.txt
RUN pip install --no-cache-dir -r /tmp/requirements-docker.txt \
    && pip install --no-cache-dir --no-deps f5-tts==1.1.22

COPY f5_engine.py sound_manager.py scene.py tts_service.py ref_fit.py effect_gap.py __init__.py voices_f5.json /app/tts/

EXPOSE 8092
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
  CMD curl -fsS http://localhost:8092/health || exit 1
CMD ["python", "-m", "uvicorn", "tts.tts_service:app", "--host", "0.0.0.0", "--port", "8092"]
