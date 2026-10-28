const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../../web/app.js'),'utf8');
function setup(responses){
 const nodes={},timers=new Map();let next=0;
 const context={api:async()=>{const result=responses.shift();if(result instanceof Error)throw result;return result;},
  $:id=>nodes[id]??={},fixedDataStatus:null,resource:'selected',pendingExperimentPreparation:false,
  dataTimer:null,dataFailures:0,dataRefreshing:false,clearTimeout:id=>timers.delete(id),setTimeout:fn=>{timers.set(++next,fn);return next;}};
 vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('async function refreshFixedData(){'),source.indexOf("bind('dataRetry'")),context);
 return {context,nodes,timers};
}
test('a transient data-status failure keeps a visible retry and resumes polling',async()=>{
 const {context,nodes,timers}=setup([{ready:false,state:'preparing'},new Error('temporary outage'),{ready:true,state:'ready'}]);
 await context.refreshFixedData();
 let [id,callback]=[...timers][0];timers.delete(id);await callback();
 assert.equal(nodes.dataRetry.hidden,false);
 assert.equal(timers.size,1);
 [id,callback]=[...timers][0];timers.delete(id);await callback();
 assert.equal(nodes.reviewed.disabled,false);assert.equal(timers.size,0);
});
test('initial data-status failure is contained so other startup recovery can continue',async()=>{
 const {context,nodes,timers}=setup([new Error('temporary outage')]);
 await context.refreshFixedData();
 assert.equal(nodes.dataRetry.hidden,false);assert.equal(timers.size,1);
});
