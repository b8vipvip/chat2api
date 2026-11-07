import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("app/request_history_v94_patch.py","utf8");
const start=source.indexOf("function requestHistoryParseExportSince(value){");
const end=source.indexOf("\nfunction requestHistoryEnsureControls(){",start);
assert.ok(start>=0&&end>start);
function filter({time="",current=false,q="",status="",source=""}={}){
  const inputs={
    rqSearch:{value:q},rqStatus:{value:status},rqSource:{value:source},
    rqBatchExportSince:{value:time},rqBatchExportCurrentVersion:{checked:current},
  };
  const context={document:{getElementById:id=>inputs[id]||null},URLSearchParams,Date};
  vm.runInNewContext(source.slice(start,end)+"\nthis.build=requestHistoryFilterParams;",context);
  return context.build();
}
const expected="2026-10-30T03:56:59.000Z";
assert.equal(filter({time:"2026-10-30 11:56:59"}).get("since"),expected);
assert.equal(filter({time:"2026-10-30T11:56:59+08:00"}).get("since"),expected);
const intersection=filter({time:"2026-10-30 11:56:59",current:true,status:"error",q:"gpt-6",source:"playground"});
assert.equal(intersection.get("since"),expected);
assert.equal(intersection.get("current_version_only"),"true");
assert.equal(intersection.get("status"),"error");
assert.equal(intersection.get("model"),"gpt-6");
assert.equal(intersection.get("q"),null);
assert.equal(intersection.get("source"),"playground");
const plain=filter({q:"bot2",source:"external"});
assert.equal(plain.get("q"),"bot2");
assert.equal(plain.get("model"),null);
assert.equal(plain.get("source"),"external");
assert.equal(filter({q:"model:gpt-5.6-sol"}).get("model"),"gpt-5.6-sol");
assert.equal(filter().has("since"),false);
assert.ok(!source.includes("since.addEventListener('click',openPicker)"),
  "Text field click must not be intercepted by the calendar");
assert.match(source,/calendar\.addEventListener\('click',openPicker\)/);
assert.match(source,/since\.type='text'/);
assert.match(source,/let requestHistoryLatestLoad=0/);
assert.match(source,/if\(sequence!==requestHistoryLatestLoad\)return/);
assert.match(source,/if\(parsed && applied\?\.since\)/);
assert.match(source,/time<Date\.parse\(parsed\.iso\)/);
console.log("Admin time paste, explicit picker, shared list/ZIP predicate and stale-query guard passed");
