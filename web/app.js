'use strict';
const $=id=>document.getElementById(id), pretty=x=>JSON.stringify(x,null,2);
const store={get(k){try{return localStorage.getItem(k);}catch{return null;}},set(k,v){try{localStorage.setItem(k,v);}catch{}}};
let session='',credential=null,profiles=[],profile=store.get('rag-profile')||'formal-v1',resource=store.get('rag-resource')||'',run=store.get('rag-run')||'',examples=[],timer=null,answers=[];
let savedState=null,restoring=true,referenceQuestion='',operationClient=null,operationsSupported=false;
let fixedDataStatus=null,preparedExperiment=null,pendingExperimentPreparation=false;
let dataTimer=null,dataFailures=0,dataRefreshing=false;
let eventRun='',eventCursor=0,eventHistory=[];
let syntheticOutput=false,answerPage=0,runKind='';
const el=(tag,props={},...children)=>{const node=Object.assign(document.createElement(tag),props);node.append(...children.filter(c=>c!==null&&c!==undefined));return node;};
const short=id=>id?(id.length>14?id.slice(0,8)+'…':id):'';

// ---------- scenes ----------
// The four areas sit side by side on one track and are never hidden or rebuilt, so
// entered values, results and polling survive navigation. `pos` is a continuous scene
// index: the track, the travel line and each masthead's parallax derive from it, so a
// horizontal trackpad swipe scrubs between scenes and any transition can be reversed.
// Vertical wheel input is never taken over; each scene's content scrolls on its own.
const TABS=['config','demo','experiment','results'];
const scenes=TABS.map(t=>$('scene-'+t)),stage=$('stage'),track=$('track'),viewport=$('viewport');
const narrow=matchMedia('(max-width:860px)'),calm=matchMedia('(prefers-reduced-motion: reduce)');
const EDITABLE='input,textarea,select,[contenteditable]';
const OMEGA=13,COMMIT=.14,QUIET=160;
let pos=0,vel=0,target=0,frame=0,last=0,gesture=null;
const clampIndex=i=>Math.max(0,Math.min(TABS.length-1,i));
function render(){
 if(narrow.matches){track.style.transform='';$('travel').style.transform='';scenes.forEach(s=>s.style.cssText='');return;}
 track.style.transform=`translate3d(${-pos*25}%,0,0)`;
 $('travel').style.transform=`translateX(${pos*100}%)`;
 scenes.forEach((s,i)=>{const d=i-pos;s.style.setProperty('--d',Math.max(-1,Math.min(1,d)).toFixed(4));s.style.setProperty('--a',Math.min(1,Math.abs(d)).toFixed(4));});
}
function tick(now){
 const dt=Math.min(64,now-(last||now));last=now;lastTick=performance.now();
 // Critically damped spring: eases out of rest and keeps its velocity when redirected mid-move.
 if(!gesture||gesture.committed){
  if(calm.matches){pos=target;vel=0;}
  else for(let s=dt/1000;s>0;s-=1/240){const h=Math.min(s,1/240);vel+=(OMEGA*OMEGA*(target-pos)-2*OMEGA*vel)*h;pos+=vel*h;}
  if(Math.abs(target-pos)<.0005&&Math.abs(vel)<.01){pos=target;vel=0;}
 }
 render();
 if(pos!==target||(gesture&&!gesture.committed))frame=requestAnimationFrame(tick);else{frame=0;last=0;stage.classList.remove('moving');}
}
let lastTick=0;
function animate(){
 stage.classList.add('moving');if(!frame)frame=requestAnimationFrame(tick);
 // Background or throttled pages may never deliver a frame; land on the scene instead of stalling.
 const asked=performance.now();setTimeout(()=>{if(frame&&lastTick<asked&&(!gesture||gesture.committed)){cancelAnimationFrame(frame);frame=0;last=0;pos=target;vel=0;render();stage.classList.remove('moving');}},300);
}
function settleOn(i,{focus=false,scroll=true}={}){
 target=clampIndex(i);const name=TABS[target];
 scenes.forEach((s,j)=>{s.inert=j!==target&&!narrow.matches;});
 TABS.forEach((t,j)=>{if(j===target)$('rail-'+t).setAttribute('aria-current','step');else $('rail-'+t).removeAttribute('aria-current');});
 store.set('rag-tab',name);if(location.hash!=='#'+name)history.replaceState(null,'','#'+name);
 if(narrow.matches){if(scroll)scenes[target].scrollIntoView({behavior:calm.matches?'auto':'smooth',block:'start'});}else animate();
 if(focus)$('h-'+name).focus({preventScroll:true});
}
function showTab(name,focus=false){settleOn(TABS.indexOf(name)<0?0:TABS.indexOf(name),{focus});}
// True when some element between node and the track can still scroll horizontally in direction dx.
function scrollsX(node,dx){for(let n=node;n&&n!==track&&n.nodeType===1;n=n.parentElement){const cs=getComputedStyle(n);if(/auto|scroll/.test(cs.overflowX)&&n.scrollWidth>n.clientWidth+1&&(dx>0?n.scrollLeft+n.clientWidth<n.scrollWidth-1:n.scrollLeft>0))return true;}return false;}
stage.addEventListener('wheel',e=>{
 if(narrow.matches||e.ctrlKey)return;
 if(Math.abs(e.deltaX)<=Math.abs(e.deltaY)){
  // A masthead has nothing to scroll; let the wheel read the scene's content instead.
  const mast=e.target.closest?.('.masthead');if(mast&&!gesture){mast.nextElementSibling.scrollTop+=e.deltaY;e.preventDefault();}
  if(gesture)e.preventDefault();
  return;
 }
 const dx=e.deltaX*(e.deltaMode===1?16:1),now=performance.now();
 if(gesture?.committed){
  // After a swipe commits, swallow the rest of it and its momentum so the tail cannot hold or drag the
  // page back mid-flight. Deltas that jump up again once the page has nearly landed are a new swipe.
  const fresh=Math.abs(target-pos)<.15&&Math.abs(dx)>Math.max(6,gesture.last*2);
  gesture.last=Math.abs(dx);clearTimeout(gesture.timer);
  if(!fresh){e.preventDefault();gesture.timer=setTimeout(()=>{gesture=null;},QUIET);return;}
  gesture=null;
 }
 if(!gesture&&(e.target.closest?.(EDITABLE)||scrollsX(e.target,dx)))return;
 e.preventDefault();
 if(!gesture)gesture={start:pos,base:target,timer:0,t:now};
 clearTimeout(gesture.timer);
 // One swipe moves at most one scene; past the ends or that limit, resistance rises.
 const lo=Math.max(0,gesture.base-1),hi=Math.min(TABS.length-1,gesture.base+1),step=dx/viewport.clientWidth;
 let next=pos+step;if(next<lo||next>hi)next=Math.max(lo-.06,Math.min(hi+.06,pos+step*.2));
 const speed=step/Math.max(.008,(now-gesture.t)/1000);gesture.t=now;
 pos=target=next;vel=0;animate();
 const moved=pos-gesture.start,goal=clampIndex(gesture.base+Math.sign(moved));
 if(Math.abs(moved)>COMMIT&&goal!==gesture.base){
  // Past the threshold the page flips on its own, carrying the hand's speed into the spring.
  gesture.committed=true;gesture.last=Math.abs(dx);settleOn(goal);
  vel=Math.max(-6,Math.min(6,speed));
  gesture.timer=setTimeout(()=>{gesture=null;},QUIET);return;
 }
 // Released before the threshold: go on for a clear nudge, otherwise spring back.
 gesture.timer=setTimeout(()=>{const g=gesture;gesture=null;settleOn(Math.abs(pos-g.start)>.06?clampIndex(g.base+Math.sign(pos-g.start)):g.base);},140);
},{passive:false});
document.addEventListener('keydown',e=>{
 if(narrow.matches||e.defaultPrevented||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey)return;
 const d={ArrowRight:1,ArrowLeft:-1}[e.key];
 if(!d||e.target.closest?.(EDITABLE)||scrollsX(e.target,d))return;
 e.preventDefault();settleOn(target+d,{focus:true});
});
document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>showTab(b.dataset.goto,true));
window.onhashchange=()=>showTab(location.hash.slice(1));
narrow.addEventListener('change',()=>{render();settleOn(target,{scroll:false});});
pos=target=clampIndex(TABS.indexOf(location.hash.slice(1)||store.get('rag-tab')||'config'));
settleOn(target,{scroll:location.hash.length>1});render();
function stepStatus(name,text,tone=''){const s=$('status-'+name);s.textContent=text;s.dataset.tone=tone;$('rail-'+name).dataset.tone=tone;$('rail-'+name).title=text;}

// ---------- feedback ----------
const notice=(s,tone='info')=>{$('notice').textContent=s;$('notice').dataset.tone=tone;};
function box(id,text,tone=''){$(id).textContent=text;$(id).dataset.tone=tone;}
const reasonNames={formal_models_invalid:'模型配置无效，请检查主模型与对照模型',formal_model_missing_primary:'请填写并保存主模型名称',formal_model_missing_secondary:'完整实验包含第二模型对照，请填写并保存对照模型',concurrency_not_verified:'请在配置区完成当前模型与并发数的检查',concurrency_invalid:'并发数应为 1–32 的整数',single_check_failed:'单请求检查未通过，并发检查未执行',profile_not_frozen:'正式方案尚未冻结',demo_only_resources:'当前登记的是旧开发材料',resources_not_ready:'正式资源尚未就绪',model_missing:'尚未登记本地模型',reviews_missing:'人工核验尚未完成',credential_required:'请填写并保存本次会话密钥',kernel_unavailable:'执行模块尚未就绪',source_not_sealed:'原运行尚未封存',execution_slot_busy:'已有任务正在运行',model_profile_required:'请选择已保存的自定义模型方案',approved_resources_immutable:'已审核资料不可重建',evaluation_gold_missing:'缺少绑定的评价资料',provider_generation_failed:'模型服务未完成生成，查看调用记录',qualification_source_disjoint:'来源重复检查待完成',qualification_cohorts_validated:'样本分组检查待完成',qualification_inputs_complete:'待补齐完整输入',qualification_development_passed:'开发检查未通过',qualification_validation_passed:'验证检查未通过',qualification_compatibility_passed:'模型兼容检查未通过',qualification_throughput_passed:'吞吐检查未通过',qualification_budget_validated:'调用预算尚未核定',qualification_evidence_missing:'缺少运行检查记录',course_documents_missing:'课程报告、摘要或成员资料尚未准备',formal_preset_required:'只能冻结固定正式方案',preregistered_audit_plan_required:'缺少预注册审核子集',preregistered_audit_plan_invalid:'预注册审核子集未通过校验',budget_exhausted:'调用额度不足',retry_demo_budget_exhausted:'演示与重试额度不足',category_budget_exceeded:'分类额度已超出',budget_check_failed:'调用账本校验失败',frozen_profile_invalid:'冻结配置校验未通过',code_changed:'代码已变化，与冻结身份不符',resources_changed:'资料已变化，与冻结身份不符',profile_changed:'方案已变化，与运行身份不符',inference_projection_unavailable:'推理输入不可用',formal_plan_unavailable:'正式计划不可编译',model_not_in_catalog:'模型不在服务目录中',operational_marker_not_answered:'连通检查未返回预期标记'};
Object.assign(reasonNames,{
 demo_only_resources:'仅供开发演示，不能直接用于正式实验',
 approved_blind_scope_review_missing:'所选 E3/E4 缺少有效的证据范围审核。请登记「E1–E4 开发演示包」，或补齐该资料的审核记录',
 approved_same_range_image_missing:'E3 缺少与文字证据对应且通过 SHA 校验的裁图',
 approved_structure_sha_missing:'E4 缺少与当前证据绑定的已核验结构',
 registered_local_model_missing:'当前资料包未关联 MiniLM。请使用 E1–E4 开发演示包，或改选 BM25',
 demo_assets_invalid_or_unapproved:'演示材料校验失败或审核尚未导入，请重新登记完整演示包并查看资源明细',
 inference_asset_changed:'材料文件已变化，请重新核验受影响的材料',
 local_model_projection_incomplete:'MiniLM 模型登记不完整，请重新登记完整资料包'
});
Object.assign(reasonNames,{provider_capabilities_unverified:'正在准备模型文本、图像与并发能力检查',fixed_data_preparing:'正在自动准备固定资料，请稍候',fixed_data_unavailable:'固定资料下载未完成，请检查网络后重试',vision_check_failed:'模型图像检查未通过，请使用支持图像的模型',http_401:'密钥未被模型服务接受，请重新填写有效密钥',http_403:'当前密钥没有该模型的调用权限',http_404:'模型服务接口不存在，请核对服务地址',http_429:'模型服务限流，请稍后重试或降低并发',model_mismatch:'服务返回的模型与配置不一致，请核对模型名称',response_not_completed:'模型未完成本次回答，请提高输出上限后重试',transport_outcome_unknown:'模型服务连接中断或超时，请检查连接后重试'});
function explain(x){
 if(reasonNames[x])return reasonNames[x];
 if(x.startsWith('vision_'))return '图像能力检查未通过：'+(reasonNames[x.slice(7)]||'所选模型不能完成图像输入');
 if(x.startsWith('secondary_'))return '对照模型检查未通过：'+(reasonNames[x.slice(10)]||'对照模型不能完成本实验所需输入');
 if(x.startsWith('course_'))return '固定资料完整性检查未通过，请重新校验资料或联系提供者';
 return x;
}
const joinReasons=items=>items.map(explain).join('；');
function renderBlocks(id,items,title='还需完成'){
 const target=$(id);if(!items?.length){target.replaceChildren();return;}
 target.replaceChildren(el('p',{className:'blocks-title',textContent:`${title}（${items.length} 项）`}),el('ul',{className:'blocks'},...items.map(x=>el('li',{},el('span',{textContent:explain(x)}),el('code',{textContent:x,hidden:true})))));
}

// ---------- API ----------
async function api(path,body,method='POST',extraHeaders={}) {
 const controller=new AbortController();
 // Old running servers retain their synchronous POST behavior until restarted.
 const timeout=body===undefined||extraHeaders['X-Operation-ID']?setTimeout(()=>controller.abort(),15000):null;
 try{
  const response=await fetch('/api/v1/'+path,{method:body===undefined?'GET':method,signal:controller.signal,headers:{'Content-Type':'application/json','X-Session-ID':session,...extraHeaders},...(body===undefined?{}:{body:JSON.stringify(body)})});
  let data={};try{data=await response.json();}catch{}
  if(data.code==='invalid_session'){credential=null;throw Object.assign(Error('服务已重启，请刷新页面恢复配置；密钥需重新填写。'),{status:403});}
  if(!response.ok)throw Object.assign(Error(data.detail||data.message||data.code||'请求未完成（'+response.status+'）'),{status:response.status});return data;
 }finally{clearTimeout(timeout);}
}
function bind(id,fn){$(id).onclick=async()=>{const button=$(id);button.disabled=true;try{await fn();}catch(e){notice(String(e.message),'error');}finally{button.disabled=false;if(id==='experiment')button.disabled=!experimentReady;}};}
function request(kind,canonical=false){
 if(kind==='experiment'&&canonical&&!window.RagState.matchesFrozenModel(profiles.find(p=>p.profile_id==='formal-v1'),{model:$('model').value.trim(),secondary:$('secondary').value.trim(),endpoint:$('endpoint').value.trim(),concurrency:Number($('concurrency').value)}))throw Error('当前模型或并发配置与冻结方案不同，请先保存、检查并重新冻结。');
 return {schema_version:1,client_request_id:crypto.randomUUID(),kind,profile_id:canonical?'formal-v1':profile,resource_set_id:resource,source_run_id:kind==='verify'?run:null,demo_input:kind==='demo'?{doc_id:$('doc').value.trim(),question:$('question').value.trim(),retriever:$('retriever').value,groups:['E1',...['e2','e3','e4'].filter(id=>$(id).checked).map(id=>id.toUpperCase())]}:null};}

// ---------- 1 配置与资源 ----------
function showProfile(){const current=profiles.find(p=>p.profile_id===profile);$('profiles').value=profile;const status=current?.status||'draft';$('profile').textContent=profile==='formal-v1'?`固定正式方案 · ${status}；开发 E1 无需密钥。`:`当前模型方案：${profile} · ${status}`;$('ctxProfile').textContent=(profile==='formal-v1'?'formal-v1':current?.models?.primary||short(profile))+' · '+status;}
async function refreshProfiles(){const caps=await api('capabilities');profiles=caps.profiles;$('profiles').replaceChildren(...profiles.map(p=>el('option',{value:p.profile_id,textContent:(p.profile_id==='formal-v1'?'固定正式方案':p.models?.primary||'自定义')+' · '+p.status+' · '+p.profile_id})));if(!profiles.some(p=>p.profile_id===profile))profile='formal-v1';showProfile();return caps;}
function fillProfile(){const p=profiles.find(x=>x.profile_id===profile);const v=p?.provider||{};$('providerApiStyle').value=v.api_style||'';$('maxOutput').value=v.max_output_tokens||2048;$('providerTimeout').value=v.timeout_seconds||120;$('reasoningEffort').value=v.reasoning_effort||v.reasoning?.effort||'';$('responseFormat').value=v.response_format||'auto';$('providerStream').checked=v.stream!==false;$('concurrency').value=p?.concurrency??4;if(p?.provider){$('endpoint').value=p.provider.endpoint||'';$('model').value=p.models?.primary||'';$('secondary').value=p.models?.secondary||'';}}
$('profiles').onchange=()=>{profile=$('profiles').value;store.set('rag-profile',profile);savedState?.set('profile',profile);fillProfile();showProfile();preparedExperiment=null;experimentReady=false;$('experiment').disabled=true;};
function resourceSummary(report){
 if(report.resources[0]?.registered_root)$('resourcePath').value=report.resources[0].registered_root;
 $('resourceDetails').textContent=pretty(report);
 const pages=new Set(examples.map(q=>q.doc_id)).size,demoReady=report.ready_for.includes('demo');
 $('resources').replaceChildren(el('strong',{textContent:demoReady?`文件校验通过 · ${examples.length} 个开发问题 · ${pages} 份财报`:'资源待处理'}));
 $('resources').dataset.tone=demoReady?'ok':'warn';
 const blocks=report.blocking_items||[];
 if(blocks.length)$('resources').append(el('br'),demoReady?'正式运行缺项：':'待解决：',joinReasons(blocks));
 else if(demoReady)$('resources').append(el('br'),'正式运行缺项：无');
 $('ctxResource').textContent=short(resource);
 stepStatus('config',demoReady?`资料已登记 · ${examples.length} 题`:'资料待处理',demoReady?'ok':'warn');
}
async function loadExamples(){
 examples=await api('resources/'+encodeURIComponent(resource)+'/examples');
 $('examples').replaceChildren(...examples.map((q,i)=>el('option',{value:i,textContent:q.question})));
 $('demoEmpty').hidden=examples.length>0;
 stepStatus('demo',examples.length?'可运行 E1':'没有可公开的开发问题',examples.length?'':'warn');
 if(examples.length)selectExample();else notice('该资源没有可公开的开发问题','warn');
}
function selectExample(){const q=examples[Number($('examples').value)];if(q){$('question').value=q.question;$('doc').value=q.doc_id;}}
function syncResultContext(){const different=referenceQuestion&&referenceQuestion.trim()!==$('question').value.trim();$('resultContext').hidden=!different;$('resultContext').textContent=different?'上次运行的题目：'+referenceQuestion:'';}
function saveDemoState(){syncResultContext();if(restoring||!savedState)return;savedState.saveDemo({resource,question:$('question').value,doc:$('doc').value,example:$('examples').value,retriever:$('retriever').value,e2:$('e2').checked,e3:$('e3').checked,e4:$('e4').checked});}
function restoreDemoState(){const d=savedState?.demo(resource);if(!d)return;for(const id of ['question','doc','retriever'])if(typeof d[id]==='string')$(id).value=d[id];if(d.example!==undefined)$('examples').value=d.example;for(const id of ['e2','e3','e4'])$(id).checked=d[id]===true;}
$('examples').onchange=()=>{selectExample();saveDemoState();};
for(const id of ['question','doc','retriever','e2','e3','e4'])$(id).addEventListener('input',saveDemoState);

async function register(path,review_policy){const report=await api('resources/register',{root_path:path,...(review_policy?{review_policy}:{})});resource=report.resource_set_id;store.set('rag-resource',resource);savedState?.set('resource',resource);await loadExamples();resourceSummary(report);notice('资源已登记，文件已核对；开发资料可用于演示，正式缺项见校验明细。下一步：在「单题演示」运行一题。','ok');}
bind('saveWorkspace',async()=>{const result=await api('settings',{output_directory:$('outputDirectory').value.trim()},'PUT');$('workspaceStatus').textContent=result.restart_required?'已保存，下次启动后生效；当前任务与原结果保留在原工作区。':'当前已使用该目录';notice($('workspaceStatus').textContent,'ok');});
bind('register',()=>register($('resourcePath').value));
bind('legacy',async()=>register((await api('demo-defaults')).legacy_root));
bind('reviewed',async()=>{if(!fixedDataStatus?.ready)throw Error('固定资料正在准备，请稍候。');await register(fixedDataStatus.demo_root);});
bind('prepared',async()=>{if(!fixedDataStatus?.ready)throw Error('固定资料正在准备，请稍候。');await register(fixedDataStatus.formal_root);});
bind('prepare',()=>start(request('prepare'),'prepareBlocks'));
bind('importReviews',async()=>{if(!resource)throw Error('请先登记对应资源');const report=await api('resources/'+resource+'/reviews',JSON.parse($('reviewManifest').value));resourceSummary(report);notice('已校验并导入审核记录。记录与资源 SHA 绑定，其他门槛仍单独检查。','ok');});
async function saveModel(){
 if(operationClient?.pending())throw Error('上次检查结果尚未确认，请先继续查看原检查。');
 experimentReady=false;preparedExperiment=null;$('experiment').disabled=true;
 const endpoint=$('endpoint').value.trim(),model=$('model').value.trim();if(!endpoint||!model)throw Error('请填写实际接口完整地址和主模型名称');
 const concurrency=Number($('concurrency').value);if(!Number.isInteger(concurrency)||concurrency<1||concurrency>32)throw Error('正式实验并发数应为 1–32 的整数');
 await api('settings',{base_url:endpoint,model,secondary_model:$('secondary').value.trim(),timeout_seconds:120},'PUT');
 const provider={endpoint,max_output_tokens:Number($('maxOutput').value),reserve_input_tokens:131072,timeout_seconds:Number($('providerTimeout').value),stream:$('providerStream').checked,response_format:$('responseFormat').value};
 if($('providerApiStyle').value)provider.api_style=$('providerApiStyle').value;
 if($('reasoningEffort').value.trim())provider.reasoning_effort=$('reasoningEffort').value.trim();
 const values={concurrency,models:{primary:model,secondary:$('secondary').value.trim()||null},provider};
 const current=profiles.find(p=>p.profile_id===profile);
 const adopted=profile==='formal-v1'||current?.status==='frozen'?await api('profiles/'+profile+'/clone',values):await api('profiles/'+profile,values,'PUT');profile=adopted.profile_id;store.set('rag-profile',profile);savedState?.set('profile',profile);
 if($('key').value){const result=await api('credentials/session',{api_key:$('key').value});credential=result.credential_ref;savedState?.saveCredential(session,credential);$('key').value='';$('key').placeholder='本次会话已保存密钥';}
 await refreshProfiles();fillProfile();return adopted;
}
bind('save',async()=>{await saveModel();notice('模型方案已保存；单题串行，正式实验使用配置的并发数。','ok');});
const modelControls=['save','check','preflight','endpoint','model','secondary','concurrency','profiles','key','providerApiStyle','maxOutput','providerTimeout','reasoningEffort','responseFormat','providerStream'];
function lockModel(locked){for(const id of modelControls)$(id).disabled=locked;}
function operationStatus(message,missing=false){
 $('operationStatus').textContent=message;$('recoverOperation').hidden=!operationClient?.pending();$('forgetOperation').hidden=!missing;
}
async function applyCheckReport(report){
 const count=report.parallel.concurrency;
 const text=`文本${report.single.ready?'通过':'未通过'}；图像${report.vision?.ready?'通过':'未通过'}；${count} 路并发 ${report.parallel.success_count}/${count} 成功；实际调用 ${report.sent_attempts} 次。`;
 if(report.ready&&report.effective_provider){await api('profiles/'+profile,{provider:report.effective_provider},'PUT');await refreshProfiles();fillProfile();}
 $('providerCheckStatus').textContent=text;$('providerCheckDetails').textContent=pretty(report);
 notice(text+(report.ready?' 配置检查通过。':' '+joinReasons(report.blocking_items)),report.ready?'ok':'warn');
}
async function applyOperation(outcome){
 if(!outcome)return;
 profile=outcome.profile;store.set('rag-profile',profile);savedState?.set('profile',profile);
 if(outcome.kind==='prepare'&&outcome.result.state==='preparing_data')operationClient.acknowledge(outcome.id);
 if(outcome.kind==='check')await applyCheckReport(outcome.result);else await applyPreparation(outcome.result);
 operationClient.acknowledge(outcome.id);operationStatus('检查已完成。');
}
async function recoverOperation(){
 lockModel(true);
 try{await applyOperation(await operationClient.recover());}
 finally{lockModel(false);$('recoverOperation').hidden=!operationClient.pending();}
}
bind('recoverOperation',recoverOperation);
bind('forgetOperation',()=>{operationClient.forget();operationStatus('已结束等待；没有重新提交或取消后台任务。');});
bind('check',async()=>{
 lockModel(true);
 try{
  const adopted=await saveModel(),count=adopted.concurrency;
  $('providerCheckStatus').textContent=`检查中：先测单请求，再测 ${count} 路并发…`;
  const values={settings:{...adopted.provider,model:adopted.models.primary,concurrency:count,require_vision:true},credential_ref:credential,level:'compatibility'};
  if(operationsSupported)await applyOperation(await operationClient.start('check','provider/check',values,profile));
  else await applyCheckReport(await api('provider/check',values));
 }finally{lockModel(false);$('recoverOperation').hidden=!operationClient?.pending();}
});
for(const id of ['endpoint','model','secondary','concurrency','providerApiStyle','maxOutput','providerTimeout','reasoningEffort','responseFormat','providerStream'])$(id).addEventListener('input',()=>{experimentReady=false;preparedExperiment=null;$('experiment').disabled=true;$('providerCheckStatus').textContent='配置已修改，请保存并重新检查；单题演示仍使用串行。';});

// ---------- 3 正式实验 ----------
let experimentReady=false;
function renderPreflight(p){
 $('preflightDetails').textContent=pretty(p);
 if(p.budget_remaining!==undefined)$('budgetRemaining').textContent=Number(p.budget_remaining).toLocaleString();
 if(p.attempt_limit!==undefined)$('plannedAttemptLimit').textContent=Number(p.attempt_limit).toLocaleString();
 if(p.estimated_logical_generations!==undefined)$('plannedGenerations').textContent=Number(p.estimated_logical_generations).toLocaleString();
 box('preflightResult',p.ready?`预检通过，预计新增 ${p.estimated_additional_attempts} 次模型调用。`:`尚未就绪，还有 ${p.blocking_items.length} 项需要完成。`,p.ready?'ok':'warn');
 renderBlocks('preflightBlocks',p.blocking_items);
}
bind('freeze',async()=>{const result=await api('profiles/formal-v1/freeze',{resource_set_id:resource,model_profile_id:profile});notice(result.frozen?'正式配置已冻结；下一步运行预检。':'尚不能冻结：'+joinReasons(result.blocking_items),result.frozen?'ok':'warn');box('preflightResult',result.frozen?'正式配置已冻结；下一步：正式预检。':`尚不能冻结，还有 ${result.blocking_items.length} 项需要完成。`,result.frozen?'ok':'warn');renderBlocks('preflightBlocks',result.blocking_items);$('preflightDetails').textContent=pretty(result);await refreshProfiles();});
async function start(q,blocksId){
 if(savedState?.get('pendingRequest'))throw Error('上次提交结果尚未确认，请刷新恢复运行后再操作，避免重复启动。');
 const pre=await api('runs/preflight',q);
 if(q.kind==='experiment')renderPreflight(pre);else renderBlocks(blocksId,pre.blocking_items,'运行前需解决');
 if(!pre.ready)throw Error('待解决：'+joinReasons(pre.blocking_items));
 savedState?.set('pendingPayload',q);savedState?.set('pendingRequest',q.client_request_id);$('recoverSubmission').hidden=false;
 const state=await api('runs',{request:q,credential_ref:credential});savedState?.set('pendingRequest',null);savedState?.set('pendingPayload',null);$('recoverSubmission').hidden=true;run=state.run_id;store.set('rag-run',run);savedState?.set('run',run);$('runId').value=run;
 if(q.kind==='demo'){referenceQuestion='';syncResultContext();$('demoReference').replaceChildren();$('demoAnswers').replaceChildren(el('p',{className:'hint',textContent:'等待本次运行结果…'}));}
 await refresh();
}
bind('demo',async()=>{$('demoStatus').textContent='预检中…';try{await start(request('demo'),'demoBlocks');}catch(e){$('demoStatus').textContent='';throw e;}});
async function prepareExperiment(){
 lockModel(true);
 try{
  await saveModel();
  box('preflightResult','正在检查固定资料、模型文本与图像能力及并发，并冻结本次配置…','run');
  $('preflightBlocks').replaceChildren();
  const values={model_profile_id:profile,credential_ref:credential};
  if(operationsSupported)await applyOperation(await operationClient.start('prepare','experiments/prepare',values,profile));
  else await applyPreparation(await api('experiments/prepare',values));
 }finally{lockModel(false);$('recoverOperation').hidden=!operationClient?.pending();}
}
async function applyPreparation(p){
 if(p.state==='preparing_data'){
  pendingExperimentPreparation=true;await refreshFixedData();
  notice('固定资料正在自动准备，下载完成后会继续检查。');return;
 }
 if(p.resource_set_id){resource=p.resource_set_id;store.set('rag-resource',resource);savedState?.set('resource',resource);}
 if(p.model_profile_id){profile=p.model_profile_id;store.set('rag-profile',profile);savedState?.set('profile',profile);}
 await refreshProfiles();fillProfile();renderPreflight(p);
 experimentReady=p.ready;preparedExperiment=p.ready?p.request:null;$('experiment').disabled=!p.ready;
 stepStatus('experiment',p.ready?'准备完成，可启动':reasonNames[p.blocking_items?.[0]]||'准备未完成',p.ready?'ok':'warn');
 notice(p.ready?'正式实验已准备完成。':joinReasons((p.blocking_items||[]).slice(0,2)),p.ready?'ok':'warn');
}
bind('preflight',prepareExperiment);
bind('experiment',()=>{if(!preparedExperiment)throw Error('请先准备正式实验。');return start({...preparedExperiment,client_request_id:crypto.randomUUID()});});

const stateNames={queued:'排队中',validating:'校验中',running:'运行中',pause_requested:'正在暂停',paused:'已暂停',cancel_requested:'正在取消',cancelled:'已取消',completed:'已完成',failed:'失败',blocked:'受阻',interrupted:'已中断'};
const kindNames={demo:'单题演示',experiment:'正式实验',verify:'结果复核',prepare:'重建输入'};
const ACTIVE=['running','queued','validating','pause_requested','cancel_requested'];
const stateTone=s=>s==='completed'?'ok':ACTIVE.includes(s)?'run':['failed','blocked','cancelled'].includes(s)?'err':'warn';
function renderRunState(state){
 const label=stateNames[state.state]||state.state,tone=stateTone(state.state),kind=kindNames[state.kind]||state.kind;
 const total=state.logical_generation_count,done=state.completed_logical_count;
 $('experimentStart').hidden=state.kind==='experiment'&&ACTIVE.includes(state.state);
 $('progressBar').style.width=total?Math.min(100,100*done/total)+'%':(state.state==='completed'?'100%':'0');
 const stat=(k,v)=>el('div',{},el('dt',{textContent:k}),el('dd',{textContent:String(v)}));
 $('progress').replaceChildren(
  el('div',{className:'run-head'},el('span',{className:'badge '+tone,textContent:label}),el('strong',{textContent:kind}),el('span',{className:'hint',textContent:'阶段 '+({prepare:'准备输入',generation:'生成回答',scoring:'评分'}[state.phase]||state.phase)})),
  el('dl',{},stat('逻辑生成',`${done}/${total}`),stat('实际尝试',state.attempt_count),stat('在途',state.in_flight_count),stat('失败',state.failure_count),stat('运行 ID',short(state.run_id||run))));
 if(state.blocking_items.length){const b=el('div',{id:'runBlocks'});$('progress').append(b);renderBlocks('runBlocks',state.blocking_items,'待解决');}
 $('ctxRun').textContent=`${short(state.run_id||run)} · ${label}`;
 if(state.kind==='experiment'){$('plannedGenerations').textContent=total.toLocaleString();stepStatus('experiment',label,tone==='run'?'':tone==='err'?'err':tone);}
 if(state.kind==='demo'){$('demoStatus').textContent=total?`${label} · ${done}/${total}`:label;stepStatus('demo','演示 '+label,tone==='run'?'':tone==='err'?'err':tone);}
}
function renderActivity(state,events){
 const a=RagState.runActivity(state,events),clock=t=>t?new Date(t).toLocaleTimeString():'尚无';
 const phase={prepare:'准备输入',retrieval:'检索证据',generation:'生成回答',scoring:'对照标准答案评分',statistics:'汇总统计'}[a.phase]||a.phase;
 const box=el('div',{className:'run-activity'});
 const detail=a.phase==='prepare'&&a.preparationTotal?`${phase} ${a.prepared}/${a.preparationTotal} · 检查点自动保存`:phase;
 box.append(el('p',{textContent:detail}),el('p',{className:'hint',textContent:`并发：${a.concurrency||'准备中'} · 已保存结果：${a.saved} 条 · 最近保存：${clock(a.lastSaved)}`}),el('p',{className:'hint',textContent:`最近进展：${clock(a.lastProgress)}${a.stalled?' · 超过 150 秒没有新进展，请检查运行状态。':''}`}));
 const recentError=events.findLast(e=>e.event_type==='request_error');
 if(recentError){
  const names={transport_TimeoutError:'请求超时',transport_URLError:'连接异常',stream_eof_without_final_response:'响应流提前断开',stream_done_without_final_response:'响应流未提供最终结果',response_stream_error:'服务返回错误事件',http_429:'服务限流',http_502:'上游网关异常',http_503:'服务暂不可用',http_504:'上游超时'};
  box.append(el('p',{className:'hint',textContent:`最近请求异常 ${clock(recentError.time)}：${names[recentError.error_code]||recentError.error_code} · 临时错误最多重试 2 次，仍失败则记录并继续。`}));
 }
 $('progress').append(box);
 if(a.phase==='prepare'&&a.preparationTotal)$('progressBar').style.width=(100*a.prepared/a.preparationTotal)+'%';
}
async function renderRecovery(state,id){
 const panel=$('recoveryPanel');panel.hidden=true;panel.replaceChildren();
 if(!['blocked','interrupted','failed','paused'].includes(state.state))return;
 if(operationsSupported){
  try{const diagnostic=await api('runs/'+id+'/diagnostics');if(run!==id)return;
   if(diagnostic.diagnostic_id){panel.hidden=false;panel.append(el('h3',{textContent:'运行诊断'}),el('p',{textContent:'诊断编号：'+diagnostic.diagnostic_id}),el('p',{className:'hint',textContent:'本地记录：当前运行目录下的 diagnostics.json；仅含异常类型与代码位置。'}),el('pre',{textContent:pretty(diagnostic)}));}
  }catch{/* Diagnostics never block the existing recovery controls. */}
 }
 const report=await api('runs/'+id+'/recovery');if(run!==id)return;
 if(!report.items.length)return;panel.hidden=false;
 panel.append(el('h3',{textContent:'中断恢复'}),el('p',{className:'hint',textContent:'已有响应会在恢复时自动取回。无法确认的调用不会自动重发；记为失败会影响实验指标，调用记录仍保留。'}));
 for(const item of report.items){
  const section=el('div',{className:'block'},el('p',{textContent:'任务：'+item.task_id}));
  if(item.recoverable)section.append(el('p',{textContent:'恢复记录已保存，点击下方“恢复”继续。'}));
  else{
   const note=el('textarea',{placeholder:'记录核对人、核对依据及按失败处理的原因（至少10字）','ariaLabel':'恢复处理说明'});
   const check=el('input',{type:'checkbox'});
   const button=el('button',{className:'secondary',textContent:'记为失败，不重新调用',disabled:true});
   const enable=()=>{button.disabled=!check.checked||note.value.trim().length<10;};note.oninput=enable;check.onchange=enable;
   button.onclick=async()=>{button.disabled=true;try{await api('runs/'+id+'/recovery',{attempt_id:item.attempt_id,note:note.value,confirmed:check.checked});notice('处理记录已保存；点击“恢复”继续剩余任务。');await refresh();}catch(e){notice(e.message,'error');enable();}};
   section.append(note,el('label',{},check,'我确认按失败计入结果，保留原尝试账本'),button);
  }
  panel.append(section);
 }
}
async function refresh(){if(!run)return;clearTimeout(timer);const observedRun=run;const state=await api('runs/'+observedRun);if(run!==observedRun)return;runKind=state.kind;renderRunState(state);document.querySelectorAll('[data-control]').forEach(b=>{b.disabled=!state.available_actions.includes(b.dataset.control);});if(eventRun!==observedRun){eventRun=observedRun;eventCursor=0;eventHistory=[];}const events=await api('runs/'+observedRun+'/events?after_seq='+eventCursor);if(run!==observedRun)return;for(const e of events){if(e.seq>eventCursor){eventHistory.push(e);eventCursor=e.seq;}}$('events').textContent=pretty(eventHistory.slice(-100));renderActivity(state,eventHistory);await renderRecovery(state,observedRun);if(run!==observedRun)return;if(state.state==='completed')await results();else if(ACTIVE.includes(state.state))timer=setTimeout(pollRun,1200);}
async function pollRun(){try{await refresh();$('connection').textContent='本机服务已连接';}catch(e){$('connection').textContent='连接中断 · 正在重连';notice('暂时无法读取进度，正在重连；不会重新提交实验。','warn');timer=setTimeout(pollRun,3000);}}
bind('recoverSubmission',async()=>{
 const id=savedState?.get('pendingRequest');if(!id)return;
 let state=await api('runs?client_request_id='+encodeURIComponent(id));
 if(!state.run_id){const q=savedState.get('pendingPayload');if(!q)throw Error('无法读取上次请求，请保留当前工作区并检查运行记录。');state=await api('runs',{request:q,credential_ref:credential});}
 run=state.run_id;savedState.set('run',run);store.set('rag-run',run);savedState.set('pendingRequest',null);savedState.set('pendingPayload',null);$('recoverSubmission').hidden=true;$('runId').value=run;await refresh();
});
bind('restore',async()=>{run=$('runId').value.trim();store.set('rag-run',run);savedState?.set('run',run);await refresh();});
document.querySelectorAll('[data-control]').forEach(b=>b.onclick=async()=>{try{await api('runs/'+run+'/control',{action:b.dataset.control});await refresh();}catch(e){notice(e.message,'error');}});

// ---------- 4 结果复核 ----------
async function file(name){const r=await fetch('/api/v1/runs/'+run+'/files/'+name);if(!r.ok)throw Error('输出尚不可用或封存校验未通过（'+r.status+'）');return r.text();}
const statusNames={answered:'已作答',abstained:'证据不足',failed:'调用失败',refused:'拒绝回答',parse_error:'格式异常',truncated:'输出截断'};
const statusTone={answered:'ok',abstained:'warn'};
const provenanceNames={local:'规则抽取',live:'实际模型调用',same_round_reuse:'同轮复用'};
function answerCard(r){
 const d=el('article',{className:'answer'});
 d.append(el('header',{},el('strong',{textContent:r.condition}),el('span',{className:'badge '+(statusTone[r.status]||'err'),textContent:statusNames[r.status]||r.status}),el('span',{className:'badge',textContent:syntheticOutput?'软件测试':(provenanceNames[r.provenance]||r.provenance)}),r.retriever?el('span',{className:'badge',textContent:r.retriever}):null));
 d.append(el('p',{className:'value'},'答案：'+(r.answer??'—'),el('small',{textContent:'量级：'+(r.scale||'无单独量级')})));
 d.append(el('p',{textContent:'题目编号：'+r.question_id+' ｜轮次：'+r.repeat+' ｜耗时：'+r.elapsed_ms+' ms ｜模型：'+r.model}));
 if(r.uem!==undefined)d.append(el('p',{textContent:'UEM: '+r.uem+' ｜ EM: '+r.em+' ｜ F1: '+r.f1}));
 const more=el('details',{},el('summary',{textContent:'引用'+(r.uem!==undefined?'与数值诊断':'')}),el('pre',{textContent:'引用：'+pretty(r.citations)+(r.uem!==undefined?'\n数值与单位诊断：'+pretty(r.numeric):'')}));
 d.append(more);return d;
}
function renderAnswers(){
 const filter=$('partition').value;const condition=$('conditionFilter').value,retriever=$('answerRetriever').value;
 const selected=answers.filter(r=>(filter==='all'||(filter==='development'?['development','demo','custom'].includes(r.task_id.split('|')[0]):r.task_id.split('|')[0]===filter))&&(condition==='all'||r.condition===condition)&&(retriever==='all'||r.retriever===retriever));
 const pages=Math.max(1,Math.ceil(selected.length/100));answerPage=Math.min(answerPage,pages-1);
 $('answerCount').textContent=`符合筛选 ${selected.length} 条结果，第 ${answerPage+1}/${pages} 页，每页最多100条。`;
 $('previousAnswers').disabled=answerPage===0;$('nextAnswers').disabled=answerPage>=pages-1;
 const page=selected.slice(answerPage*100,(answerPage+1)*100).map(answerCard);
 $('answers').replaceChildren(...(page.length?page:[el('p',{className:'empty',textContent:'没有符合筛选的结果。'})]));
}
function renderDemoAnswers(){
 const shown=answers.slice(0,20).map(r=>el('div',{className:'demo-answer-line'},
  el('span',{className:'demo-condition',textContent:r.condition}),
  el('p',{className:'demo-answer-value',textContent:r.status==='answered'?((r.answer??'—')+(r.scale?' · '+r.scale:'')):(statusNames[r.status]||r.status)})));
 $('demoAnswers').replaceChildren(...(shown.length?shown:[el('p',{className:'empty',textContent:'暂无回答'})]));
}
function renderReference(report){
 const target=$('demoReference');referenceQuestion=report?.question||'';syncResultContext();
 renderDemoAnswers();
 if(!report?.available){target.replaceChildren(el('p',{className:'hint',textContent:'暂无可对照的标准答案'}));return;}
 const content=[el('div',{className:'reference-line'},el('span',{className:'reference-label',textContent:'标准答案'}),el('strong',{textContent:report.answer.join('；')+(report.scale?' · '+report.scale:'')}))];
 if(report.annotation_conflict)content.push(el('p',{className:'hint',textContent:'原标注有争议，请结合原文核对。'}));
 target.replaceChildren(...content);
}
for(const id of ['partition','conditionFilter','answerRetriever'])$(id).onchange=()=>{answerPage=0;renderAnswers();};
$('previousAnswers').onclick=()=>{answerPage=Math.max(0,answerPage-1);renderAnswers();};
$('nextAnswers').onclick=()=>{answerPage++;renderAnswers();};
function renderMetrics(metrics){const table=document.createElement('table');for(const cells of [['分区 / 检索器 / 条件 / 模型','UEM','题数','来源数'],...Object.entries(metrics).map(([key,m])=>[key,(100*m.uem).toFixed(1)+'%',m.question_count,m.source_count])]){const row=document.createElement('tr');for(const value of cells){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}table.append(row);}$('metricTable').replaceChildren(...(Object.keys(metrics).length?[table]:[]));}
async function results(){
 const referenceRun=run;
 const summary=await api('runs/'+run+'/results');syntheticOutput=summary.engineering.synthetic===true;$('summaryDetails').textContent=pretty(summary);renderMetrics(summary.metrics);
 const scored=Object.keys(summary.metrics).length>0;
 $('summary').textContent=scored?`封存结果已评分；人工审核${summary.audit_complete?'已完成':'尚未完成'}。`:`本次尚未进行测试集评分。实际模型尝试 ${summary.engineering.attempt_count||0} 次。`;
 try{const trace=(await file('retrieval.jsonl')).trim().split('\n').filter(Boolean).map(JSON.parse);$('retrievalTrace').textContent=pretty(trace.slice(0,20));}catch(e){$('retrievalTrace').textContent='此任务未提供检索明细，可加载原始生成运行查看。';}
 const answerIndex=await api('runs/'+run+'/results?view=answers');
 answers=(await file(answerIndex.answer_tables[0]||'predictions.jsonl')).trim().split('\n').filter(Boolean).map(JSON.parse);
 if(answerIndex.answer_tables[0]==='scored-answers.jsonl'){const raw=(await file('predictions.jsonl')).trim().split('\n').filter(Boolean).map(JSON.parse);const byTask=new Map(raw.map(row=>[row.task_id,row]));answers=answers.map(row=>({...byTask.get(row.task_id),...row}));}
 const previousCondition=$('conditionFilter').value;const conditionOptions=['all',...new Set(answers.map(r=>r.condition))];
 $('conditionFilter').replaceChildren(...conditionOptions.map(value=>el('option',{value,textContent:value==='all'?'全部条件':value})));
 $('conditionFilter').value=conditionOptions.includes(previousCondition)?previousCondition:'all';answerPage=0;renderAnswers();
 if(runKind==='demo'){
  renderDemoAnswers();
  try{const reference=await api('runs/'+referenceRun+'/reference');if(run!==referenceRun)return;renderReference(reference);}
  catch(e){if(run!==referenceRun)return;renderReference({available:false,reason:'标准答案读取或来源校验失败；已生成回答仍保留。'});}
 }
 $('downloads').replaceChildren(...[...summary.answer_tables,...summary.exports].map(name=>el('a',{href:'/api/v1/runs/'+run+'/files/'+name,textContent:'下载 '+name,download:name})));
 try{const units=await api('runs/'+run+'/evidence');$('evidence').replaceChildren(...units.map(u=>{const d=el('div',{className:'evidence',textContent:u.evidence_id+'\n'+u.text});if(u.image_url)d.append(el('img',{src:u.image_url,alt:u.evidence_id+' 对应范围裁图',loading:'lazy'}));return d;}));}catch(e){$('evidence').textContent='正式逐题证据请依据封存的检索记录复核；此处仅展示开发材料。';}
 stepStatus('results',(syntheticOutput?'软件测试 · ':'')+(scored?'已评分':'已读取 · 未评价')+` · ${answers.length} 条`,syntheticOutput?'warn':'ok');
 notice(summary.engineering.synthetic?'软件测试输出（synthetic），不代表正式实验成绩。':'已读取封存输出。',summary.engineering.synthetic?'warn':'ok');
}
const needRun=fn=>async()=>{if(!run)throw Error('请先运行或加载一个运行 ID');await fn();};
bind('results',needRun(results));
bind('verify',needRun(async()=>{const origin=await api('runs/'+run+'/origin'),q=request('verify');q.profile_id=origin.profile_id;q.resource_set_id=origin.resource_set_id;await start(q,'resultsBlocks');}));
bind('export',needRun(async()=>{const result=await api('runs/'+run+'/export',{});renderBlocks('resultsBlocks',result.exported?[]:[...result.blocking_items,...(result.missing||[]).map(m=>'缺少 '+m)],'尚不能导出');notice(result.exported?'课程专用包已生成：'+result.archive:'尚不能导出：'+joinReasons(result.blocking_items)+(result.missing?'；缺少 '+result.missing.join('、'):''),result.exported?'ok':'warn');}));

async function refreshFixedData(){
 if(dataRefreshing)return fixedDataStatus;
 dataRefreshing=true;clearTimeout(dataTimer);
 try{
  const d=await api('data/status');fixedDataStatus=d;dataFailures=0;
  const downloaded=d.downloaded_bytes,total=d.total_bytes;
  const stopped=['failed','invalid','configuration_error','conflict'].includes(d.state);
  $('dataSetupStatus').textContent=d.ready?'固定主集与开发资料已校验，可直接使用。':stopped?(d.message||'资料准备未完成，请查看应用指南。'):downloaded?`正在下载固定资料：${(downloaded/1048576).toFixed(1)}${total?' / '+(total/1048576).toFixed(1):''} MiB`:'正在自动校验并准备固定实验资料…';
  $('dataRetry').hidden=d.ready;
  $('dataRetry').textContent=stopped?'重新检查并准备资料':'刷新资料状态';
  $('reviewed').disabled=!d.ready;$('prepared').disabled=!d.ready;
  if(d.ready&&(!resource||['formal-v1','formal-v1-course'].includes(resource)))await register(d.formal_root);
  if(d.ready&&pendingExperimentPreparation){pendingExperimentPreparation=false;await prepareExperiment();}
  if(!d.ready&&!stopped){
   if(d.state==='not_started')await api('data/prepare',{});
   dataTimer=setTimeout(refreshFixedData,1500);
  }
  return d;
 }catch(e){
  $('dataSetupStatus').textContent='暂时无法读取资料状态，正在自动重连；可手动刷新。'+e.message;
  $('dataRetry').hidden=false;$('dataRetry').textContent='刷新资料状态';
  $('reviewed').disabled=!fixedDataStatus?.ready;$('prepared').disabled=!fixedDataStatus?.ready;
  dataFailures+=1;dataTimer=setTimeout(refreshFixedData,Math.min(15000,1500*2**Math.min(dataFailures,4)));
  return null;
 }finally{dataRefreshing=false;}
}
bind('dataRetry',async()=>{if(['failed','invalid','configuration_error','conflict'].includes(fixedDataStatus?.state))await api('data/prepare',{});await refreshFixedData();});

// ---------- start ----------
(async()=>{try{
 const caps=await refreshProfiles();session=caps.session_id;
 const storage=name=>{try{return window[name];}catch{return null;}};
 savedState=RagState.createState(storage('localStorage'),storage('sessionStorage'),caps.workspace);
 operationsSupported=caps.background_checks===true;
 operationClient=RagState.createOperations(api,savedState,session,operationStatus);
 const legacyWorkspace=store.get('rag-migration-workspace');const migrate=!legacyWorkspace||legacyWorkspace===caps.workspace;
 if(!legacyWorkspace)store.set('rag-migration-workspace',caps.workspace);
 profile=savedState.get('profile')||(migrate?profile:'formal-v1');resource=savedState.get('resource')??(migrate?resource:'');run=savedState.get('run')??(migrate?run:'');
 if(!profiles.some(p=>p.profile_id===profile))profile='formal-v1';showProfile();savedState.set('profile',profile);
 credential=savedState.credential(session);$('key').placeholder=credential?'本次会话已保存密钥':'填写 API Key';
 $('connection').textContent='本机服务已连接';$('connection').className='conn ok';
 const defaults=await api('demo-defaults');$('resourcePath').value=defaults.legacy_root;$('outputDirectory').value=defaults.workspace;$('workspaceStatus').textContent='当前结果位置：'+defaults.workspace;
 const s=caps.settings||{};$('endpoint').value=s.base_url||'';$('model').value=s.model||'';$('secondary').value=s.secondary_model||'';fillProfile();
 notice('本机服务已连接，固定实验资料正在自动准备。');
 await refreshFixedData();
 // Remembered ids may belong to another workspace; a failed restore is not a lost connection.
 if(resource){try{const report=await api('resources/'+resource);await loadExamples();restoreDemoState();resourceSummary(report);savedState.set('resource',resource);notice('已恢复配置与资料。');}catch(e){notice('上次登记的资料在当前工作区不可用，请重新登记。（'+e.message+'）','warn');}}
 const pending=savedState.get('pendingRequest');
 if(pending){$('recoverSubmission').hidden=false;try{const found=await api('runs?client_request_id='+encodeURIComponent(pending));if(found.run_id){run=found.run_id;savedState.set('run',run);savedState.set('pendingRequest',null);savedState.set('pendingPayload',null);$('recoverSubmission').hidden=true;}else notice('上次提交尚未确认，请先检查运行记录，不要重复启动。','warn');}catch{notice('上次提交状态待确认，请恢复连接后刷新。','warn');}}
 if(!run){try{const history=await api('runs');const recent=history.runs.find(r=>ACTIVE.includes(r.state))||history.runs[0];if(recent){run=recent.run_id;savedState.set('run',run);}}catch{}}
 restoring=false;
 if(operationClient.pending())recoverOperation().catch(e=>notice(e.message,'warn'));
 if(run){$('runId').value=run;try{await refresh();}catch(e){notice('暂时无法恢复运行进度，正在重连。','warn');timer=setTimeout(pollRun,3000);}}
}catch(e){$('connection').textContent='未连接';$('connection').className='conn err';notice(e.message,'error');}})();
