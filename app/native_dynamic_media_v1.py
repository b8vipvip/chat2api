"""Byte-exact, request-owned media for post-terminal native interaction revisions.

Original images are immutable archive objects, not canvas captures. No source
URL or untrusted media token is accepted from the Worker.
"""
from __future__ import annotations

import base64
import hashlib
import re
from .media_archive_v173 import ALLOWED
from .native_content_v1 import native_image_identity_candidates

_HASH = re.compile(r"^[a-f0-9]{64}$")
_MAX_BYTES = 256 * 1024
_MAX_TOTAL = 384 * 1024


def image_hashes(snapshot):
    nodes, hashes = [snapshot["root"]], []
    while nodes:
        node = nodes.pop()
        if node.get("tag")=="img":
            digest = node.get("source_sha256")
            if (node.get("media_index")!=len(hashes) or not isinstance(digest,str)
                    or not _HASH.fullmatch(digest) or len(hashes)>=40):
                raise ValueError("Unverified interaction image slot")
            hashes.append(digest)
        nodes.extend(reversed(node.get("children") or []))
    return hashes


def archive_interaction_images(archive, owner, request_id, snapshot, media):
    hashes = set(image_hashes(snapshot))
    if not isinstance(media,list) or len(media)>4:
        raise ValueError("Invalid interaction media list")
    decoded, total = {},0
    for item in media:
        if not isinstance(item,dict) or set(item)!={"sha256","mime","base64"}:
            raise ValueError("Invalid interaction media envelope")
        digest,mime,encoded = item["sha256"],item["mime"],item["base64"]
        if (not isinstance(digest,str) or digest not in hashes or digest in decoded
                or not isinstance(mime,str) or mime not in ALLOWED or not isinstance(encoded,str)
                or len(encoded)>4*((_MAX_BYTES+2)//3)):
            raise ValueError("Invalid interaction original")
        raw = base64.b64decode(encoded,validate=True)
        total += len(raw)
        if not raw or len(raw)>_MAX_BYTES or total>_MAX_TOTAL or hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError("Interaction original checksum or size mismatch")
        decoded[digest]=(raw,mime)
    if not hashes:
        return []
    inventory=archive.request_image_inventory(owner,request_id,hashes)
    if hashes-set(inventory)-set(decoded):
        raise ValueError("Interaction image has no request-owned original")
    # Validate all missing originals before writing any archive rows.
    for digest in hashes-set(inventory):
        raw,mime=decoded[digest]
        archive._render_thumbnail(raw,mime)
    for digest in sorted(hashes-set(inventory)):
        raw,mime=decoded[digest]
        record=archive.store_image(raw,mime,owner,request_id,max_request_images=80)
        if record.get("sha256")!=digest:
            raise ValueError("Interaction archive checksum mismatch")
    inventory=archive.request_image_inventory(owner,request_id,hashes)
    if set(inventory)!=hashes:
        raise ValueError("Interaction archive incomplete")
    manifest=[{"id":inventory[d]["id"],"sha256":d} for d in sorted(hashes)]
    if native_image_identity_candidates(snapshot,manifest)["status"]!="verified-sha256":
        raise ValueError("Interaction image mapping incomplete")
    return manifest


def interaction_image_projection(archive, owner, request_id, snapshot, manifest):
    hashes=set(image_hashes(snapshot))
    if not isinstance(manifest,list) or len(manifest)>40:
        raise ValueError("Invalid interaction image manifest")
    expected={}
    for row in manifest:
        if (not isinstance(row,dict) or set(row)!={"id","sha256"} or
                not isinstance(row["sha256"],str) or not isinstance(row["id"],str) or
                row["sha256"] in expected):
            raise ValueError("Invalid interaction image reference")
        expected[row["sha256"]]=row["id"]
    if set(expected)!=hashes:
        raise ValueError("Interaction image manifest does not match revision")
    inventory=archive.request_image_inventory(owner,request_id,hashes)
    assets,metadata=[],[]
    for digest in sorted(hashes):
        item=inventory.get(digest)
        if not item or item["id"]!=expected[digest]:
            continue
        assets.append("/api/media/"+item["id"]+"/"+item["token"])
        metadata.append({"sha256":digest,"width":item["width"],"height":item["height"]})
    return assets,metadata
