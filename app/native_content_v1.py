"""Opt-in native rich presentation sidecar for non-Voice requests.

Neither this store nor its SSE subscribers participate in Broker completion.
The authoritative OpenAI reply remains the existing archived Markdown.
No HTML, CSS text, media URLs, file bytes, tokens or JS are accepted.
"""
from __future__ import annotations

import json
import base64
import hashlib
import math
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

MAX_BYTES = 6 * 1024 * 1024
MAX_NODES = 30000
# Large roleless ChatGPT responses can place thousands of blocks under one
# flex/grid parent. Byte/node caps still prevent unbounded trees.
MAX_CHILDREN = 8192
MAX_DEPTH = 64
MAX_TEXT = 300000
MAX_ACTIVE = 128
MAX_AGE_SECONDS = 90 * 86400
SNAPSHOT_FILENAME = re.compile(r"(?:req|resp|imgreq)_[a-zA-Z0-9_-]{6,100}\.json")
MATH_TAGS = {"math", "semantics", "mrow", "mi", "mo", "mn", "msub", "mfrac",
             "mtext", "mover", "munder", "munderover", "msup", "msubsup", "msqrt", "mroot"}
SVG_TAGS = {"svg", "g", "path", "line", "rect", "circle", "ellipse",
            "polyline", "polygon", "text", "tspan", "defs", "clippath"}
SVG_ATTRS = {"viewBox", "width", "height", "preserveAspectRatio", "x", "y",
             "dx", "dy", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry",
             "d", "points", "transform", "fill", "stroke", "stroke-width",
             "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
             "fill-opacity", "stroke-opacity", "text-anchor",
             "dominant-baseline", "font-size", "font-weight", "id", "clip-path"}
SVG_NUMBER = re.compile(r"^[-+]?(?:\d{1,6}(?:\.\d{1,5})?|\.\d{1,5})(?:px|%)?$")
SVG_GEOMETRY = re.compile(r"^[MmLlHhVvCcSsQqTtAaZzEe0-9+.,\-\s]+$")
SVG_NUMBERS = re.compile(r"^[0-9eE+.,\-\s]+$")
SVG_COLOR = re.compile(
    r"^(?:none|currentColor|transparent|black|white|red|blue|green|gray|grey|"
    r"#[\da-f]{3,8}|rgba?\([\d.,\s%]+\))$", re.I)
SVG_TRANSFORM = re.compile(
    r"^(?:(?:translate|scale|rotate|matrix|skewX|skewY)\([-+0-9.,\s]+\)\s*){1,8}$")
SVG_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,80}$")
SVG_CLIP = re.compile(r"^url\(#([A-Za-z_][A-Za-z0-9_-]{0,80})\)$")
SVG_RATIO = re.compile(r"^(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max))(?:\s+(?:meet|slice))?$")

def _svg_attribute(key: str, value: object) -> bool:
    """Only inert, bounded vector geometry. Never URLs or event handlers."""
    if key not in SVG_ATTRS or not isinstance(value, str):
        return False
    if not value or value != value.strip() or len(value) > (
            16000 if key == "d" else 4096 if key == "points" else 240):
        return False
    if key == "d":
        return bool(SVG_GEOMETRY.fullmatch(value))
    if key in {"points", "stroke-dasharray", "viewBox"}:
        return bool(SVG_NUMBERS.fullmatch(value))
    if key == "transform":
        return bool(SVG_TRANSFORM.fullmatch(value))
    if key in {"fill", "stroke"}:
        return bool(SVG_COLOR.fullmatch(value))
    if key == "id":
        return bool(SVG_ID.fullmatch(value))
    if key == "clip-path":
        return bool(SVG_CLIP.fullmatch(value))
    if key == "preserveAspectRatio":
        return bool(SVG_RATIO.fullmatch(value))
    if key == "stroke-linecap":
        return value in {"butt", "round", "square"}
    if key == "stroke-linejoin":
        return value in {"miter", "round", "bevel"}
    if key == "text-anchor":
        return value in {"start", "middle", "end"}
    if key == "dominant-baseline":
        return value in {"auto", "middle", "central", "hanging",
                         "alphabetic", "text-after-edge"}
    if key == "font-weight":
        return value in {"normal", "bold", "400", "500", "600", "700"}
    return bool(SVG_NUMBER.fullmatch(value))


SAFE_TAGS = MATH_TAGS | SVG_TAGS | {"div", "p", "span", "strong", "em", "h1", "h2", "h3", "h4",
             "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead",
             "tbody", "tr", "td", "th", "figure", "figcaption", "img", "hr", "br"}
SAFE_STYLES = {
    "display": {"block", "inline", "inline-block", "flex", "inline-flex", "grid", "block math", "inline math"},
    "flexDirection": {"row", "row-reverse", "column", "column-reverse"},
    "flexWrap": {"wrap", "nowrap"},
    "justifyContent": {"start", "center", "end", "space-between", "space-around"},
    "alignItems": {"start", "center", "end", "stretch", "baseline"},
    "objectFit": {"contain", "cover", "fill", "none"},
    "textAlign": {"left", "center", "right", "justify", "start", "end"},
    "fontWeight": {"400", "500", "600", "700"},
    "whiteSpace": {"normal", "nowrap", "pre", "pre-wrap", "pre-line", "break-spaces"},
    "wordBreak": {"normal", "break-all", "keep-all", "break-word"},
    "overflowWrap": {"normal", "break-word", "anywhere"},
    "verticalAlign": {"baseline", "middle", "top", "bottom", "sub", "super"},
    "textDecorationLine": {"none", "underline", "line-through", "overline"},
    "borderStyle": {"none", "solid", "dashed", "dotted", "double"},
    "alignSelf": {"auto", "start", "center", "end", "stretch", "baseline"},
    "justifySelf": {"auto", "start", "center", "end", "stretch"},
}
LENGTH_STYLES = {"gap", "rowGap", "columnGap", "padding", "marginTop", "marginBottom",
                 "marginLeft", "marginRight", "width", "minWidth", "maxWidth", "height",
                 "minHeight", "maxHeight", "borderRadius", "borderWidth",
                 "letterSpacing", "fontSize", "lineHeight"}
COLOR_STYLES = {"color", "backgroundColor", "borderColor"}
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

    def node(value: object, depth: int, parent_tag: str | None = None) -> dict:
        nonlocal count, chars
        if depth > MAX_DEPTH or count >= MAX_NODES or not isinstance(value, dict):
            raise ValueError("Native tree exceeds limit")
        count += 1
        tag = value.get("tag")
        if tag not in SAFE_TAGS or not isinstance(tag, str):
            raise ValueError("Unsupported native element")
        if parent_tag in SVG_TAGS and tag not in SVG_TAGS:
            raise ValueError("Invalid SVG children")
        if tag in SVG_TAGS and parent_tag not in SVG_TAGS and tag != "svg":
            raise ValueError("Unscoped SVG element")
        if set(value) - {"tag", "text", "children", "style", "media_index",
                          "intrinsic_width", "intrinsic_height", "source_sha256", "attrs"}:
            raise ValueError("Native element has unsafe attributes")
        result = {"tag": tag}
        if "attrs" in value:
            if tag not in SVG_TAGS or not isinstance(value["attrs"], dict) or len(value["attrs"]) > 24:
                raise ValueError("Unsafe SVG attributes")
            if any(not isinstance(key, str) or not _svg_attribute(key, val)
                   for key, val in value["attrs"].items()):
                raise ValueError("Unsafe SVG attributes")
            result["attrs"] = dict(value["attrs"])
        if tag == "img":
            if set(value) - {"tag", "media_index", "style",
                             "intrinsic_width", "intrinsic_height", "source_sha256"}:
                raise ValueError("Image attributes must be asset references")
            idx = value.get("media_index")
            if type(idx) is not int or idx < 0 or idx >= 40:
                raise ValueError("Invalid media index")
            result["media_index"] = idx
            has_width = "intrinsic_width" in value
            has_height = "intrinsic_height" in value
            if has_width != has_height:
                raise ValueError("Incomplete image dimensions")
            if has_width:
                width, height = value["intrinsic_width"], value["intrinsic_height"]
                if (type(width) is not int or type(height) is not int
                    or not 1 <= width <= 16384 or not 1 <= height <= 16384):
                    raise ValueError("Invalid intrinsic image dimensions")
                result["intrinsic_width"] = width
                result["intrinsic_height"] = height
            if "source_sha256" in value:
                digest = value["source_sha256"]
                if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
                    raise ValueError("Invalid image byte identity")
                result["source_sha256"] = digest
        elif any(k in value for k in ("media_index", "intrinsic_width",
                                       "intrinsic_height", "source_sha256")):
            raise ValueError("Image reference on non-image element")
        if "text" in value:
            content = value["text"]
            if tag == "img" or tag in (SVG_TAGS - {"text", "tspan"}) or not isinstance(content, str):
                raise ValueError("Invalid text")
            chars += len(content)
            if chars > MAX_TEXT:
                raise ValueError("Native content too large")
            result["text"] = content
        if "style" in value:
            raw = value["style"]
            if not isinstance(raw, dict) or len(raw) > 32:
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
            if tag in {"img", "hr", "br"} or not isinstance(children, list) or len(children) > MAX_CHILDREN:
                raise ValueError("Invalid children")
            # Namespace integrity: no HTML/script/image descendants under
            # MathML. Existing validated HTML content remains unchanged.
            if tag in MATH_TAGS and any(not isinstance(child, dict)
                or child.get("tag") not in MATH_TAGS for child in children):
                raise ValueError("Invalid MathML children")
            if tag in SVG_TAGS and any(not isinstance(child, dict)
                    or child.get("tag") not in SVG_TAGS for child in children):
                raise ValueError("Invalid SVG children")
            result["children"] = [node(child, depth + 1, tag) for child in children]
        return result

    root = node(payload["root"], 0)
    output = {"version": 1, "revision": revision, "root": root}
    if len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("Native snapshot byte cap")
    return output


class NativeChunkAssembler:
    """Authenticated Worker-only in-memory reassembly; partial trees are never published.

    Byte count, SHA256, ordered chunks, owner identity and validated full tree
    must all agree. Chunks are not durable and never own request terminals.
    """
    MAX_CHUNKS = 96
    CHUNK_BYTES = 64 * 1024
    MAX_SESSIONS = 16
    TTL = 120.0

    def __init__(self, store: "NativeContentStore"):
        self.store = store
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, dict] = OrderedDict()

    def progress(self, request_id: str, owner: str) -> dict:
        """Safe transport state only: never emit chunk bytes or source SHA."""
        self.store._valid_id(request_id)
        with self._lock:
            state = self._sessions.get(request_id)
            if not state or state["identity"][0] != owner:
                return {"state": "none"}
            _owner, revision, size, count, _digest = state["identity"]
            return {
                "state": "incomplete",
                "revision": revision,
                "received_chunks": len(state["parts"]),
                "expected_chunks": count,
                "total_bytes": size,
                "age_seconds": round(max(0.0, time.monotonic() - state["at"]), 2),
            }

    def ingest(self, request_id: str, owner: str, value: object) -> bool:
        self.store._valid_id(request_id)
        if not owner or not isinstance(value, dict) or set(value) != {
            "revision", "index", "count", "byte_length", "sha256", "data"
        }:
            raise ValueError("Invalid native chunk envelope")
        revision, index = value["revision"], value["index"]
        count, size = value["count"], value["byte_length"]
        digest = value["sha256"]
        if (type(revision) is not int or not 1 <= revision <= 1000000
            or type(index) is not int or type(count) is not int
            or type(size) is not int or not 1 <= count <= self.MAX_CHUNKS
            or not 0 <= index < count or not 1 <= size <= MAX_BYTES
            or math.ceil(size / self.CHUNK_BYTES) != count
            or not isinstance(digest, str)
            or not re.fullmatch("[0-9a-f]{64}", digest)
            or not isinstance(value["data"], str)
            or len(value["data"]) > 4 * math.ceil(self.CHUNK_BYTES / 3) + 4):
            raise ValueError("Invalid native chunk bounds")
        try:
            chunk = base64.b64decode(value["data"], validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("Invalid native chunk encoding") from exc
        expected = min(self.CHUNK_BYTES, size - index * self.CHUNK_BYTES)
        if len(chunk) != expected:
            raise ValueError("Invalid native chunk length")
        now = time.monotonic()
        key = request_id
        with self._lock:
            for old, state in list(self._sessions.items()):
                if now - state["at"] > self.TTL:
                    del self._sessions[old]
            state = self._sessions.get(key)
            identity = (owner, revision, size, count, digest)
            if state and state["identity"] != identity:
                # Newer revisions can supersede incomplete older ones, but
                # neither a different key-owner nor an older revision may do so.
                old_owner, old_revision, *_ = state["identity"]
                if old_owner != owner or revision < old_revision:
                    raise ValueError("Native chunk owner/revision conflict")
                del self._sessions[key]
                state = None
            if state is None:
                state = {"identity": identity, "at": now, "parts": {}}
                self._sessions[key] = state
                self._sessions.move_to_end(key)
                while len(self._sessions) > self.MAX_SESSIONS:
                    self._sessions.popitem(last=False)
            previous = state["parts"].get(index)
            if previous is not None and previous != chunk:
                del self._sessions[key]
                raise ValueError("Conflicting native chunk")
            state["parts"][index] = chunk
            state["at"] = now
            if len(state["parts"]) != count:
                return False
            raw = b"".join(state["parts"][i] for i in range(count))
            del self._sessions[key]
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Native snapshot SHA256 mismatch")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid native snapshot serialization") from exc
        if not isinstance(decoded, dict) or decoded.get("revision") != revision:
            raise ValueError("Native chunk revision mismatch")
        # publish invokes the full safe-node and allowed-style validator.
        # No partial data ever replaces the preceding complete snapshot.
        return self.store.publish(request_id, owner, decoded)


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
            # existing MediaArchive retention. Max 256 snapshots / 90 days.
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
    media_indices = []
    while pending and counts["nodes"] < MAX_NODES:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        counts["nodes"] += 1
        tag = node.get("tag")
        if tag == "img":
            counts["images"] += 1
            media_indices.append(node.get("media_index"))
        if tag == "table":
            counts["tables"] += 1
        mode = (node.get("style") or {}).get("display")
        if mode == "grid":
            counts["grid"] += 1
        elif mode in {"flex", "inline-flex"}:
            counts["flex"] += 1
        pending.extend(node.get("children") or ())
    # Equal image counts alone cannot establish correct asset ownership:
    # duplicated/skipped indices can show the wrong archived picture in a card.
    # Preserve native preview only when every archived asset is referenced once.
    counts["media_mapping_complete"] = (
        type(archived_assets) is int
        and 0 <= archived_assets <= 40
        and counts["images"] == archived_assets
        and sorted(media_indices) == list(range(archived_assets))
    )
    counts["revision"] = snapshot.get("revision")
    counts["layout_schema"] = snapshot.get("version")
    return counts


def native_unique_dimension_candidates(snapshot: dict, archived_media: list[dict]) -> dict:
    """Best-effort *unverified* visual preview mapping, never proof of identity.

    Some requests archive extra raster assets not present in the final DOM.
    Match only if each visible image has one uniquely sized owned asset.
    This does not match pixels or SHA256; callers MUST label accordingly.
    """
    images=[]
    pending=[snapshot.get("root")]
    while pending and len(images)<=40:
        node=pending.pop()
        if not isinstance(node,dict):
            continue
        if node.get("tag")=="img":
            images.append(node)
        else:
            pending.extend(reversed(node.get("children") or ()))
    if not images or len(images)>40 or len(archived_media)>40:
        return {"status":"unavailable","indices":[]}
    found=[]
    used=set()
    for expected,node in enumerate(images):
        if node.get("media_index")!=expected:
            return {"status":"unavailable","indices":[]}
        width=node.get("intrinsic_width")
        height=node.get("intrinsic_height")
        if type(width) is not int or type(height) is not int:
            return {"status":"unavailable","indices":[]}
        candidates=[index for index,item in enumerate(archived_media)
                    if isinstance(item,dict)
                    and item.get("width")==width and item.get("height")==height]
        if len(candidates)!=1 or candidates[0] in used:
            return {"status":"unavailable","indices":[]}
        found.append(candidates[0])
        used.add(candidates[0])
    return {"status":"unique-dimensions-unverified","indices":found}



def native_image_identity_candidates(snapshot: dict, archived_media: list[dict]) -> dict:
    """Map exact original bytes when proven; otherwise show explicit uncertainty.

    Crops/re-encodes/dimension similarity NEVER count as byte identity. Identical
    hashes may legitimately appear at multiple DOM positions. Every asset must
    belong to the same request via the caller's verified archive inventory.
    """
    images=[]
    pending=[snapshot.get("root")]
    while pending and len(images) <= 40:
        node=pending.pop()
        if not isinstance(node,dict):
            continue
        if node.get("tag")=="img":
            images.append(node)
        else:
            pending.extend(reversed(node.get("children") or ()))
    if not images or len(images)>40 or len(archived_media)>40:
        return {"status":"unavailable","indices":[]}
    proofs=[node.get("source_sha256") for node in images]
    if not any(proofs):
        return native_unique_dimension_candidates(snapshot,archived_media)
    if not all(isinstance(p,str) and re.fullmatch("[0-9a-f]{64}",p) for p in proofs):
        return {"status":"identity-incomplete","indices":[]}
    table={}
    for index,media in enumerate(archived_media):
        if not isinstance(media,dict):
            continue
        digest=media.get("sha256")
        if isinstance(digest,str) and re.fullmatch("[0-9a-f]{64}",digest):
            table.setdefault(digest,index)
    result=[]
    for index,proof in enumerate(proofs):
        if images[index].get("media_index") != index or proof not in table:
            return {"status":"identity-incomplete","indices":[]}
        result.append(table[proof])
    return {"status":"verified-sha256","indices":result}


def native_public_v1_tree(root: dict) -> dict:
    """Strip new optional dimension hints for existing strict v1 consumers.

    Private admin layout evidence retains dimension hints for candidate
    display; old MV3C clients accept only the original five safe node keys.
    """
    result = {key: value for key,value in root.items()
              if key not in ("intrinsic_width", "intrinsic_height", "source_sha256", "attrs", "children")}
    # Existing strict v1 consumers accept HTML nodes, not arbitrary MathML.
    # Preserve MathML natively in private admin preview; public v1 gets safe
    # inert HTML spans until the protocol formally negotiates MathML.
    if root.get("tag") in MATH_TAGS | SVG_TAGS:
        result["tag"] = "span"
        if "style" in result:
            result["style"] = {k:v for k,v in result["style"].items() if k!="display"}
    if "children" in root:
        result["children"] = [native_public_v1_tree(child) for child in root["children"]]
    return result
