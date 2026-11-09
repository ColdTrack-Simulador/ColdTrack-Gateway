FROM node:24-bookworm-slim AS frontend
ARG COLDTRACK_FRONTEND_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /frontend
RUN test -n "$COLDTRACK_FRONTEND_REF" \
    && git init \
    && git fetch --depth=1 https://github.com/abcdfer/ColdTrack-Gateway_FRONT.git "$COLDTRACK_FRONTEND_REF" \
    && git checkout --detach FETCH_HEAD \
    && npm ci \
    && npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COLDTRACK_FRONTEND_DIR=/app/web
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apy.py database.py ./
COPY routes ./routes
COPY services ./services
COPY --from=frontend /frontend/dist ./web
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 2 apy:app"]
