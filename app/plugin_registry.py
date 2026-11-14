"""Isolated, non-executing product plugin manifest registry (C-05a).

This is NOT the legacy browser Worker Tools catalog. There are no outbound
calls, arbitrary code execution, secret storage or plugin invocations here.
All manifests are unverified metadata; execute_tool always denies access.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]{1,32})?$")
ALLOWED_KEYS = frozenset({"id", "name", "description", "publisher", "version", "tools"})
ALLOWED_TOOL_KEYS = frozenset({"name", "description", "input_schema", "output_schema"})
FORBIDDEN_SCHEMA_KEYS = frozenset({"$ref", "$id", "$schema", "default", "examples", "contentEncoding", "contentMediaType"})


class PluginRegistryError(ValueError):
    pass


def _validate_schema(node: Any, depth: int = 0) -> None:
    if depth > 12:
        raise PluginRegistryError("Schema exceeds allowed nesting depth")
    if isinstance(node, dict):
        if FORBIDDEN_SCHEMA_KEYS.intersection(node):
            raise PluginRegistryError("Unsafe or external schema feature")
        for value in node.values():
            _validate_schema(value, depth + 1)
    elif isinstance(node, list):
        if len(node) > 128:
            raise PluginRegistryError("Schema array too large")
        for value in node:
            _validate_schema(value, depth + 1)
    elif node is None or isinstance(node, (str, int, float, bool)):
        return
    else:
        raise PluginRegistryError("Schema contains unsupported data")


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != ALLOWED_KEYS:
        raise PluginRegistryError("Manifest must contain exactly the approved metadata fields")
    pid = value["id"]
    version = value["version"]
    if not isinstance(pid, str) or not PLUGIN_ID.fullmatch(pid):
        raise PluginRegistryError("Invalid plugin ID")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise PluginRegistryError("Invalid plugin version")
    for key, maxlen in (("name", 120), ("description", 1000), ("publisher", 120)):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > maxlen:
            raise PluginRegistryError(f"Invalid manifest {key}")
    tools = value["tools"]
    if not isinstance(tools, list) or not 1 <= len(tools) <= 20:
        raise PluginRegistryError("Manifest must define between 1 and 20 tools")
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != ALLOWED_TOOL_KEYS:
            raise PluginRegistryError("Tool manifest has invalid fields")
        name = tool["name"]
        if not isinstance(name, str) or not TOOL_NAME.fullmatch(name) or name in seen:
            raise PluginRegistryError("Invalid or duplicate tool name")
        seen.add(name)
        if not isinstance(tool["description"], str) or len(tool["description"]) > 500:
            raise PluginRegistryError("Invalid tool description")
        for field in ("input_schema", "output_schema"):
            schema = tool[field]
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise PluginRegistryError("Tool schema root must be a JSON object schema")
            _validate_schema(schema)
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(canonical.encode("utf-8")) > 32 * 1024:
        raise PluginRegistryError("Manifest exceeds 32 KiB limit")
    return json.loads(canonical)


class PluginRegistry:
    """SQLite transactional manifest catalogue, with no execution authority."""

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "plugin_catalog_v1.sqlite3"
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise PluginRegistryError("Plugin catalog cannot be a symbolic link")
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS plugin_manifests (
              plugin_id TEXT NOT NULL,
              version TEXT NOT NULL,
              manifest_json TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (plugin_id, version)
            )
        """)
        self.db.commit()
        os.chmod(self.path, 0o600)

    def add(self, raw: dict[str, Any]) -> dict[str, Any]:
        manifest = validate_manifest(raw)
        canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        created = datetime.now(timezone.utc).isoformat()
        with self.lock:
            try:
                with self.db:
                    self.db.execute(
                        "INSERT INTO plugin_manifests(plugin_id, version, manifest_json, sha256, created_at) VALUES (?, ?, ?, ?, ?)",
                        (manifest["id"], manifest["version"], canonical, digest, created),
                    )
            except sqlite3.IntegrityError as exc:
                raise PluginRegistryError("Plugin version already exists (immutable manifest)") from exc
        return {
            "id": manifest["id"], "version": manifest["version"],
            "sha256": digest, "trusted": False, "execution_enabled": False,
            "created_at": created,
        }

    def list_public_metadata(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT plugin_id, version, sha256, created_at FROM plugin_manifests ORDER BY plugin_id, version"
            ).fetchall()
        return [
            {"id": pid, "version": version, "sha256": digest, "created_at": created,
             "trusted": False, "execution_enabled": False}
            for pid, version, digest, created in rows
        ]

    def get(self, plugin_id: str, version: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT manifest_json FROM plugin_manifests WHERE plugin_id = ? AND version = ?",
                (plugin_id, version),
            ).fetchone()
        if row is None:
            return None
        return {
            "manifest": json.loads(row[0]),
            "trusted": False, "execution_enabled": False,
        }

    def execute_tool(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("Product plugin execution is disabled until trust, sandbox and secret-store gates pass")

    def close(self) -> None:
        self.db.close()
