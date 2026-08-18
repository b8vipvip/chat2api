# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_CACHE_DIR=/root/.cache/pip

# Keep dependency installation in its own layer so normal application-code
# changes do not invalidate the expensive Python dependency step.
COPY requirements.txt ./
RUN --mount=type=cache,id=chat2api-pip,target=/root/.cache/pip,sharing=locked \
    pip install --prefer-binary -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data

EXPOSE 8765
CMD ["sh", "-c", "uvicorn app.entry:app --host ${CHAT2API_HOST:-0.0.0.0} --port ${CHAT2API_PORT:-8765}"]
