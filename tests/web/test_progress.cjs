const assert=require('node:assert/strict');const {runActivity}=require('../../web/state.js');
const t='2026-10-26T03:00:00Z';
let events=[{time:t,event_type:'preparing_20_of_100',phase:'prepare',counts:{completed_local_count:17}}];
let state={state:'running',phase:'prepare',completed_logical_count:19,in_flight_count:0};
let v=runActivity(state,events,Date.parse(t)+1000);assert.equal(v.prepared,20);assert.equal(v.preparationTotal,100);assert.equal(v.saved,36);assert.equal(v.stalled,false);
v=runActivity(state,events,Date.parse(t)+180000);assert.equal(v.stalled,true);
events.push({time:t,event_type:'concurrency_4',phase:'generation',counts:{completed_local_count:17}});v=runActivity({...state,phase:'generation'},events,Date.parse(t));assert.equal(v.concurrency,4);assert.equal(v.phase,'generation');
console.log('PASS preparation progress, saved count, stale warning, concurrency');
