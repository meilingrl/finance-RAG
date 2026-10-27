/* Persist only an explicit allowlist; API keys never enter browser storage. */
(function(root){
 function createState(local,tab,workspace){
  const prefix='rag:'+workspace+':';
  const read=(storage,key)=>{try{return JSON.parse(storage.getItem(prefix+key));}catch{return null;}};
  const write=(storage,key,value)=>{try{storage.setItem(prefix+key,JSON.stringify(value));}catch{}};
  return {
   get:key=>read(local,key),set:(key,value)=>write(local,key,value),
   saveDemo(value){const safe={};for(const k of ['resource','question','doc','example','retriever','e2','e3','e4'])safe[k]=value[k];write(local,'demo',safe);},
   demo(resource){const saved=read(local,'demo');return saved?.resource===resource?saved:null;},
   saveCredential(session,ref){write(tab,'credential',{session,ref});},
   credential(session){const saved=read(tab,'credential');return saved?.session===session?saved.ref:null;}
  };
 }
 function runActivity(state,events,now=Date.now()){
  let prepared=0,preparationTotal=0,concurrency=null,lastSaved=null,local=0;
  for(const e of events){
   const p=/^preparing_(\d+)_of_(\d+)$/.exec(e.event_type),c=/^concurrency_(\d+)$/.exec(e.event_type);
   if(p){prepared=Number(p[1]);preparationTotal=Number(p[2]);}
   if(c)concurrency=Number(c[1]);
   if(e.event_type==='logical_unit_completed')lastSaved=e.time;
   if(Number.isInteger(e.counts?.completed_local_count))local=e.counts.completed_local_count;
  }
  const last=events.at(-1),age=last?Math.max(0,(now-Date.parse(last.time))/1000):null;
  return {phase:state.phase,prepared,preparationTotal,concurrency,lastSaved,lastProgress:last?.time||null,saved:state.completed_logical_count+local,stalled:state.state==='running'&&(age===null||age>150)};
 }
 function matchesFrozenModel(profile,values){
  return profile?.status==='frozen'&&profile.models?.primary===values.model&&
   (profile.models?.secondary||'')===(values.secondary||'')&&profile.provider?.endpoint===values.endpoint&&
   (profile.concurrency??4)===values.concurrency;
 }
 if(typeof module!=='undefined')module.exports={createState,runActivity,matchesFrozenModel};else root.RagState={createState,runActivity,matchesFrozenModel};
})(typeof window!=='undefined'?window:globalThis);
