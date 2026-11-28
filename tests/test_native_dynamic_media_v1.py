import base64
import hashlib
import io

import pytest
from PIL import Image

from app.media_archive_v173 import MediaArchive
from app.native_dynamic_media_v1 import archive_interaction_images,interaction_image_projection


def test_interaction_media_original_integrity_and_owner_scope(tmp_path):
    archive=MediaArchive(tmp_path,"test-master")
    out=io.BytesIO()
    Image.new("RGB",(12,12),"red").save(out,"PNG")
    raw=out.getvalue()
    digest=hashlib.sha256(raw).hexdigest()
    snapshot={"version":1,"revision":2,"root":{"tag":"div","children":[
        {"tag":"img","media_index":0,"source_sha256":digest},
        {"tag":"img","media_index":1,"source_sha256":digest}]}}
    payload={"sha256":digest,"mime":"image/png","base64":base64.b64encode(raw).decode()}
    with pytest.raises(ValueError):
        archive_interaction_images(archive,"alice","req_dynamic_media_123",snapshot,[])
    with pytest.raises(ValueError):
        archive_interaction_images(archive,"alice","req_dynamic_media_123",snapshot,[{**payload,"base64":"AAAA"}])
    manifest=archive_interaction_images(archive,"alice","req_dynamic_media_123",snapshot,[payload])
    assert len(manifest)==1 and manifest[0]["sha256"]==digest
    assert archive_interaction_images(archive,"alice","req_dynamic_media_123",snapshot,[])==manifest
    urls,metadata=interaction_image_projection(archive,"alice","req_dynamic_media_123",snapshot,manifest)
    assert len(urls)==1 and metadata[0]["sha256"]==digest
    _,media_id,token=urls[0].rsplit("/",2)
    assert archive.load(media_id,token)[0]==raw
    assert interaction_image_projection(archive,"bob","req_dynamic_media_123",snapshot,manifest)==([],[])
    assert interaction_image_projection(archive,"alice","req_other_media_123",snapshot,manifest)==([],[])
    with pytest.raises(ValueError):
        archive_interaction_images(archive,"bob","req_dynamic_media_123",snapshot,[])
    with pytest.raises(ValueError):
        archive_interaction_images(archive,"alice","req_dynamic_media_123",snapshot,[{**payload,"url":"https://example.test"}])
