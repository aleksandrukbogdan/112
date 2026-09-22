# GigaAM v3 требует torch>=2.6. Образ с CUDA 12.4 — драйвер NVIDIA >= 525 (рекомендуется 550+).
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Веса скачиваются ОДИН раз при сборке и остаются в образе.
# Источник — официальный CDN SberDevices; контрольная сумма из исходников GigaAM.
ARG GIGAAM_MODEL=v3_e2e_rnnt
ARG GIGAAM_MD5=2730de7545ac43ad256485a462b0a27a
ARG CDN=https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM
RUN mkdir -p /models/gigaam \
 && curl -fL --retry 5 -o /models/gigaam/${GIGAAM_MODEL}.ckpt "$CDN/${GIGAAM_MODEL}.ckpt" \
 && curl -fL --retry 5 -o /models/gigaam/${GIGAAM_MODEL}_tokenizer.model "$CDN/${GIGAAM_MODEL}_tokenizer.model" \
 && echo "${GIGAAM_MD5}  /models/gigaam/${GIGAAM_MODEL}.ckpt" | md5sum -c -

# Проверка при сборке: модель действительно загружается (на CPU — GPU при сборке нет).
# Ошибка всплывёт сейчас, а не при первом запуске у пользователя.
RUN python -c "import gigaam; gigaam.load_model('${GIGAAM_MODEL}', device='cpu', \
    download_root='/models/gigaam', fp16_encoder=False); print('GigaAM: загрузка проверена')"

COPY app.py .
ENV GIGAAM_MODEL=${GIGAAM_MODEL} GIGAAM_DIR=/models/gigaam
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS http://localhost:8091/health | grep -q '"ok":true' || exit 1
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8091"]
