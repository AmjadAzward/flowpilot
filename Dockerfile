# FlowPilot in one container: the API serves the built interface on port 8000.
#   docker compose up -d --build        then open http://127.0.0.1:8000
# Data (database, encryption key, files) lives in the /data volume. Back up the whole volume.

FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    FLOWPILOT_SERVE_UI=1 \
    FLOWPILOT_DB=/data/flowpilot.db \
    FLOWPILOT_KEY_FILE=/data/flowpilot.key \
    FLOWPILOT_FILES_DIR=/data/files \
    FLOWPILOT_SQLITE_DIR=/data
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/rotate_key.py backend/reset_password.py ./
COPY --from=ui /ui/dist /app/frontend/dist
RUN useradd --create-home --uid 10001 flowpilot && mkdir -p /data && chown flowpilot /data
USER flowpilot
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
