"""Admin preview is separate from the canonical API reply and requires asset ownership."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE=(ROOT/"app"/"request_history_v94_patch.py").read_text("utf-8")
SERVER=(ROOT/"app"/"main.py").read_text("utf-8")

def test_request_dialog_has_explicit_native_preview_switch():
    assert 'requestConversationNativeMode' in SOURCE
    assert 'requestConversationMarkdownMode' in SOURCE
    assert 'requestHistoryLoadNativePreview(requestId,row,sequence)' in SOURCE
    assert '/api/admin/requests/' in SOURCE and "'/native-content'" in SOURCE
    assert '@app.get("/api/admin/requests/{request_id}/native-content", dependencies=[Depends(require_admin_key)])' in SERVER

def test_unmapped_images_have_inert_placeholders_without_discarding_rich_layout():
    assert "const noNativeImages=Number.isInteger(imageCount)&&imageCount===0" in SOURCE
    assert "原生回复没有已绑定图片" in SOURCE
    assert 'previewAssets.length!==imageCount' in SOURCE
    assert 'requestHistoryNativeNode(snapshot.root,previewAssets,{count:0})' in SOURCE
    assert "candidate?.status==='verified-sha256-partial'" in SOURCE
    assert "candidate?.status==='verified-sha256'" in SOURCE
    assert "原生图片索引无效" in SOURCE
    assert "字节身份未验证（未插入图片）" in SOURCE
    assert "Array(imageCount).fill(null)" in SOURCE
    assert "completeOrdinal?assets:" not in SOURCE
    assert "dimensionHint?candidate.indices" not in SOURCE
    assert "结构捕获完成不等于像素级 1:1 验收" in SOURCE

def test_archived_media_list_is_not_capped_at_four_and_supports_med_id():
    assert "row.response_media.slice(0,40)" in SOURCE
    assert "row?.response_media)?row.response_media.slice(0,40)" in SOURCE
    assert "response_media.slice(0,4)" not in SOURCE
    assert "requestHistoryOwnedImageUrl(item?.owned_url||item?.url)" in SOURCE
    assert "requestHistoryMediaUrls.push(imageUrl)" in SOURCE
    assert 'fileId' in SOURCE

def test_native_preview_builds_dom_from_approved_elements_without_raw_html():
    assert "requestHistoryNativeTags" in SOURCE
    assert "requestHistoryNativeStyles" in SOURCE
    assert "requestHistoryNativeNode" in SOURCE
    assert ":document.createElement(value.tag)" in SOURCE
    assert "document.createElementNS('http://www.w3.org/1998/Math/MathML',value.tag)" in SOURCE
    assert "node.textContent=value.text" in SOURCE
    assert "node.style[key]=raw" in SOURCE
    assert "node.innerHTML" not in SOURCE
    assert "node.style.cssText=raw" not in SOURCE


def test_request_detail_refreshes_deferred_files_without_overwriting_terminal_history():
    assert "current = dict(row)" in SERVER
    assert 'row.get("response_generated_files")' in SERVER
    assert "await asyncio.to_thread(" in SERVER
    assert "deferred_generated_files.status, matched.group(1), matched.group(2)" in SERVER
    assert 'current["response_generated_files_live_checked"] = True' in SERVER
    assert "current[\"response_generated_files\"] = live" in SERVER


def test_native_preview_renderer_matches_current_30000_node_capture_limit():
    # 49070880: Worker successfully captured 1311 native nodes but the old
    # admin renderer threw once its local counter exceeded 1200.
    assert "++budget.count>30000" in SOURCE
    assert "++budget.count>6400" not in SOURCE
    assert "++budget.count>1200" not in SOURCE


def test_pending_generated_files_refresh_without_changing_request_terminal():
    # req_49070880 exported four processing files; a one-shot read remains stale.
    assert "async function requestHistoryMonitorGeneratedFiles(requestId,initialFiles,sequence)" in SOURCE
    assert "void requestHistoryMonitorGeneratedFiles(requestId,files,sequence);" in SOURCE
    assert "row?.response_generated_files_live_checked!==true" in SOURCE
    assert "f?.status==='processing'||f?.status==='pending'" in SOURCE
    assert "sequence!==requestHistoryMediaSequence" in SOURCE
    assert "setTimeout(resolve,4000)" in SOURCE
    assert "attempt<45" in SOURCE
    assert "if(++errors>=3)" in SOURCE
    assert "requestHistoryGeneratedFileStatusLabel(files)" in SOURCE


def test_admin_native_preview_matches_worker_child_budget():
    assert "if(children.length>8192)throw new Error('原生快照子节点超限');" in SOURCE
    assert "if(children.length>80)throw new Error('原生快照子节点超限');" not in SOURCE


def test_unproven_dimensions_or_ordinal_never_map_to_native_image_bytes():
    assert "data?.image_asset_candidates" in SOURCE
    assert "const previewAssets=noNativeImages?[]" in SOURCE
    assert "candidate.indices.map(i=>i===null?null:assets[i]||null)" in SOURCE
    assert "Array(imageCount).fill(null)" in SOURCE
    assert "完整" not in SOURCE.split("const completeOrdinal=")[-1].split("const previewAssets=")[0] if "const completeOrdinal=" in SOURCE else True
    assert "native_image_identity_candidates(" in SERVER


def test_only_sha_proven_image_positions_get_owned_asset_urls():
    # Extra archived assets and unverified slots cannot inject the wrong image.
    assert "previewAssets.filter(Boolean).length" in SOURCE
    assert "assets.some(u=>!u)" not in SOURCE
    assert "previewAssets.length!==imageCount" in SOURCE
    assert "const owned=assets[index]" in SOURCE
    assert "if(!owned)" in SOURCE


def test_17b4_admin_renderer_accepts_4000_nodes_but_not_30001():
    """Run the exact served renderer in Node, not a separately mocked limit."""
    import subprocess
    import json
    renderer = SOURCE.split("function requestHistoryNativeNode(", 1)[1].split(
        "async function requestHistoryLoadNativePreview(", 1
    )[0]
    js = """
    const assert=require('node:assert/strict');
    const requestHistoryNativeTags=new Set('div p span strong em h1 h2 h3 h4 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br svg g path line rect circle ellipse polyline polygon text tspan defs clippath math semantics mrow mi mo mn msub mfrac mtext mover munder munderover msup msubsup msqrt mroot'.split(' '));
    const requestHistoryNativeMathTags=new Set('math semantics mrow mi mo mn msub mfrac mtext mover munder munderover msup msubsup msqrt mroot'.split(' '));
    const requestHistoryNativeSvgTags=new Set('svg g path line rect circle ellipse polyline polygon text tspan defs clippath'.split(' '));
    const requestHistoryNativeSvgAttrs=new Set('viewBox width height preserveAspectRatio x y dx dy x1 x2 y1 y2 cx cy r rx ry d points transform fill stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray opacity fill-opacity stroke-opacity text-anchor dominant-baseline font-size font-weight id clip-path'.split(' '));
    const requestHistoryNativeStyles=new Set('display flexDirection flexWrap justifyContent alignItems objectFit textAlign fontWeight gap padding marginTop marginBottom width maxWidth height borderRadius fontSize lineHeight color backgroundColor gridTemplateColumns'.split(' '));
    const document={
       createElement(tag){return {tag,children:[],style:{},
         appendChild(c){this.children.push(c)}};},
       createElementNS(namespace,tag){return {tag,namespace,children:[],style:{},
         attrs:{},setAttribute(k,v){this.attrs[k]=v},
         appendChild(c){this.children.push(c)}};}
    };
    function requestHistoryNativeNode("""
    js += renderer
    js += """
    const safe={
      tag:'div',
      children:Array.from({length:50},(_,g)=>({
        tag:'div',children:Array.from({length:80},(_,n)=>({tag:'span',text:g+'-'+n}))
      }))
    };
    const budget={count:0};
    const rendered=requestHistoryNativeNode(safe,[],budget);
    assert.ok(budget.count>3200 && budget.count<6400,budget.count);
    assert.equal(rendered.children.length,50);
    assert.equal(rendered.children[49].children[79].textContent,'49-79');

    // 2a43's 165 children and >24 levels must survive the actual renderer.
    let deep={tag:'p',text:'last deep section'};
    for(let i=0;i<38;i++)deep={tag:'div',children:[deep]};
    const wide={tag:'div',children:Array.from({length:165},(_,i)=>({tag:'p',text:'section '+i}))};
    const current=requestHistoryNativeNode({tag:'div',children:[deep,wide]},[],{count:0});
    assert.equal(current.children[1].children[164].textContent,'section 164');
    let renderedDeep=current.children[0];
    for(let i=0;i<38;i++)renderedDeep=renderedDeep.children[0];
    assert.equal(renderedDeep.textContent,'last deep section');
    let tooDeep={tag:'p',text:'over limit'};
    for(let i=0;i<65;i++)tooDeep={tag:'div',children:[tooDeep]};
    assert.throws(()=>requestHistoryNativeNode(tooDeep,[],{count:0}),/原生快照结构无效/);
    assert.throws(()=>requestHistoryNativeNode({tag:'div',children:Array.from({length:8193},()=>({tag:'p'}))},[],{count:0}),/子节点超限/);

    const overflow={
      tag:'div',
      children:Array.from({length:100},()=>({
        tag:'div',children:Array.from({length:2},()=>({
          tag:'div',children:Array.from({length:160},()=>({tag:'span',text:'x'}))
        }))
      }))
    };
    assert.throws(()=>requestHistoryNativeNode(overflow,[],{count:0}),/原生快照结构无效/);
    const img=requestHistoryNativeNode({
      tag:'img', media_index:0,
      style:{objectFit:'cover',borderRadius:'12px',width:'320px',height:'180px'}
    },['/api/media/med_valid/asset-token'],{count:0});
    assert.equal(img.src,'/api/media/med_valid/asset-token');
    assert.equal(img.style.objectFit,'cover');
    assert.equal(img.style.borderRadius,'12px');
    assert.equal(img.style.width,'320px');
    assert.equal(img.style.height,'180px');
    const math=requestHistoryNativeNode({tag:'math',children:[
      {tag:'mrow',children:[{tag:'mtext',text:'每炉'}]}
    ]},[],{count:0});
    assert.equal(math.namespace,'http://www.w3.org/1998/Math/MathML');
    assert.equal(math.children[0].children[0].textContent,'每炉');
    assert.throws(()=>requestHistoryNativeNode({
      tag:'script',text:'alert(1)'},[],{count:0}),/原生快照结构无效/);
    const chart=requestHistoryNativeNode({tag:'svg',attrs:{
        viewBox:'0 0 650 300',width:'650',height:'300'
      },children:[{tag:'defs',children:[{tag:'clippath',
        attrs:{id:'internal-clip'},children:[{tag:'rect',
          attrs:{x:'0',y:'0',width:'650',height:'300'}}]}]},
      {tag:'g',attrs:{'clip-path':'url(#internal-clip)'},
        children:[{tag:'path',attrs:{d:'M0 220 L100 210 L500 20',
          stroke:'#2288cc',fill:'none'}}]},
      {tag:'text',text:'75万元',attrs:{x:'24',y:'38'}}]
    },[],{count:0});
    assert.equal(chart.namespace,'http://www.w3.org/2000/svg');
    const definedId=chart.children[0].children[0].attrs.id;
    assert.ok(definedId.startsWith('chat2api-native-v1-'));
    assert.equal(chart.children[1].attrs['clip-path'],'url(#'+definedId+')');
    assert.equal(chart.children[1].children[0].attrs.d,'M0 220 L100 210 L500 20');
    assert.equal(chart.children[2].textContent,'75万元');
    assert.throws(()=>requestHistoryNativeNode({tag:'svg',
      attrs:{onclick:'alert(1)'}},[],{count:0}),/SVG 属性不安全/);
    assert.throws(()=>requestHistoryNativeNode({tag:'svg',
      children:[{tag:'script',text:'evil'}]},[],{count:0}),/原生快照结构无效/);
    // Do not execute URL/CSS injection even in an image node.
    const injected=requestHistoryNativeNode({
      tag:'img',media_index:0,
      style:{borderRadius:'12px;position:fixed',backgroundColor:'url(https://bad)'}
    },['/api/media/med_valid/asset-token'],{count:0});
    assert.notEqual(injected.style.borderRadius,'12px;position:fixed');
    assert.equal(injected.style.backgroundColor,undefined);
    console.log('Admin native renderer: 4k accepted, >30000 rejected; image style preserved');
    """
    result=subprocess.run(
        ["node", "-e", js], cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    assert result.returncode==0,result.stderr+result.stdout


def test_sha256_verified_and_partial_images_never_fallback_to_ordinal():
    assert "candidate?.status==='verified-sha256'" in SOURCE
    assert "candidate?.status==='verified-sha256-partial'" in SOURCE
    assert "未验证图片保留原位置提示，绝不按顺序或尺寸替换" in SOURCE
    assert "图片按归档顺序展示" not in SOURCE
    assert "source_sha256" in (ROOT/"app"/"native_content_v1.py").read_text("utf-8")
    assert '"visual_gaps":' in SERVER
    assert '"visual_completeness_verified": False' in SERVER


def test_image_identity_fetch_does_not_block_request_terminal_unboundedly():
    worker=(ROOT/"chrome_extension"/"content_native_content_v1.js").read_text("utf-8")
    assert "const deadline=Date.now()+3500" in worker
    assert "Promise.race([" in worker
    assert "active.nativeIdentityVerifiedCount=digests.size" in worker
