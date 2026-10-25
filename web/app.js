'use strict';
const $=id=>document.getElementById(id), pretty=x=>JSON.stringify(x,null,2);
const store={get(k){try{return localStorage.getItem(k);}catch{return null;}},set(k,v){try{localStorage.setItem(k,v);}catch{}}};
let session='',credential=null,profiles=[],profile=store.get('rag-profile')||'formal-v1',resource=store.get('rag-resource')||'',run=store.get('rag-run')||'',examples=[],timer=null,answers=[];
let syntheticOutput=false,answerPage=0,runKind='';
const el=(tag,props={},...children)=>{const node=Object.assign(document.createElement(tag),props);node.append(...children.filter(c=>c!==null&&c!==undefined));return node;};
const short=id=>id?(id.length>14?id.slice(0,8)+'…':id):'';

// ---------- navigation ----------
const TABS=['config','demo','experiment','results'];
function showTab(name,focus=false){
 if(!TABS.includes(name))name='config';
 for(const t of TABS){const tab=$('tab-'+t),panel=$('panel-'+t),on=t===name;tab.setAttribute('aria-selected',on);tab.tabIndex=on?0:-1;panel.hidden=!on;}
 if(focus)$('tab-'+name).focus();
 store.set('rag-tab',name);if(location.hash!=='#'+name)history.replaceState(null,'','#'+name);
}
for(const t of TABS)$('tab-'+t).onclick=()=>showTab(t);
document.querySelector('.steps').onkeydown=e=>{const i=TABS.indexOf(document.activeElement?.dataset?.tab);if(i<0)return;const d={ArrowRight:1,ArrowLeft:-1}[e.key];if(d){e.preventDefault();showTab(TABS[(i+d+TABS.length)%TABS.length],true);}};
document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>showTab(b.dataset.goto));
window.onhashchange=()=>showTab(location.hash.slice(1));
showTab(location.hash.slice(1)||store.get('rag-tab')||'config');
function stepStatus(name,text,tone=''){$('status-'+name).textContent=text;$('tab-'+name).dataset.tone=tone;}

// ---------- feedback ----------
const notice=(s,tone='info')=>{$('notice').textContent=s;$('notice').dataset.tone=tone;};
function box(id,text,tone=''){$(id).textContent=text;$(id).dataset.tone=tone;}
const reasonNames={profile_not_frozen:'正式方案尚未冻结',demo_only_resources:'当前登记的是旧开发材料',resources_not_ready:'正式资源尚未就绪',model_missing:'尚未登记本地模型',reviews_missing:'人工核验尚未完成',credential_required:'请填写并保存本次会话密钥',kernel_unavailable:'执行模块尚未就绪',source_not_sealed:'原运行尚未封存',execution_slot_busy:'已有任务正在运行',model_profile_required:'请选择已保存的自定义模型方案',approved_resources_immutable:'已审核资料不可重建',evaluation_gold_missing:'缺少绑定的评价资料',provider_generation_failed:'模型服务未完成生成，查看调用记录',qualification_source_disjoint:'待完成来源隔离审核',qualification_cohorts_validated:'待完成样本集合资格审核',qualification_inputs_complete:'待补齐完整输入',qualification_development_passed:'开发检查未通过',qualification_validation_passed:'验证检查未通过',qualification_compatibility_passed:'模型兼容检查未通过',qualification_throughput_passed:'吞吐检查未通过',qualification_budget_validated:'调用预算尚未核定',qualification_evidence_missing:'缺少资格检查凭据',course_documents_missing:'课程报告、摘要或成员资料尚未准备',formal_preset_required:'只能冻结固定正式方案',preregistered_audit_plan_required:'缺少预注册审核子集',preregistered_audit_plan_invalid:'预注册审核子集未通过校验',budget_exhausted:'调用额度不足',retry_demo_budget_exhausted:'演示与重试额度不足',category_budget_exceeded:'分类额度已超出',budget_check_failed:'调用账本校验失败',frozen_profile_invalid:'冻结配置校验未通过',code_changed:'代码已变化，与冻结身份不符',resources_changed:'资料已变化，与冻结身份不符',profile_changed:'方案已变化，与运行身份不符',inference_projection_unavailable:'推理输入不可用',formal_plan_unavailable:'正式计划不可编译',model_not_in_catalog:'模型不在服务目录中',operational_marker_not_answered:'连通检查未返回预期标记'};
const explain=x=>reasonNames[x]||x;
const joinReasons=items=>items.map(explain).join('；');
function renderBlocks(id,items,title='还需完成'){
 const target=$(id);if(!items?.length){target.replaceChildren();return;}
 target.replaceChildren(el('p',{className:'blocks-title',textContent:`${title}（${items.length} 项）`}),el('ul',{className:'blocks'},...items.map(x=>el('li',{},el('span',{textContent:explain(x)}),el('code',{textContent:x})))));
}

// ---------- API ----------
async function api(path,body,method='POST') {
 const response=await fetch('/api/v1/'+path,{method:body===undefined?'GET':method,headers:{'Content-Type':'application/json','X-Session-ID':session},...(body===undefined?{}:{body:JSON.stringify(body)})});
 let data={};try{data=await response.json();}catch{}
 if(!response.ok)throw Error(data.detail||data.message||data.code||'请求未完成（'+response.status+'）');return data;
}
function bind(id,fn){$(id).onclick=async()=>{const button=$(id);button.disabled=true;try{await fn();}catch(e){notice(String(e.message),'error');}finally{button.disabled=false;if(id==='experiment')button.disabled=!experimentReady;}};}
function request(kind,canonical=false){return {schema_version:1,client_request_id:crypto.randomUUID(),kind,profile_id:canonical?'formal-v1':profile,resource_set_id:resource,source_run_id:kind==='verify'?run:null,demo_input:kind==='demo'?{doc_id:$('doc').value.trim(),question:$('question').value.trim(),retriever:$('retriever').value,groups:['E1',...['e2','e3','e4'].filter(id=>$(id).checked).map(id=>id.toUpperCase())]}:null};}

// ---------- 1 配置与资源 ----------
function showProfile(){const current=profiles.find(p=>p.profile_id===profile);$('profiles').value=profile;const status=current?.status||'draft';$('profile').textContent=profile==='formal-v1'?`固定正式方案 · ${status}；开发 E1 无需密钥。`:`当前模型方案：${profile} · ${status}`;$('ctxProfile').textContent=(profile==='formal-v1'?'formal-v1':current?.models?.primary||short(profile))+' · '+status;}
async function refreshProfiles(){const caps=await api('capabilities');profiles=caps.profiles;$('profiles').replaceChildren(...profiles.map(p=>el('option',{value:p.profile_id,textContent:(p.profile_id==='formal-v1'?'固定正式方案':p.models?.primary||'自定义')+' · '+p.status+' · '+p.profile_id})));if(!profiles.some(p=>p.profile_id===profile))profile='formal-v1';showProfile();return caps;}
$('profiles').onchange=()=>{profile=$('profiles').value;store.set('rag-profile',profile);const p=profiles.find(x=>x.profile_id===profile);if(p?.provider){$('endpoint').value=p.provider.endpoint||'';$('model').value=p.models?.primary||'';$('secondary').value=p.models?.secondary||'';}showProfile();};
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
$('examples').onchange=selectExample;
async function register(path){const report=await api('resources/register',{root_path:path});resource=report.resource_set_id;store.set('rag-resource',resource);await loadExamples();resourceSummary(report);notice('资源已登记，文件已核对；开发资料可用于演示，正式缺项见校验明细。下一步：在「单题演示」运行一题。','ok');}
bind('saveWorkspace',async()=>{const result=await api('settings',{output_directory:$('outputDirectory').value.trim()},'PUT');$('workspaceStatus').textContent=result.restart_required?'已保存，下次启动后生效；当前任务与原结果保留在原工作区。':'当前已使用该目录';notice($('workspaceStatus').textContent,'ok');});
bind('register',()=>register($('resourcePath').value));
bind('legacy',async()=>register((await api('demo-defaults')).legacy_root));
bind('prepared',async()=>{const path=(await api('demo-defaults')).formal_root;$('resourcePath').value=path;await register(path);});
bind('prepare',()=>start(request('prepare'),'prepareBlocks'));
bind('importReviews',async()=>{if(!resource)throw Error('请先登记对应资源');const report=await api('resources/'+resource+'/reviews',JSON.parse($('reviewManifest').value));resourceSummary(report);notice('已校验并导入审核记录。记录与资源 SHA 绑定，其他门槛仍单独检查。','ok');});
bind('save',async()=>{
 const endpoint=$('endpoint').value.trim(),model=$('model').value.trim();if(!endpoint||!model)throw Error('请填写实际接口完整地址和主模型名称');
 await api('settings',{base_url:endpoint,model,secondary_model:$('secondary').value.trim(),timeout_seconds:120},'PUT');
 const values={models:{primary:model,secondary:$('secondary').value.trim()||null},provider:{endpoint,max_output_tokens:2048,reserve_input_tokens:131072,timeout_seconds:120,stream:true,reasoning_effort:'low'}};
 const current=profiles.find(p=>p.profile_id===profile);
 const adopted=profile==='formal-v1'||current?.status==='frozen'?await api('profiles/'+profile+'/clone',values):await api('profiles/'+profile,values,'PUT');profile=adopted.profile_id;store.set('rag-profile',profile);
 if($('key').value){const result=await api('credentials/session',{api_key:$('key').value});credential=result.credential_ref;$('key').value='';$('key').placeholder='本次会话已保存密钥';}
 await refreshProfiles();notice('模型方案已保存；密钥只在服务内存中。输出上限2048，reasoning low，store false。','ok');
});
bind('check',async()=>{const report=await api('provider/check',{settings:{endpoint:$('endpoint').value.trim(),model:$('model').value.trim(),max_output_tokens:2048,reserve_input_tokens:131072,stream:true,reasoning_effort:'low'},credential_ref:credential,level:'generation'});notice(report.ready?'合成文本连通检查通过；图像、对照模型和吞吐仍需独立检查。':'连通检查未通过：'+joinReasons(report.blocking_items),report.ready?'ok':'warn');$('preflightDetails').textContent=pretty(report);});

// ---------- 3 正式实验 ----------
let experimentReady=false;
function renderPreflight(p){
 $('preflightDetails').textContent=pretty(p);
 if(p.budget_remaining!==undefined)$('budgetRemaining').textContent=Number(p.budget_remaining).toLocaleString();
 box('preflightResult',p.ready?`预检通过，预计新增 ${p.estimated_additional_attempts} 次模型调用。`:`尚未就绪，还有 ${p.blocking_items.length} 项需要完成。`,p.ready?'ok':'warn');
 renderBlocks('preflightBlocks',p.blocking_items);
}
bind('freeze',async()=>{const result=await api('profiles/formal-v1/freeze',{resource_set_id:resource,model_profile_id:profile});notice(result.frozen?'正式配置已冻结；下一步运行预检。':'尚不能冻结：'+joinReasons(result.blocking_items),result.frozen?'ok':'warn');box('preflightResult',result.frozen?'正式配置已冻结；下一步：正式预检。':`尚不能冻结，还有 ${result.blocking_items.length} 项需要完成。`,result.frozen?'ok':'warn');renderBlocks('preflightBlocks',result.blocking_items);$('preflightDetails').textContent=pretty(result);await refreshProfiles();});
async function start(q,blocksId){
 const pre=await api('runs/preflight',q);
 if(q.kind==='experiment')renderPreflight(pre);else renderBlocks(blocksId,pre.blocking_items,'运行前需解决');
 if(!pre.ready)throw Error('待解决：'+joinReasons(pre.blocking_items));
 const state=await api('runs',{request:q,credential_ref:credential});run=state.run_id;store.set('rag-run',run);$('runId').value=run;await refresh();
}
bind('demo',async()=>{$('demoStatus').textContent='预检中…';try{await start(request('demo'),'demoBlocks');}catch(e){$('demoStatus').textContent='';throw e;}});
bind('preflight',async()=>{const p=await api('runs/preflight',request('experiment',true));renderPreflight(p);experimentReady=p.ready;$('experiment').disabled=!p.ready;stepStatus('experiment',p.ready?'预检通过':`${p.blocking_items.length} 项待完成`,p.ready?'ok':'warn');notice(p.ready?'正式预检通过':'正式实验尚未就绪：'+joinReasons(p.blocking_items),p.ready?'ok':'warn');});
bind('experiment',()=>start(request('experiment',true)));

const stateNames={queued:'排队中',validating:'校验中',running:'运行中',pause_requested:'正在暂停',paused:'已暂停',cancel_requested:'正在取消',cancelled:'已取消',completed:'已完成',failed:'失败',blocked:'受阻',interrupted:'已中断'};
const kindNames={demo:'单题演示',experiment:'正式实验',verify:'结果复核',prepare:'重建输入'};
const ACTIVE=['running','queued','validating','pause_requested','cancel_requested'];
const stateTone=s=>s==='completed'?'ok':ACTIVE.includes(s)?'run':['failed','blocked','cancelled'].includes(s)?'err':'warn';
function renderRunState(state){
 const label=stateNames[state.state]||state.state,tone=stateTone(state.state),kind=kindNames[state.kind]||state.kind;
 const total=state.logical_generation_count,done=state.completed_logical_count;
 $('progressBar').style.width=total?Math.min(100,100*done/total)+'%':(state.state==='completed'?'100%':'0');
 const stat=(k,v)=>el('div',{},el('dt',{textContent:k}),el('dd',{textContent:String(v)}));
 $('progress').replaceChildren(
  el('div',{className:'run-head'},el('span',{className:'badge '+tone,textContent:label}),el('strong',{textContent:kind}),el('span',{className:'hint',textContent:'阶段 '+state.phase})),
  el('dl',{},stat('逻辑生成',`${done}/${total}`),stat('实际尝试',state.attempt_count),stat('在途',state.in_flight_count),stat('失败',state.failure_count),stat('费用','未知'),stat('运行 ID',short(state.run_id||run))));
 if(state.blocking_items.length){const b=el('div',{id:'runBlocks'});$('progress').append(b);renderBlocks('runBlocks',state.blocking_items,'待解决');}
 $('ctxRun').textContent=`${short(state.run_id||run)} · ${label}`;
 if(state.kind==='experiment')stepStatus('experiment',label,tone==='run'?'':tone==='err'?'err':tone);
 if(state.kind==='demo'){$('demoStatus').textContent=total?`${label} · ${done}/${total}`:label;stepStatus('demo','演示 '+label,tone==='run'?'':tone==='err'?'err':tone);}
}
async function refresh(){if(!run)return;clearTimeout(timer);const state=await api('runs/'+run);runKind=state.kind;renderRunState(state);document.querySelectorAll('[data-control]').forEach(b=>{b.disabled=!state.available_actions.includes(b.dataset.control);});$('events').textContent=pretty(await api('runs/'+run+'/events'));if(state.state==='completed')await results();else if(ACTIVE.includes(state.state))timer=setTimeout(()=>refresh().catch(e=>notice(e.message,'error')),1200);}
bind('restore',async()=>{run=$('runId').value.trim();store.set('rag-run',run);await refresh();});
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
 const shown=answers.slice(0,20).map(answerCard);
 if(answers.length>20)shown.push(el('p',{className:'hint'},`共 ${answers.length} 条，此处显示前 20 条。`,el('button',{type:'button',className:'link',textContent:'在结果复核中查看全部 →',onclick:()=>showTab('results')})));
 $('demoAnswers').replaceChildren(...(shown.length?shown:[el('p',{className:'empty',textContent:'本次运行没有回答记录。'})]));
}
for(const id of ['partition','conditionFilter','answerRetriever'])$(id).onchange=()=>{answerPage=0;renderAnswers();};
$('previousAnswers').onclick=()=>{answerPage=Math.max(0,answerPage-1);renderAnswers();};
$('nextAnswers').onclick=()=>{answerPage++;renderAnswers();};
function renderMetrics(metrics){const table=document.createElement('table');for(const cells of [['分区 / 检索器 / 条件 / 模型','UEM','题数','来源数'],...Object.entries(metrics).map(([key,m])=>[key,(100*m.uem).toFixed(1)+'%',m.question_count,m.source_count])]){const row=document.createElement('tr');for(const value of cells){const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);}table.append(row);}$('metricTable').replaceChildren(...(Object.keys(metrics).length?[table]:[]));}
async function results(){
 const summary=await api('runs/'+run+'/results');syntheticOutput=summary.engineering.synthetic===true;$('summaryDetails').textContent=pretty(summary);renderMetrics(summary.metrics);
 const scored=Object.keys(summary.metrics).length>0;
 $('summary').textContent=scored?`封存结果已评分；人工审核${summary.audit_complete?'已完成':'尚未完成'}。`:`本次尚未进行测试集评分。实际模型尝试 ${summary.engineering.attempt_count||0} 次，费用未知。`;
 try{const trace=(await file('retrieval.jsonl')).trim().split('\n').filter(Boolean).map(JSON.parse);$('retrievalTrace').textContent=pretty(trace.slice(0,20));}catch(e){$('retrievalTrace').textContent='此任务未提供检索明细，可加载原始生成运行查看。';}
 const answerIndex=await api('runs/'+run+'/results?view=answers');
 answers=(await file(answerIndex.answer_tables[0]||'predictions.jsonl')).trim().split('\n').filter(Boolean).map(JSON.parse);
 if(answerIndex.answer_tables[0]==='scored-answers.jsonl'){const raw=(await file('predictions.jsonl')).trim().split('\n').filter(Boolean).map(JSON.parse);const byTask=new Map(raw.map(row=>[row.task_id,row]));answers=answers.map(row=>({...byTask.get(row.task_id),...row}));}
 const previousCondition=$('conditionFilter').value;const conditionOptions=['all',...new Set(answers.map(r=>r.condition))];
 $('conditionFilter').replaceChildren(...conditionOptions.map(value=>el('option',{value,textContent:value==='all'?'全部条件':value})));
 $('conditionFilter').value=conditionOptions.includes(previousCondition)?previousCondition:'all';answerPage=0;renderAnswers();
 if(runKind==='demo')renderDemoAnswers();
 $('downloads').replaceChildren(...[...summary.answer_tables,...summary.exports].map(name=>el('a',{href:'/api/v1/runs/'+run+'/files/'+name,textContent:'下载 '+name,download:name})));
 try{const units=await api('runs/'+run+'/evidence');$('evidence').replaceChildren(...units.map(u=>{const d=el('div',{className:'evidence',textContent:u.evidence_id+'\n'+u.text});if(u.image_url)d.append(el('img',{src:u.image_url,alt:u.evidence_id+' 对应范围裁图',loading:'lazy'}));return d;}));}catch(e){$('evidence').textContent='正式逐题证据请依据封存的检索记录复核；此处仅展示开发材料。';}
 stepStatus('results',(syntheticOutput?'软件测试 · ':'')+(scored?'已评分':'已读取 · 未评价')+` · ${answers.length} 条`,syntheticOutput?'warn':'ok');
 notice(summary.engineering.synthetic?'软件测试输出（synthetic），不代表正式实验成绩。':'已读取封存输出。',summary.engineering.synthetic?'warn':'ok');
}
const needRun=fn=>async()=>{if(!run)throw Error('请先运行或加载一个运行 ID');await fn();};
bind('results',needRun(results));
bind('verify',needRun(async()=>{const origin=await api('runs/'+run+'/origin'),q=request('verify');q.profile_id=origin.profile_id;q.resource_set_id=origin.resource_set_id;await start(q,'resultsBlocks');}));
bind('export',needRun(async()=>{const result=await api('runs/'+run+'/export',{});renderBlocks('resultsBlocks',result.exported?[]:[...result.blocking_items,...(result.missing||[]).map(m=>'缺少 '+m)],'尚不能导出');notice(result.exported?'课程专用包已生成：'+result.archive:'尚不能导出：'+joinReasons(result.blocking_items)+(result.missing?'；缺少 '+result.missing.join('、'):''),result.exported?'ok':'warn');}));

// ---------- start ----------
(async()=>{try{
 const caps=await refreshProfiles();session=caps.session_id;
 $('connection').textContent='本机服务已连接';$('connection').className='pill ok';
 const defaults=await api('demo-defaults');$('resourcePath').value=defaults.legacy_root;$('outputDirectory').value=defaults.workspace;$('workspaceStatus').textContent='当前结果位置：'+defaults.workspace;
 const s=caps.settings||{};$('endpoint').value=s.base_url||'';$('model').value=s.model||'';$('secondary').value=s.secondary_model||'';
 notice('本机服务已连接。先在「配置与资源」登记开发材料，即可运行规则问答。');
 if(resource){const report=await api('resources/'+resource);await loadExamples();resourceSummary(report);notice('本机服务已连接，已恢复上次登记的资料。');}
 if(run){$('runId').value=run;await refresh();}
}catch(e){$('connection').textContent='未连接';$('connection').className='pill err';notice(e.message,'error');}})();
