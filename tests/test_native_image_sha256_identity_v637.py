"""Image identities must be bytes, not ordinal/dimension guesses."""
import pytest
from app.native_content_v1 import (native_image_identity_candidates,
                                   native_public_v1_tree, validate_native_snapshot)

A="a"*64
B="b"*64
C="c"*64


def tree(proofs):
    return validate_native_snapshot({"version":1,"revision":21,
        "root":{"tag":"div","children":[
            {"tag":"img","media_index":index,
             "intrinsic_width":1290,"intrinsic_height":860,
             **({"source_sha256":digest} if digest else {})}
            for index,digest in enumerate(proofs)]}})


def test_duplicate_dimensions_are_correctly_resolved_by_byte_digest():
    node=tree([B,A])
    archived=[
        {"width":1290,"height":860,"sha256":A,"id":"med_first"},
        {"width":1290,"height":860,"sha256":B,"id":"med_second"},
        {"width":500,"height":500,"sha256":C,"id":"med_extra"},
    ]
    result=native_image_identity_candidates(node,archived)
    # Both displayed slots are byte-matched, but a third *distinct* archive
    # SHA C has no native location. Do not declare the whole reply 1:1.
    assert result=={"status":"verified-sha256-partial",
                    "indices":[1,0],"matched_count":2}
    # Once all distinct image identities have actual DOM slots, positional
    # matching is exact even if archive order differs and dimensions repeat.
    assert native_image_identity_candidates(node,archived[:2])=={
        "status":"verified-sha256","indices":[1,0]}
    assert all("source_sha256" not in item for item in
               native_public_v1_tree(node["root"])["children"])


def test_identical_source_image_may_repeat_at_different_dom_positions():
    node=tree([A,A])
    assert native_image_identity_candidates(node,[{"sha256":A}])=={
        "status":"verified-sha256","indices":[0,0]}


def test_missing_or_contradictory_hash_never_uses_dimensions():
    node=tree([A,C])
    archived=[{"sha256":A,"width":1290,"height":860},
              {"sha256":B,"width":1290,"height":860}]
    # A proved byte identity remains usable at its exact DOM slot; the
    # missing/contradictory second slot must NEVER be filled from similar
    # dimensions, archive ordering or unrelated byte hash B.
    assert native_image_identity_candidates(node,archived)=={
        "status":"verified-sha256-partial",
        "indices":[0,None],"matched_count":1}
    assert native_image_identity_candidates(tree([A,None]),archived)=={
        "status":"verified-sha256-partial",
        "indices":[0,None],"matched_count":1}
    assert native_image_identity_candidates(tree([None,None]),archived)=={
        "status":"unavailable","indices":[]}


def test_hash_is_strictly_scoped_to_img_and_valid_lowercase_hex_only():
    for digest in ("bad", "0"*63, "G"*64, 123, "x"*64):
        with pytest.raises(ValueError,match="identity"):
            tree([digest])
    with pytest.raises(ValueError,match="Image reference"):
        validate_native_snapshot({"version":1,"revision":1,
            "root":{"tag":"div","source_sha256":A}})


def test_no_image_url_mime_or_blob_can_be_injected_through_digest():
    with pytest.raises(ValueError):
        validate_native_snapshot({"version":1,"revision":1,
            "root":{"tag":"img","media_index":0,
                    "source_sha256":"https://example.com/secret"}})


def test_background_hash_bridge_is_origin_scoped_and_byte_correct():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run(["node","tests/media_native_hash_v637.mjs"],
                          cwd=root,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+"\n"+result.stderr
