/* Independently releasable Windows ChatGPT history DOM collector.
 * Executed only through Chrome userScripts.execute in USER_SCRIPT world.
 * No extension APIs, page mutation, network access or live request terminals.
 */
(()=>{
  const DIAGNOSTIC_TOOL_VERSION="1.0.0";
  function capturePage() {
    const now=new Date().toISOString();
    const visible=node=>{
      try{const r=node.getBoundingClientRect(),s=getComputedStyle(node);
        return r.width>0&&r.height>0&&s.display!=="none"&&s.visibility!=="hidden";
      }catch(_){return false;}
    };
    const USER="[data-message-author-role='user'],[data-conversation-role='user'],[data-testid='user-message']";
    const SKIP="script,style,form,textarea,input,button,nav,aside,header,footer,[contenteditable='true'],[role='textbox'],[role='dialog'],[role='menu']";
    const selections=[];
    for(const heading of document.querySelectorAll("h4[data-conversation-role='assistant']")){
      const wrap=heading.closest("[data-chatgpt-search-message-ids]");
      if(!wrap||wrap.querySelector("h4[data-conversation-role='user']"))continue;
      const list=String(wrap.getAttribute("data-chatgpt-search-message-ids")||"").split(/\s+/);
      for(const sel of wrap.querySelectorAll("[data-chatgpt-selection-message-id]")){
        const id=String(sel.getAttribute("data-chatgpt-selection-message-id")||"");
        if(!id||!list.includes(id)||!visible(sel)||sel.querySelector(USER))continue;
        const md=sel.querySelector("[data-markdown-text-style='assistant-message']");
        if(!md||!visible(md))continue;
        const dil=md.querySelector("[data-dil-message-id]");
        const source=md.querySelector("[data-dil-source-message-id]");
        if(dil&&dil.getAttribute("data-dil-message-id")!==id||
            source&&source.getAttribute("data-dil-source-message-id")!==id)continue;
        selections.push({node:sel,id});
      }
    }
    for(const role of document.querySelectorAll("[data-message-author-role='assistant']")){
      if(!visible(role)||role.querySelector(USER))continue;
      if(selections.some(s=>s.node===role||s.node.contains(role)))continue;
      selections.push({node:role,id:String(role.getAttribute("data-message-id")||"")});
    }
    const target=selections[selections.length-1];
    // A stable DOM is not necessarily a finished response. A generation may
    // pause without changing node/text counts for multiple polling intervals.
    const generationActive=Array.from(document.querySelectorAll(
      "[data-testid='stop-button'],[data-testid='composer-stop-button'],"+
      "[data-testid='stop-generating-button'],"+
      "button[aria-label='Stop generating'],button[aria-label='停止生成'],"+
      "[data-conversation-role='assistant'][aria-busy='true']"
    )).some(visible);
    const probe={
      ready_state:document.readyState,assistant_role_nodes:document.querySelectorAll(
        "h4[data-conversation-role='assistant'],[data-message-author-role='assistant']").length,
      native_message_nodes:document.querySelectorAll("[data-chatgpt-selection-message-id]").length,
      user_role_nodes:document.querySelectorAll(
        "h4[data-conversation-role='user'],[data-message-author-role='user']").length,
      selected_assistant_count:selections.length,
      generation_active:generationActive,
      captured_at:now
    };
    if(!target)return {ok:false,stage:"no-safe-assistant-boundary",probe};
    const allowed=new Set("div p span strong em h1 h2 h3 h4 h5 h6 ul ol li blockquote pre code table thead tbody tr td th figure figcaption img hr br math semantics mrow mi mo mn mfrac msub msup msubsup".split(" "));
    const cssKeys=["display","fontSize","fontWeight","lineHeight","color","backgroundColor",
      "textAlign","whiteSpace","padding","marginTop","marginBottom","marginLeft","marginRight",
      "width","height","gridTemplateColumns","gridTemplateRows","gap","borderWidth"];
    const safeCss=v=>typeof v==="string"&&v.length<100&&
      !/(?:url\s*\(|expression|javascript:|var\s*\(|calc\s*\(|[;<>{}])/i.test(v);
    let nodes=0,chars=0,images=0,tables=0,truncated=false;
    function walk(node,depth) {
      if(truncated||depth>64){truncated=true;return null;}
      if(node.nodeType===Node.TEXT_NODE){
        const value=String(node.nodeValue||"");
        if(!value.trim())return null;
        if(chars+value.length>150000){truncated=true;return null;}
        chars+=value.length;return {tag:"#text",text:value};
      }
      if(!(node instanceof Element)||!visible(node)||node.matches(SKIP)||node.matches(USER))return null;
      if(++nodes>12000){truncated=true;return null;}
      const tag=node.tagName.toLowerCase();
      const out={tag:allowed.has(tag)?tag:"div"};
      if(out.tag==="img"){
        images++;
        const box=node.getBoundingClientRect();
        out.image={natural_width:Math.max(0,Number(node.naturalWidth)||0),
          natural_height:Math.max(0,Number(node.naturalHeight)||0),
          display_width:Math.round(box.width*100)/100,
          display_height:Math.round(box.height*100)/100,
          loaded:node.complete===true};
      }else if(out.tag==="table"){tables++;}
      const computed=getComputedStyle(node);
      const styles={};
      for(const key of cssKeys){
        const v=computed[key];
        if(safeCss(v))styles[key]=v;
      }
      if(Object.keys(styles).length)out.style=styles;
      const children=[];
      for(const child of node.childNodes){const c=walk(child,depth+1);if(c)children.push(c);if(truncated)break;}
      if(children.length)out.children=children;
      return out;
    }
    const tree=walk(target.node,0);
    const answer={schema:"windows-native-page-evidence-v1",version:"windows-dom-1.1.1",
      boundary_verified:!!target.id&&!truncated,
      request_bound:false,complete:!truncated&&!generationActive,
      message_id:target.id,counts:{nodes,text_chars:chars,tables,images},
      probe,root:tree,limitations:["No full-page screenshot","No source image bytes","No API request identity validation"]};
    return {ok:true,answer};
  }
  const result=capturePage();
  if(result?.ok&&result.answer){result.answer.version="windows-hot-1.0.0";result.answer.collector_version=DIAGNOSTIC_TOOL_VERSION;}
  return result;
})()