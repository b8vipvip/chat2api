from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


CACHE_TTL_SECONDS = 6 * 60 * 60
API_TIMEOUT_SECONDS = 12
ASSET_TIMEOUT_SECONDS = 90
DIGEST_TIMEOUT_SECONDS = 12
REFRESH_RETRY_SECONDS = 30
GITHUB_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
VERSION_RE = re.compile(r"^v[0-9A-Za-z._-]+$")
SHA_RE = re.compile(r"SHA2-256=([0-9A-Fa-f]{64})")
_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE: dict[str, dict[str, Any]] = {}


def _download(url: str, target: Path, timeout: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "chat2api-linux-worker-bootstrap"})
    with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_cached(meta_path: Path, *, require_fresh: bool) -> dict[str, Any] | None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        zip_path = meta_path.parent / str(meta.get("filename") or "")
        expected = str(meta.get("sha256") or "").lower()
        if not zip_path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
            return None
        age = max(0.0, time.time() - meta_path.stat().st_mtime)
        if require_fresh and age >= CACHE_TTL_SECONDS:
            return None
        if _sha256(zip_path) != expected:
            return None
        return {**meta, "path": zip_path, "stale": age >= CACHE_TTL_SECONDS}
    except Exception:
        return None


def _cache_paths(data_dir: Path) -> tuple[Path, Path]:
    cache_dir = Path(data_dir) / "bootstrap-cache" / "xray"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir, cache_dir / "latest.json"


def _ensure_cache(data_dir: Path) -> dict[str, Any]:
    cache_dir, meta_path = _cache_paths(data_dir)

    fresh = _validated_cached(meta_path, require_fresh=True) if meta_path.exists() else None
    if fresh:
        return fresh

    with _LOCK:
        fresh = _validated_cached(meta_path, require_fresh=True) if meta_path.exists() else None
        if fresh:
            return fresh
        stale = _validated_cached(meta_path, require_fresh=False) if meta_path.exists() else None

        api_tmp = cache_dir / ".latest-api.json"
        zip_tmp = cache_dir / ".Xray-linux-64.zip"
        dgst_tmp = cache_dir / ".Xray-linux-64.zip.dgst"
        try:
            _download(GITHUB_API, api_tmp, API_TIMEOUT_SECONDS)
            release = json.loads(api_tmp.read_text(encoding="utf-8"))
            version = str(release.get("tag_name") or "")
            if not VERSION_RE.fullmatch(version):
                raise RuntimeError("Invalid Xray release version")
            base = f"https://github.com/XTLS/Xray-core/releases/download/{version}"
            _download(f"{base}/Xray-linux-64.zip", zip_tmp, ASSET_TIMEOUT_SECONDS)
            _download(f"{base}/Xray-linux-64.zip.dgst", dgst_tmp, DIGEST_TIMEOUT_SECONDS)
            match = SHA_RE.search(dgst_tmp.read_text(encoding="utf-8", errors="replace"))
            if not match:
                raise RuntimeError("Could not read official Xray SHA-256 digest")
            expected = match.group(1).lower()
            actual = _sha256(zip_tmp)
            if actual != expected:
                raise RuntimeError("Xray SHA-256 verification failed")
            filename = f"Xray-linux-64-{version}.zip"
            final = cache_dir / filename
            zip_tmp.replace(final)
            meta = {"version": version, "sha256": actual, "filename": filename, "size": final.stat().st_size}
            temp_meta = cache_dir / ".latest.json.tmp"
            temp_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            temp_meta.replace(meta_path)
            return {**meta, "path": final, "stale": False}
        except Exception:
            if stale:
                return stale
            raise
        finally:
            api_tmp.unlink(missing_ok=True)
            zip_tmp.unlink(missing_ok=True)
            dgst_tmp.unlink(missing_ok=True)


def _refresh_key(data_dir: Path) -> str:
    return str(Path(data_dir).resolve())


def _refresh_worker(data_dir: Path, key: str) -> None:
    try:
        _ensure_cache(data_dir)
    except Exception as exc:
        with _REFRESH_LOCK:
            state = _REFRESH_STATE.setdefault(key, {})
            state.update({
                "running": False,
                "last_error": str(exc)[:240],
                "last_finished_at": time.time(),
            })
        return
    with _REFRESH_LOCK:
        state = _REFRESH_STATE.setdefault(key, {})
        state.update({"running": False, "last_error": "", "last_finished_at": time.time()})


def _start_refresh(data_dir: Path) -> bool:
    key = _refresh_key(data_dir)
    now = time.time()
    with _REFRESH_LOCK:
        state = _REFRESH_STATE.setdefault(key, {"running": False, "last_error": "", "last_finished_at": 0.0})
        if state.get("running"):
            return False
        if state.get("last_error") and now - float(state.get("last_finished_at") or 0.0) < REFRESH_RETRY_SECONDS:
            return False
        state.update({"running": True, "last_error": "", "started_at": now})
    thread = threading.Thread(
        target=_refresh_worker,
        args=(Path(data_dir), key),
        name="chat2api-xray-cache-refresh",
        daemon=True,
    )
    thread.start()
    return True


def _manifest_status(data_dir: Path) -> dict[str, Any]:
    _, meta_path = _cache_paths(data_dir)
    cached = _validated_cached(meta_path, require_fresh=False) if meta_path.exists() else None
    if cached:
        if cached.get("stale"):
            _start_refresh(data_dir)
        return {
            "object": "chat2api.xray-cache",
            "ready": True,
            "state": "ready",
            "version": cached["version"],
            "sha256": cached["sha256"],
            "size": cached["size"],
            "stale": bool(cached.get("stale")),
        }

    _start_refresh(data_dir)
    key = _refresh_key(data_dir)
    with _REFRESH_LOCK:
        state = dict(_REFRESH_STATE.get(key) or {})
    if state.get("last_error") and not state.get("running"):
        return {
            "object": "chat2api.xray-cache",
            "ready": False,
            "state": "error",
            "retry_after_seconds": REFRESH_RETRY_SECONDS,
            "message": str(state.get("last_error") or "Xray cache preparation failed")[:240],
        }
    return {
        "object": "chat2api.xray-cache",
        "ready": False,
        "state": "preparing",
        "retry_after_seconds": 2,
        "message": "Center server is preparing verified Xray cache",
    }


def install_linux_worker_xray_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_xray_patch_installed", False):
        return app
    app.state.linux_worker_xray_patch_installed = True

    @app.get("/bootstrap/xray/latest.json", include_in_schema=False)
    async def xray_manifest() -> dict[str, Any]:
        return _manifest_status(app.state.settings.data_dir)

    @app.get("/bootstrap/xray/latest.zip", include_in_schema=False)
    async def xray_download() -> FileResponse:
        _, meta_path = _cache_paths(app.state.settings.data_dir)
        meta = _validated_cached(meta_path, require_fresh=False) if meta_path.exists() else None
        if not meta:
            _start_refresh(app.state.settings.data_dir)
            raise HTTPException(503, "Verified Xray cache is still preparing")
        if meta.get("stale"):
            _start_refresh(app.state.settings.data_dir)
        return FileResponse(
            meta["path"],
            media_type="application/zip",
            filename="Xray-linux-64.zip",
            headers={"Cache-Control": "public, max-age=21600", "X-Chat2API-SHA256": meta["sha256"]},
        )

    return app
