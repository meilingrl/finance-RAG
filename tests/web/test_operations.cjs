const test=require('node:test'),assert=require('node:assert/strict');
const {createOperations}=require('../../web/state.js');
function storage(){const data={};return {get:k=>data[k],set:(k,v)=>{data[k]=v;}};}
test('lost POST response and refresh query the same operation without another POST',async()=>{
 const saved=storage(),calls=[];let outage=true;
 const api=async(path,body)=>{calls.push({path,body});if(body)throw Error('lost response');if(outage)throw Error('offline');return {state:'completed',result:{ready:true}};};
 let client=createOperations(api,saved,'session',()=>{},async()=>{},()=> 'operation');
 await assert.rejects(client.start('prepare','experiments/prepare',{credential_ref:'private-ref'},'profile'));
 assert.equal(saved.get('pendingOperation').id,'operation');
 assert.equal(JSON.stringify(saved.get('pendingOperation')).includes('private-ref'),false);
 outage=false;client=createOperations(api,saved,'session',()=>{},async()=>{},()=> 'different');
 const result=await client.recover();assert.equal(result.result.ready,true);
 assert.equal(calls.filter(c=>c.body).length,1);assert.ok(saved.get('pendingOperation'));
 client.acknowledge(result.id);assert.equal(saved.get('pendingOperation'),null);
});
test('running operations poll status and a new service session never replays work',async()=>{
 const saved=storage();saved.set('pendingOperation',{id:'op',kind:'check',profile:'profile',session:'old'});
 let calls=0;const client=createOperations(async()=>{calls++;},saved,'new',()=>{},async()=>{});
 await assert.rejects(client.recover(),/重启/);assert.equal(calls,0);
 assert.equal(saved.get('pendingOperation'),null);
});
test('unknown submission remains pending until an explicit discard',async()=>{
 const saved=storage();saved.set('pendingOperation',{id:'op',kind:'check',session:'same'});
 const client=createOperations(async()=>{throw Object.assign(Error('missing'),{status:404});},saved,'same',()=>{},async()=>{});
 await assert.rejects(client.recover());assert.ok(saved.get('pendingOperation'));
 client.forget();assert.equal(saved.get('pendingOperation'),null);
});
test('browser storage unavailable still retains the in-flight operation in memory',async()=>{
 const saved={get:()=>null,set:()=>{}};
 const client=createOperations(async(path,body)=>body?{state:'running'}:{state:'completed',result:{ready:true}},saved,'same',()=>{},async()=>{},()=> 'op');
 const result=await client.start('check','provider/check',{},'profile');
 assert.equal(result.result.ready,true);
});
test('network failure never recommends refresh when the operation cannot be persisted',async()=>{
 const saved={get:()=>null,set:()=>{}};let message='';
 const client=createOperations(async(path,body)=>{if(body)return {state:'running'};throw Error('offline');},saved,'same',text=>{message=text;},async()=>{},()=> 'op');
 await assert.rejects(client.start('check','provider/check',{},'profile'));
 assert.ok(client.pending());assert.match(message,/保持本页/);assert.doesNotMatch(message,/或刷新页面/);
});
