# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend-builder
WORKDIR /build

COPY frontend-user/package*.json ./frontend-user/
RUN cd frontend-user && npm ci
COPY frontend-user ./frontend-user
RUN cd frontend-user && npm run build

COPY frontend-admin/package*.json ./frontend-admin/
RUN cd frontend-admin && npm ci
COPY frontend-admin ./frontend-admin
RUN cd frontend-admin && npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN useradd --create-home --uid 10001 appuser
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY --from=frontend-builder /build/frontend-user/dist ./static/user
COPY --from=frontend-builder /build/frontend-admin/dist ./static/admin
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

EXPOSE 10000
CMD ["sh", "-c", "python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
