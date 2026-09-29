# Phase 2: Management on a separate PC. Build context is ../../python (see compose.yaml).
FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "fastapi>=0.115" "uvicorn>=0.30"
COPY management ./management
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "management"]
