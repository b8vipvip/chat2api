"""Generated file staging remains bounded, ordered and request-scoped."""
import base64
import hashlib
import sqlite3
import time

import pytest

from app.generated_file_staging_v238 import (
    GeneratedFileStaging, MAX_CHUNK_BYTES, MAX_STAGE_AGE_SECONDS,
)
from app.generated_files_v196 import GeneratedFileArchive
from app.media_archive_v173 import MediaArchive

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UPID = "a" * 32


def push(staging, request_id, data, digest=None, complete=True, owner_key_id="owner"):
    digest = digest or hashlib.sha256(data).hexdigest()
    chunks = [data[i:i + MAX_CHUNK_BYTES] for i in range(0,len(data),MAX_CHUNK_BYTES)]
    if not complete:
        chunks=chunks[:-1]
    for index,chunk in enumerate(chunks):
        staging.ingest(request_id=request_id,owner_key_id=owner_key_id,upload_id=UPID,
            filename="budget.xlsx",mime=MIME,sequence=index,
            expected_bytes=len(data),expected_sha256=digest,
            base64_chunk=base64.b64encode(chunk).decode(),
            final=(complete and index==len(chunks)-1))


def test_large_staged_xlsx_is_archived_with_exact_bytes(tmp_path):
    first_process=GeneratedFileStaging(tmp_path)
    payload=b"PK\x03\x04"+b"Z"*(6*1024*1024+101)
    push(first_process,"req_real",payload)
    # A fresh instance models the API process restarting before terminal
    # replay; only a fully verified stage is restored.
    staging=GeneratedFileStaging(tmp_path)
    assert staging.read("req_real",UPID,"budget.xlsx",owner_key_id="owner")== (payload,MIME)
    with pytest.raises(ValueError):
        staging.read("req_real",UPID,"budget.xlsx",owner_key_id="other-owner")
    with pytest.raises(ValueError):
        staging.read("req_unrelated",UPID,"budget.xlsx",owner_key_id="owner")
    archive=GeneratedFileArchive(MediaArchive(tmp_path,"secret"))
    archive.staging=staging
    text,records=archive.rewrite_markdown(
        "![📎 budget.xlsx](chat2api-staged:"+UPID+")",
        "owner","req_real","https://files.example")
    assert "chat2api-staged:" not in text
    assert records[0]["bytes"]==len(payload)
    row=archive.describe(records[0]["id"],"owner")
    assert archive.read(row["id"],row["token"]) == (payload,MIME,"budget.xlsx")
    with pytest.raises(ValueError):
        staging.read("req_real",UPID,"budget.xlsx",owner_key_id="owner")


def test_stage_rejects_incorrect_order_and_bad_digest(tmp_path):
    staging=GeneratedFileStaging(tmp_path)
    data=b"PK\x03\x04"+b"ab"*1500
    kw=dict(request_id="req_bad",owner_key_id="owner",upload_id=UPID,filename="budget.xlsx",
        mime=MIME,expected_bytes=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        base64_chunk=base64.b64encode(data).decode(),final=True)
    with pytest.raises(ValueError):
        staging.ingest(**kw,sequence=1)
    staging.ingest(**kw,sequence=0)
    with pytest.raises(ValueError):
        staging.ingest(**kw,sequence=0)
    with pytest.raises(ValueError):
        staging.read("req_bad",UPID,"other.xlsx",owner_key_id="owner")
    with pytest.raises(ValueError):
        staging.read("req_other",UPID,"budget.xlsx",owner_key_id="owner")
    with pytest.raises(ValueError):
        push(staging,"req_corrupt",data,digest="0"*64)


def test_missing_final_never_archives_and_mixed_batch_is_atomic(tmp_path):
    staging=GeneratedFileStaging(tmp_path)
    payload=b"PK\x03\x04"+b"x"*(MAX_CHUNK_BYTES+100)
    push(staging,"req_incomplete",payload,complete=False)
    archive=GeneratedFileArchive(MediaArchive(tmp_path,"secret"))
    archive.staging=staging
    source="![📎 okay.pdf](data:application/pdf;base64,"+base64.b64encode(b"%PDF-1.7\n").decode()+")"
    source+="\n\n![📎 budget.xlsx](chat2api-staged:"+UPID+")"
    with pytest.raises(ValueError):
        archive.rewrite_markdown(source,"owner","req_incomplete","https://files.example")
    assert archive.list_items(10)==[]


def test_restart_promotes_fully_written_stage_and_discards_partial_or_corrupt_files(tmp_path):
    complete = GeneratedFileStaging(tmp_path)
    payload = b"PK\x03\x04" + b"complete" * 2048
    push(complete, "req_full_before_flag", payload)
    with sqlite3.connect(complete.db_path) as conn:
        conn.execute(
            "UPDATE generated_file_stages SET finished=0 WHERE request_id=? AND upload_id=?",
            ("req_full_before_flag", UPID),
        )
    recovered = GeneratedFileStaging(tmp_path)
    assert recovered.read(
        "req_full_before_flag", UPID, "budget.xlsx", owner_key_id="owner",
    ) == (payload, MIME)

    partial = GeneratedFileStaging(tmp_path)
    partial_payload = b"PK\x03\x04" + b"partial" * (MAX_CHUNK_BYTES // 7 + 10)
    push(partial, "req_partial", partial_payload, complete=False)
    partial_recovered = GeneratedFileStaging(tmp_path)
    with pytest.raises(ValueError, match="not verified"):
        partial_recovered.read("req_partial", UPID, "budget.xlsx", owner_key_id="owner")

    corrupt = GeneratedFileStaging(tmp_path)
    corrupt_payload = b"PK\x03\x04" + b"verified"
    push(corrupt, "req_corrupt_restart", corrupt_payload)
    corrupt_path = corrupt.entries[("req_corrupt_restart", UPID)].path
    corrupt_path.write_bytes(b"tampered")
    corrupt_recovered = GeneratedFileStaging(tmp_path)
    with pytest.raises(ValueError, match="not verified"):
        corrupt_recovered.read(
            "req_corrupt_restart", UPID, "budget.xlsx", owner_key_id="owner",
        )
    assert not corrupt_path.exists()


def test_startup_does_not_delete_another_process_incomplete_stage(tmp_path):
    first_process = GeneratedFileStaging(tmp_path)
    payload = b"PK\x03\x04" + b"partial-stage" * (MAX_CHUNK_BYTES // 13 + 20)
    digest = hashlib.sha256(payload).hexdigest()
    chunks = [payload[i:i + MAX_CHUNK_BYTES] for i in range(0, len(payload), MAX_CHUNK_BYTES)]
    push(first_process, "req_live_upload", payload, complete=False)
    stage_path = first_process.entries[("req_live_upload", UPID)].path

    # A second process startup must not delete an upload still owned by the
    # first process. It does not restore partial bytes into its own memory.
    second_process = GeneratedFileStaging(tmp_path)
    assert stage_path.exists()
    with pytest.raises(ValueError):
        second_process.read("req_live_upload", UPID, "budget.xlsx", owner_key_id="owner")

    last = chunks[-1]
    first_process.ingest(
        request_id="req_live_upload", owner_key_id="owner", upload_id=UPID,
        filename="budget.xlsx", mime=MIME, sequence=len(chunks) - 1,
        expected_bytes=len(payload), expected_sha256=digest,
        base64_chunk=base64.b64encode(last).decode(), final=True,
    )
    assert first_process.read(
        "req_live_upload", UPID, "budget.xlsx", owner_key_id="owner",
    ) == (payload, MIME)



def test_prune_expires_incomplete_stage_not_restored_by_this_process(tmp_path):
    first_process = GeneratedFileStaging(tmp_path)
    payload = b"x" * (MAX_CHUNK_BYTES + 100)
    request_id = "req_expired_cross_process"
    push(first_process, request_id, payload, complete=False)
    stage_path = first_process.entries[(request_id, UPID)].path

    # The second process keeps a fresh incomplete upload intact, but does not
    # restore it into memory because it belongs to the first process.
    second_process = GeneratedFileStaging(tmp_path)
    assert stage_path.exists()
    with sqlite3.connect(second_process.db_path) as conn:
        conn.execute(
            "UPDATE generated_file_stages SET created_at=? WHERE request_id=? AND upload_id=?",
            (time.time() - MAX_STAGE_AGE_SECONDS - 1, request_id, UPID),
        )

    with pytest.raises(ValueError, match="not verified"):
        second_process.read(request_id, UPID, "budget.xlsx", owner_key_id="owner")

    assert not stage_path.exists()
    with sqlite3.connect(second_process.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM generated_file_stages WHERE request_id=? AND upload_id=?",
            (request_id, UPID),
        ).fetchone()[0] == 0
