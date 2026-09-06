const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('assets/static/js/app.js','utf8');
const extract = (start,end) => source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
let requests=0, release;
const context = vm.createContext({state:{bootstrap:{runtime:{state:'idle'},history:{items:[{wins:2,losses:1,draws:1}]}}},document:{hidden:true},fetchJSON:async()=>{requests++;return new Promise(resolve=>{release=resolve})}});
vm.runInContext(extract('async function refreshRuntimeState()', '\nfunction historySignature'),context);
vm.runInContext(extract('function getHistorySummary()', '\nfunction getFilteredHistoryItems'),context);
(async()=>{
 await context.refreshRuntimeState(); assert.equal(requests,0);
 context.document.hidden=false;
 const pending=context.refreshRuntimeState();
 await context.refreshRuntimeState(); assert.equal(requests,1);
 release({ok:false}); await pending; assert.equal(context.state.runtimePollBusy,false);
 const summary=context.getHistorySummary(); assert.equal(summary.total_matches,4); assert.equal(summary.draws,1); assert.equal(summary.win_rate,50);
 console.log('panel polling: 6 passed, 0 failed');
})().catch(e=>{console.error(e);process.exitCode=1});
