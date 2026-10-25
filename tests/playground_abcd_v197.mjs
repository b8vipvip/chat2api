import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

const source=readFileSync(new URL("../app/admin_playground_abcd_v197.js",import.meta.url),"utf8");
const FULL = "## 开店\\n" + "咖啡运营".repeat(900) + 
  "\\n| 类别 | 数值 |\\n|---|---|\\n| A | 1 |\\n| 类别 | 数值 |\\n|---|---|\\n| B | 2 |" +
  "\\n\\\\(100+200=300\\\\)\\n" + String.fromCharCode(96).repeat(3) +
  "js\\nconsole.log(1)\\n" + String.fromCharCode(96).repeat(3) +
  "\\n落地执行清单：指定负责人与期限。";
const IMG_LINK=i=>"![插画](https://api.test/api/media/med_1234567890/abc"+i+")";
const FILE_LINK=(ext,i)=>"[📎 资料"+i+"."+ext+"](https://api.test/api/generated-files/gen_abc"+i+"/token"+i+")";
const frames=(text,includeDone=true)=>
"data: "+JSON.stringify({choices:[{index:0,delta:{role:"assistant"},finish_reason:null}]})+"\n\n"+
"data: "+JSON.stringify({choices:[{index:0,delta:{chat2api_status:{text:"正在分析"}},finish_reason:null}]})+"\n\n"+
"data: "+JSON.stringify({choices:[{index:0,delta:{content:text},finish_reason:null}]})+"\n\n"+
"data: "+JSON.stringify({choices:[{index:0,delta:{},finish_reason:"stop"}]})+"\n\n"+
(includeDone?"data: [DONE]\n\n":"");

function scenario(id, outputs) {
  const elements=new Map(), requests=[], records=[],downloads=[];
  const element=key=>{
    if(!elements.has(key))elements.set(key,{
      value:"",disabled:false,textContent:"",style:{},handlers:{},
      addEventListener(name,callback){this.handlers[name]=callback},
      get selectedOptions(){return [{dataset:{keyName:"test"}}]}
    });
    return elements.get(key);
  };
  element("pgChatModel").value="default";
  element("pgChatKeyInput").value="sk-private-test";
  element("pgABCDSelect").value=id;
  const host={querySelector(){return {after(){}}}};
  let uuid=0, postIndex=0, outputIndex=0;
  const document={
    getElementById:key=>key==="playgroundChatPanel"?host:(key==="pgABCD"&&!elements.has(key)?null:element(key)),
    createElement(){return {style:{},innerHTML:"",querySelector(){return {after(){}}}}}
  };
  const fetch=async (url, options={})=>{
    if(url==="/v1/chat/completions"){
      const body=JSON.parse(options.body);
      requests.push({url,body,headers:options.headers});
      const text=outputs[Math.min(postIndex++,outputs.length-1)];
      const data=typeof text==="string"?frames(text):frames(text.text,text.done!==false);
      return {ok:true,body:{getReader(){
        let read=false;
        return {read:async()=>read?{done:true}:(read=true,{done:false,value:new TextEncoder().encode(data)}),
          cancel:async()=>{}};
      }}};
    }
    downloads.push(String(url));
    const ext=String(url).includes("gen_abc")?(id==="D"?"md":["xlsx","pptx","json","zip"][outputIndex++ % 4]):"image";
    let bytes;
    if(ext==="image")bytes=Uint8Array.from([137,80,78,71,13,10,26,10,...Array(25).fill(1)]);
    else if(ext==="json")bytes=new TextEncoder().encode(JSON.stringify({schedule:"full",events:[1,2,3]}));
    else if(ext==="md")bytes=new TextEncoder().encode("# Characters\nDetails of the heroes.");
    else bytes=Uint8Array.from([80,75,3,4,...Array(25).fill(1)]);
    return {ok:true,arrayBuffer:async()=>bytes.buffer};
  };
  const api=async (endpoint,options)=>{
    if(endpoint.endsWith("/chat-runs"))records.push({kind:"start",body:options.body});
    if(endpoint.endsWith("/chat-records"))records.push({kind:"terminal",body:options.body});
    return {};
  };
  const context={
    document,fetch,api,crypto:{randomUUID:()=>("00000000-0000-0000-0000-"+String(++uuid).padStart(12,"0"))},
    TextDecoder,TextEncoder,performance:{now:()=>1234},location:{origin:"https://api.test"},
    console,setTimeout:()=>0,Blob,URL
  };
  vm.runInNewContext(source,context);
  return {run:async()=>{
    await element("pgABCDRun").handlers.click();
    return {status:element("pgABCDNote").textContent,requests,records,downloads};
  }, element};
}

test("A: real SSE terminal and full-length report is accepted",async()=>{
  const r=await scenario("A",[FULL]).run();
  assert.match(r.status,/验收通过/);
  assert.equal(r.requests.length,1);
  assert.equal(r.requests[0].body.stream,true);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"passed");
});
test("Incomplete SSE without [DONE] must fail even with full content",async()=>{
  const r=await scenario("A",[{text:FULL,done:false}]).run();
  assert.match(r.status,/未通过/);
  assert.equal(r.records.find(x=>x.kind==="terminal").body.status,"failed");
});
test("B: three distinct image files are fetched",async()=>{
  const r=await scenario("B",[IMG_LINK(1)+"\n"+IMG_LINK(2)+"\n"+IMG_LINK(3)]).run();
  assert.match(r.status,/验收通过/);
  assert.equal(r.downloads.length,3);
});
test("C: XLSX PPTX JSON ZIP links require real file downloads",async()=>{
  const r=await scenario("C",[["xlsx","pptx","json","zip"].map(FILE_LINK).join("\n")]).run();
  assert.match(r.status,/验收通过/);
  assert.equal(r.downloads.length,4);
});
test("D: second turn keeps prior messages",async()=>{
  const r=await scenario("D",[FILE_LINK("md",0)+"\n"+"钟楼故事".repeat(200),"雾港钟声人物".repeat(200)]).run();
  assert.match(r.status,/验收通过/);
  assert.equal(r.requests.length,2);
  assert.equal(r.requests[1].body.messages.length,3);
  assert.equal(r.records.filter(x=>x.kind==="terminal").length,2);
});
