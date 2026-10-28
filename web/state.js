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
 function createOperations(api,saved,session,onStatus,delay=ms=>new Promise(resolve=>setTimeout(resolve,ms)),newId=()=>crypto.randomUUID()){
  let busy=false,mayForget=false,current=saved.get('pendingOperation');
  const pending=()=>current;
  const remember=value=>{current=value;saved.set('pendingOperation',value);};
  async function recover(){
   if(busy)throw Error('正在读取原检查状态，请稍候。');
   const p=pending();if(!p)return null;
   if(p.session!==session){remember(null);throw Error('服务已重启，原检查不会自动重发；请重新填写密钥并按需手动检查。');}
   busy=true;mayForget=false;
   try{
    while(true){
     onStatus(saved.get('pendingOperation')?.id===p.id?'正在读取原检查进度；刷新页面后仍可继续查看，不会重复调用模型。':'当前浏览器无法保存恢复记录，请保持本页打开；刷新会失去原检查入口。正在读取进度。',false);
     let status;
     try{status=await api('operations/'+p.id);}catch(e){
      mayForget=e.status===404;
      onStatus(mayForget?'当前服务未找到该检查；不会自动重发。可再次查询，或结束等待后手动重新检查。':saved.get('pendingOperation')?.id===p.id?'连接暂时中断。点击“继续查看原检查”或刷新页面；不会重复提交。':'连接暂时中断，且浏览器无法保存恢复记录。请保持本页打开，点击“继续查看原检查”；不要刷新。',mayForget);
      throw e;
     }
     if(status.state==='completed')return {...p,result:status.result};
     if(status.state==='failed'){remember(null);throw Error(status.message+(status.diagnostic_id?' 诊断编号：'+status.diagnostic_id:''));}
     if(status.state!=='running')throw Error('无法识别检查状态，请保留当前页面记录。');
     await delay(1200);
    }
   }finally{busy=false;}
  }
  return {pending,recover,
   acknowledge(id){if(current?.id===id)remember(null);},
   forget(){if(!mayForget)throw Error('请先查询原检查状态，不能取消仍在运行的检查。');remember(null);mayForget=false;},
   async start(kind,path,body,profile){
    if(pending())throw Error('上次检查结果尚未确认，请先继续查看原检查。');
    const p={id:newId(),kind,profile,session};remember(p);
    try{await api(path,body,'POST',{'X-Operation-ID':p.id});}
    catch(e){if(e.status>=400&&e.status<500){remember(null);throw e;}}
    return recover();
   }
  };
 }
 const exports={createState,runActivity,matchesFrozenModel,createOperations};
 if(typeof module!=='undefined')module.exports=exports;else root.RagState=exports;
})(typeof window!=='undefined'?window:globalThis);
