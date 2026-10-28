"""Generated file staging remains bounded, ordered and request-scoped."""
import base64
import hashlib

import pytest

from app.generated_file_staging_v238 import GeneratedFileStaging, MAX_CHUNK_BYTES
from app.generated_files_v196 import GeneratedFileArchive
from app.media_archive_v173 import MediaArchive

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UPID = "a" * 32


def push(staging, request_id, data, digest=None, complete=True):
    digest = digest or hashlib.sha256(data).hexdigest()
    chunks = [data[i:i + MAX_CHUNK_BYTES] for i in range(0,len(data),MAX_CHUNK_BYTES)]
    if not complete:
        chunks=chunks[:-1]
    for index,chunk in enumerate(chunks):
        staging.ingest(request_id=request_id,upload_id=UPID,
            filename="budget.xlsx",mime=MIME,sequence=index,
            expected_bytes=len(data),expected_sha256=digest,
            base64_chunk=base64.b64encode(chunk).decode(),
            final=(complete and index==len(chunks)-1))


def test_large_staged_xlsx_is_archived_with_exact_bytes(tmp_path):
    staging=GeneratedFileStaging(tmp_path)
    payload=b"PK\x03\x04"+b"Z"*(6*1024*1024+101)
    push(staging,"req_real",payload)
    assert staging.read("req_real",UPID,"budget.xlsx")== (payload,MIME)
    with pytest.raises(ValueError):
        staging.read("req_unrelated",UPID,"budget.xlsx")
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
        staging.read("req_real",UPID,"budget.xlsx")


def test_stage_rejects_incorrect_order_and_bad_digest(tmp_path):
    staging=GeneratedFileStaging(tmp_path)
    data=b"PK\x03\x04"+b"ab"*1500
    kw=dict(request_id="req_bad",upload_id=UPID,filename="budget.xlsx",
        mime=MIME,expected_bytes=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
        base64_chunk=base64.b64encode(data).decode(),final=True)
    with pytest.raises(ValueError):
        staging.ingest(**kw,sequence=1)
    staging.ingest(**kw,sequence=0)
    with pytest.raises(ValueError):
        staging.ingest(**kw,sequence=0)
    with pytest.raises(ValueError):
        staging.read("req_bad",UPID,"other.xlsx")
    with pytest.raises(ValueError):
        staging.read("req_other",UPID,"budget.xlsx")
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
