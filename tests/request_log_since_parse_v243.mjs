import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("app/request_history_v94_patch.py","utf8");
const start=source.indexOf("function requestHistoryParseExportSince(value){");
const end=source.indexOf("\nfunction requestHistoryEnsureControls(){",start);
assert.ok(start>0 && end>start,"timestamp parser must be in canonical admin asset");
const context={Date};
vm.runInNewContext(source.slice(start,end)+"\nthis.parse=requestHistoryParseExportSince;",context);
const parse=context.parse;
function parsed(source, iso, assumed){
  const output=parse(source);
  assert.ok(output,"expected parsed timestamp for "+source);
  assert.equal(output.iso,iso,source);
  assert.equal(output.assumedBeijing,assumed,source);
}
parsed("2026-10-29 10:39:31","2026-10-29T02:39:31.000Z",true);
parsed("2026-10-29T11:28:29+08:00","2026-10-29T03:28:29.000Z",false);
parsed("2026-10-29T11:28:29+0800","2026-10-29T03:28:29.000Z",false);
parsed("2026-10-29 11:28:29+08:00","2026-10-29T03:28:29.000Z",false);
parsed("2026-10-29T03:28:29Z","2026-10-29T03:28:29.000Z",false);
parsed("2026-10-29T11:28:29-07:00","2026-10-29T18:28:29.000Z",false);
parsed(" 2026-10-29 10:39:31 ","2026-10-29T02:39:31.000Z",true);
parsed("2024-02-29 00:00:00","2024-02-28T16:00:00.000Z",true);
parsed("2026-10-29T11:28:29.123+08:00","2026-10-29T03:28:29.123Z",false);
for(const value of [
  "", "hello", "2026-02-29 10:39:31", "2026-04-31 10:39:31",
  "2026-10-29 24:01:00", "2026-10-29 10:60:00",
  "2026-10-29 10:39:60", "2026-10-29 10:39:31+15:00",
  "2026-10-29 10:39:31+14:01", "2026-10-29 10:39:31 GMT+8",
  "2026/10/29 10:39:31", "2026-10-29 10:39:31<script>"
]) assert.equal(parse(value),null,"must reject "+value);
console.log("v243 timestamp paste parser: Beijing naive and explicit zone cases passed");
