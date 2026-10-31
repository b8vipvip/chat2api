"""Opt-in native rich presentation sidecar for non-Voice requests.

Neither this store nor its SSE subscribers participate in Broker completion.
The authoritative OpenAI reply remains the existing archived Markdown.
No HTML, CSS text, media URLs, file bytes, tokens or JS are accepted.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

MAX_BYTES = 1024 * 1024
MAX_NODES = 3200
MAX_DEPTH = 24
MAX_TEXT = 192000
MAX_ACTIVE = 128
MAX_AGE_SECONDS = 90 * 86400
SNAPSHOT_FILENAME = re.compile(r"(?:req|resp|imgreq)_[a-zA-Z0-9_-]{6,100}\.json")
SAFE_TAGS = {"div", "p", "span", "strong", "em", "h1", "h2", "h3", "h4",
             "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead",
             "tbody", "tr", "td", "th", "figure", "figcaption", "img", "hr", "br"}
SAFE_STYLES = {
    "display": {"block", "inline", "inline-block", "flex", "inline-flex", "grid"},
    "flexDirection": {"row", "row-reverse", "column", "column-reverse"},
    "flexWrap": {"wrap", "nowrap"},
    "justifyContent": {"start", "center", "end", "space-between", "space-around"},
    "alignItems": {"start", "center", "end", "stretch", "baseline"},
    "objectFit": {"contain", "cover", "fill", "none"},
    "textAlign": {"left", "center", "right", "justify", "start", "end"},
    "fontWeight": {"400", "500", "600", "700"},
}
LENGTH_STYLES = {"gap", "padding", "marginTop", "marginBottom",
                 "width", "maxWidth", "height", "borderRadius", "fontSize", "lineHeight"}
COLOR_STYLES = {"color", "backgroundColor"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")
RGB_COLOR = re.compile(r"^rgba?\([0-9.,\s]+\)$")
LENGTH = re.compile(r"^(?:0|(?:\d{1,4}(?:\.\d{1,2})?)(?:px|rem|%))$")
GRID_COLUMNS = re.compile(r"^(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)(?:\s+(?:\d{1,4}(?:\.\d{1,2})?(?:px|%)|minmax\(\d{1,4}(?:\.\d{1,2})?px,\s*1fr\)|1fr)){0,5}$")


def validate_native_snapshot(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Native snapshot must be an object")
    if set(payload) != {"version", "revision", "root"} or payload["version"] != 1:
        raise ValueError("Unsupported native snapshot shape")
    revision = payload["revision"]
    if type(revision) is not int or not 1 <= revision <= 1000000:
        raise ValueError("Invalid revision")
    count, chars = 0, 0

    def node(value: object, depth: int) -> dict:
        nonlocal count, chars
        if depth > MAX_DEPTH or count >= MAX_NODES or not isinstance(value, dict):
            raise ValueError("Native tree exceeds limit")
        count += 1
        tag = value.get("tag")
        if tag not in SAFE_TAGS or not isinstance(tag, str):
            raise ValueError("Unsupported native element")
        if set(value) - {"tag", "text", "children", "style", "media_index"}:
            raise ValueError("Native element has unsafe attributes")
        result = {"tag": tag}
        if tag == "img":
            if set(value) - {"tag", "media_index", "style"}:
                raise ValueError("Image attributes must be asset references")
            idx = value.get("media_index")
            if type(idx) is not int or idx < 0 or idx >= 40:
                raise ValueError("Invalid media index")
            result["media_index"] = idx
        elif "media_index" in value:
            raise ValueError("Image reference on non-image element")
        if "text" in value:
            content = value["text"]
            if tag == "img" or not isinstance(content, str):
                raise ValueError("Invalid text")
            chars += len(content)
            if chars > MAX_TEXT:
                raise ValueError("Native content too large")
            result["text"] = content
        if "style" in value:
            raw = value["style"]
            if not isinstance(raw, dict) or len(raw) > 18:
                raise ValueError("Unsafe CSS")
            styles = {}
            for key, item in raw.items():
                if not isinstance(key, str) or not isinstance(item, str):
                    raise ValueError("Unsafe CSS value")
                if key in SAFE_STYLES and item in SAFE_STYLES[key]:
                    styles[key] = item
                elif key in LENGTH_STYLES and LENGTH.fullmatch(item) and len(item) <= 16:
                    styles[key] = item
                elif key == "gridTemplateColumns" and len(item) <= 120 and GRID_COLUMNS.fullmatch(item):
                    styles[key] = item
                elif key in COLOR_STYLES and len(item) <= 48 and (
                        HEX_COLOR.fullmatch(item) or RGB_COLOR.fullmatch(item)):
                    styles[key] = item
                else:
                    raise ValueError("Unsupported CSS property")
            if styles:
                result["style"] = styles
        if "children" in value:
            children = value["children"]
            if tag in {"img", "hr", "br"} or not isinstance(children, list) or len(children) > 80:
                raise ValueError("Invalid children")
            result["children"] = [node(child, depth + 1) for child in children]
        return result

    root = node(payload["root"], 0)
    output = {"version": 1, "revision": revision, "root": root}
    if len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Native snapshot byte cap")
    return output


class NativeContentStore:
    """Bounded latest-revision store; persistence is independent of request terminal."""
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "native_content"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self._persist_count = 0
        self._lock = threading.Lock()
        self._live: OrderedDict[str, tuple[str, dict, float]] = OrderedDict()

    @staticmethod
    def _valid_id(request_id: str) -> str:
        if not re.fullmatch(r"(?:req|resp|imgreq)_[a-zA-Z0-9_-]{6,100}", request_id):
            raise ValueError("Invalid request identifier")
        return request_id

    def publish(self, request_id: str, owner: str, payload: object) -> bool:
        request_id = self._valid_id(request_id)
        if not owner:
            return False
        snapshot = validate_native_snapshot(payload)
        with self._lock:
            previous = self._live.get(request_id)
            if previous and previous[0] != owner:
                return False
            if previous and previous[1]["revision"] >= snapshot["revision"]:
                return False
            self._live[request_id] = (owner, snapshot, time.time())
            self._live.move_to_end(request_id)
            while len(self._live) > MAX_ACTIVE:
                self._live.popitem(last=False)
        return True

    def get(self, request_id: str, owner: str) -> dict | None:
        self._valid_id(request_id)
        with self._lock:
            record = self._live.get(request_id)
            if record and time.time() - record[2] >= MAX_AGE_SECONDS:
                self._live.pop(request_id, None)
                record = None
            if record and record[0] == owner:
                return record[1]
        path = self.root / (request_id + ".json")
        try:
            if time.time() - path.stat().st_mtime >= MAX_AGE_SECONDS:
                path.unlink(missing_ok=True)
                return None
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("owner") == owner:
                return validate_native_snapshot(saved["snapshot"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def persist(self, request_id: str, owner: str) -> bool:
        """Save only a final, bounded snapshot. Never influence Broker status."""
        snapshot = self.get(request_id, owner)
        if not snapshot:
            return False
        path = self.root / (self._valid_id(request_id) + ".json")
        temp = path.with_suffix(".tmp")
        data = json.dumps({"owner": owner, "snapshot": snapshot}, ensure_ascii=False)
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
            os.replace(temp, path)
            self._persist_count += 1
            # Bounded on-disk cache, independent of request_history.jsonl and
            # existing MediaArchive retention. Max 1000 snapshots / 90 days.
            if self._persist_count % 32 == 0:
                saved = sorted(
                    (p for p in self.root.glob("*.json") if SNAPSHOT_FILENAME.fullmatch(p.name)),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                cutoff = time.time() - MAX_AGE_SECONDS
                for index, candidate in enumerate(saved):
                    if index >= 256 or candidate.stat().st_mtime < cutoff:
                        candidate.unlink(missing_ok=True)
            return True
        except OSError:
            return False


def native_delivery_fingerprint(snapshot: dict | None) -> tuple | None:
    """An asset becoming ready is a new SSE state even at the same DOM revision."""
    if not snapshot:
        return None
    # Files may become available after the last DOM update, exactly as
    # image archives do. The SSE must publish this same-revision change.
    files = tuple(
        (item.get("url"), item.get("status"))
        for item in (snapshot.get("files") or []) if isinstance(item, dict)
    )
    return snapshot["revision"], tuple(snapshot.get("assets") or ()), files


def native_layout_summary(snapshot: dict, archived_assets: int) -> dict:
    """Structure-only request diagnostics; no user text, URLs, or raw HTML."""
    pending = [snapshot.get("root")]
    counts = {"nodes": 0, "images": 0, "grid": 0, "flex": 0,
              "tables": 0, "archived_assets": archived_assets}
    while pending and counts["nodes"] < MAX_NODES:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        counts["nodes"] += 1
        tag = node.get("tag")
        if tag == "img":
            counts["images"] += 1
        if tag == "table":
            counts["tables"] += 1
        mode = (node.get("style") or {}).get("display")
        if mode == "grid":
            counts["grid"] += 1
        elif mode in {"flex", "inline-flex"}:
            counts["flex"] += 1
        pending.extend(node.get("children") or ())
    counts["media_mapping_complete"] = counts["images"] == archived_assets
    counts["revision"] = snapshot.get("revision")
    counts["layout_schema"] = snapshot.get("version")
    return counts
