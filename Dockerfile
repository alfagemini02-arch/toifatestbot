# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend-builder
WORKDIR /build

COPY frontend-user/package*.json ./frontend-user/
RUN --mount=type=cache,target=/root/.npm cd frontend-user \
    && npm config set registry https://registry.npmjs.org/ \
    && sed -i 's#https://packages.applied-caas-gateway1.internal.api.openai.org/artifactory/api/npm/npm-public/#https://registry.npmjs.org/#g' package-lock.json \
    && npm ci
COPY frontend-user ./frontend-user
RUN cd frontend-user && npm run build

COPY frontend-admin/package*.json ./frontend-admin/
RUN --mount=type=cache,target=/root/.npm cd frontend-admin \
    && npm config set registry https://registry.npmjs.org/ \
    && sed -i 's#https://packages.applied-caas-gateway1.internal.api.openai.org/artifactory/api/npm/npm-public/#https://registry.npmjs.org/#g' package-lock.json \
    && npm ci
COPY frontend-admin ./frontend-admin
RUN cd frontend-admin && npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home --uid 10001 appuser
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

COPY backend ./backend
COPY --from=frontend-builder /build/frontend-user/dist ./static/user
COPY --from=frontend-builder /build/frontend-admin/dist ./static/admin
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

EXPOSE 10000
CMD ["sh", "-c", "python -m uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
