const assert=require('node:assert/strict');
const {createState}=require('../../web/state.js');
const memory=()=>{const data=new Map();return {getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)}};
const disk=memory(),tab=memory();
let state=createState(disk,tab,'workspace-a');
state.saveDemo({resource:'r',question:'custom?',doc:'d',retriever:'minilm',e2:true,e3:false,e4:true,api_key:'NEVER'});
state.saveCredential('server-a','opaque-ref');
state=createState(disk,tab,'workspace-a');
assert.equal(state.credential('server-a'),'opaque-ref');
assert.equal(state.credential('server-b'),null);
assert.equal(state.demo('r').question,'custom?');
assert.equal(state.demo('r').e4,true);
assert.equal(state.demo('other'),null);
assert.equal(state.demo('r').api_key,undefined);
assert.equal(createState(disk,tab,'workspace-b').demo('r'),null);
console.log('PASS refresh, restart, workspace isolation, resource binding, credential exclusion');

assert.equal(createState(null,null,'a').credential('server'),null);
createState(null,null,'a').saveDemo({question:'safe'});
