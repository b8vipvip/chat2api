from __future__ import annotations

import asyncio
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
GITHUB_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
VERSION_RE = re.compile(r"^v[0-9A-Za-z._-]+$")
SHA_RE = re.compile(r"SHA2-256=([0-9A-Fa-f]{64})")
_LOCK = threading.Lock()


def _download(url: str, target: Path, timeout: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "chat2api-linux-worker-bootstrap"})
    with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)


def _validated_cached(meta_path: Path, *, require_fresh: bool) -> dict[str, Any] | None:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        zip_path = meta_path.parent / str(meta.get("filename") or "")
        expected = str(meta.get("sha256") or "").lower()
        if not zip_path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
            return None
        if require_fresh and time.time() - meta_path.stat().st_mtime >= CACHE_TTL_SECONDS:
            return None
        if hashlib.sha256(zip_path.read_bytes()).hexdigest() != expected:
            return None
        return {**meta, "path": zip_path, "stale": not require_fresh and time.time() - meta_path.stat().st_mtime >= CACHE_TTL_SECONDS}
    except Exception:
        return None


def _ensure_cache(data_dir: Path) -> dict[str, Any]:
    cache_dir = Path(data_dir) / "bootstrap-cache" / "xray"
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_path = cache_dir / "latest.json"

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
            _download(GITHUB_API, api_tmp, 30)
            release = json.loads(api_tmp.read_text(encoding="utf-8"))
            version = str(release.get("tag_name") or "")
            if not VERSION_RE.fullmatch(version):
                raise RuntimeError("Invalid Xray release version")
            base = f"https://github.com/XTLS/Xray-core/releases/download/{version}"
            _download(f"{base}/Xray-linux-64.zip", zip_tmp, 180)
            _download(f"{base}/Xray-linux-64.zip.dgst", dgst_tmp, 30)
            match = SHA_RE.search(dgst_tmp.read_text(encoding="utf-8", errors="replace"))
            if not match:
                raise RuntimeError("Could not read official Xray SHA-256 digest")
            expected = match.group(1).lower()
            actual = hashlib.sha256(zip_tmp.read_bytes()).hexdigest()
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


def install_linux_worker_xray_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_xray_patch_installed", False):
        return app
    app.state.linux_worker_xray_patch_installed = True

    async def cached() -> dict[str, Any]:
        try:
            return await asyncio.to_thread(_ensure_cache, app.state.settings.data_dir)
        except Exception as exc:
            raise HTTPException(503, f"Center server could not prepare Xray package: {str(exc)[:180]}") from exc

    @app.get("/bootstrap/xray/latest.json", include_in_schema=False)
    async def xray_manifest() -> dict[str, Any]:
        meta = await cached()
        return {
            "object": "chat2api.xray-cache",
            "version": meta["version"],
            "sha256": meta["sha256"],
            "size": meta["size"],
            "stale": bool(meta.get("stale")),
        }

    @app.get("/bootstrap/xray/latest.zip", include_in_schema=False)
    async def xray_download() -> FileResponse:
        meta = await cached()
        return FileResponse(
            meta["path"],
            media_type="application/zip",
            filename="Xray-linux-64.zip",
            headers={"Cache-Control": "public, max-age=21600", "X-Chat2API-SHA256": meta["sha256"]},
        )

    return app
