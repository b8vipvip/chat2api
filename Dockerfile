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
    pip install -r requirements.txt

COPY app ./app
COPY scripts/bootstrap_linux_worker.sh ./scripts/bootstrap_linux_worker.sh

# Worker hosts no longer clone GitHub. Ship the exact worker payload in the
# center-server image and expose it as a verified tarball from /bootstrap/.
COPY chrome_extension ./worker_payload/chrome_extension
COPY scripts/linux_worker_agent.py scripts/linux_worker_proxy.py scripts/linux_worker_remote_login.py scripts/linux_worker_tab_init.py ./worker_payload/scripts/
COPY scripts/linux_worker_watchdog.sh scripts/linux_extension_autoreload.sh scripts/linux_worker_proxy_apply.sh scripts/linux_worker_chrome_launcher.sh scripts/linux_worker_diagnostics.sh ./worker_payload/scripts/
RUN chmod 755 /app/scripts/bootstrap_linux_worker.sh \
    && mkdir -p /app/data /app/bootstrap \
    && python - <<'PY'
import hashlib
import tarfile
from pathlib import Path

root = Path('/app/worker_payload')
out = Path('/app/bootstrap/linux-worker-bundle.tar.gz')
with tarfile.open(out, 'w:gz', compresslevel=6) as tar:
    for path in sorted(root.rglob('*')):
        if path.is_file():
            info = tar.gettarinfo(str(path), arcname=str(path.relative_to(root)))
            info.mtime = 0
            with path.open('rb') as handle:
                tar.addfile(info, handle)
digest = hashlib.sha256(out.read_bytes()).hexdigest()
Path('/app/bootstrap/linux-worker-bundle.sha256').write_text(digest + '\n', encoding='utf-8')
PY

EXPOSE 8765
CMD ["sh", "-c", "uvicorn app.entry:app --host ${CHAT2API_HOST:-0.0.0.0} --port ${CHAT2API_PORT:-8765}"]