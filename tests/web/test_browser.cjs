/* Optional real-browser regressions. Install Playwright only in the test environment. */
const test=require('node:test'),assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
let chromium;try{({chromium}=require('playwright'));}catch{}
const root=path.join(__dirname,'../..');

async function fixture(t,{background=true,outage=false,applyOutage=false}={}){
 const server=http.createServer((req,res)=>{
  const file={'/':'web/index.html','/web/app.js':'web/app.js','/web/state.js':'web/state.js','/web/styles.css':'web/styles.css'}[req.url.split('?')[0]];
  if(!file){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(path.join(root,file)));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 const browser=await chromium.launch({headless:true,...(process.env.RAG_BROWSER_CHANNEL?{channel:process.env.RAG_BROWSER_CHANNEL}:{})});const page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'reduce'});
 t.after(()=>browser.close());
 const state={posts:0,gets:0,writes:0,complete:false,dataGets:0,profile:{profile_id:'formal-v1',status:'draft',models:{primary:'synthetic-model'},concurrency:1,provider:{endpoint:'https://example.invalid/responses'}}};
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const report={...(applyOutage?{effective_provider:{endpoint:'https://example.invalid/responses'}}:{}),ready:true,single:{ready:true},vision:{ready:true},parallel:{concurrency:1,success_count:1},sent_attempts:2,blocking_items:[]};
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname.slice('/api/v1/'.length);let result={};
  if(p==='capabilities')result={session_id:'synthetic-session',workspace:'/synthetic-workspace',background_checks:background,profiles:state.profile.profile_id==='formal-v1'?[state.profile]:[{profile_id:'formal-v1',status:'draft'},state.profile],settings:{}};
  else if(p==='demo-defaults')result={legacy_root:'/synthetic',workspace:'/synthetic-workspace'};
  else if(p==='data/status'){
   state.dataGets++;if(outage&&state.dataGets===1){await route.abort();return;}
   result={state:'ready',ready:true,formal_root:'/synthetic'};
  }else if(p==='resources/register'||p==='resources/demo')result={resource_set_id:'demo',ready_for:['demo'],resources:[{registered_root:'/synthetic'}],blocking_items:[]};
  else if(p==='resources/demo/examples')result=[];
  else if(p==='runs')result={runs:[]};
  else if(p==='settings'){state.writes++;result=request.postDataJSON();}
  else if(p==='credentials/session')result={credential_ref:'synthetic-credential'};
  else if(p.startsWith('profiles/')){if(applyOutage&&request.method()==='PUT'){applyOutage=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({message:'synthetic writeback outage'})});return;}state.profile={...state.profile,...request.postDataJSON(),profile_id:'custom-synthetic',status:'draft'};result=state.profile;}
  else if(p==='provider/check'){
   if(state.rejectCheck){await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({message:'synthetic check failure'})});return;}
   state.posts++;state.operation=request.headers()['x-operation-id'];
   result=background?{operation_id:state.operation,state:'running'}:report;
  }else if(p.startsWith('operations/')){
   state.gets++;result={operation_id:state.operation,state:state.complete?'completed':'running',result:report};
  }
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)});
 });
 const url='http://127.0.0.1:'+server.address().port;
 await page.goto(url);return {page,state,errors};
}

test('browser reconnects initial data outage and remains usable with keyboard and narrow viewport',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t,{outage:true});
 await page.waitForFunction(()=>document.getElementById('dataRetry').hidden===false);
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false,{},{timeout:10000});
 assert.ok(state.dataGets>=2);
 await page.locator('#rail-demo').click();
 assert.equal(await page.locator('#scene-demo').evaluate(node=>node.inert),false);
 await page.keyboard.press('ArrowRight');
 assert.equal(await page.locator('#rail-experiment').getAttribute('aria-current'),'step');
 await page.setViewportSize({width:390,height:844});
 await page.waitForFunction(()=>!document.getElementById('scene-config').inert);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 assert.deepEqual(errors,[]);
});

test('browser refresh resumes a paid check through GET without another POST',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t);
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('recoverOperation').hidden===false);
 assert.equal(state.posts,1);
 await page.reload();state.complete=true;
 await page.waitForFunction(()=>document.getElementById('providerCheckStatus').textContent.includes('实际调用 2 次'));
 assert.equal(state.posts,1);assert.ok(state.gets>=2);
 assert.equal(await page.locator('#check').isEnabled(),true);
 assert.deepEqual(errors,[]);
});

test('new static page retains synchronous compatibility with an already running server',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t,{background:false});
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('providerCheckStatus').textContent.includes('实际调用 2 次'));
 assert.equal(state.posts,1);assert.equal(state.gets,0);assert.deepEqual(errors,[]);
});

test('completed check remains recoverable when negotiated configuration writeback fails',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t,{applyOutage:true});state.complete=true;
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('synthetic writeback outage'));
 assert.equal(state.posts,1);assert.equal(await page.locator('#recoverOperation').isVisible(),true);
 await page.locator('#recoverOperation').click();
 await page.waitForFunction(()=>document.getElementById('providerCheckStatus').textContent.includes('实际调用 2 次'));
 assert.equal(state.posts,1);assert.equal(await page.locator('#recoverOperation').isVisible(),false);assert.deepEqual(errors,[]);
});

test('configuration states follow edits, save, check and edits after check',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t);state.complete=true;
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#model').fill('edited-model');
 assert.match(await page.locator('#modelState').textContent(),/未保存修改/);
 await page.locator('#save').click();
 await page.waitForFunction(()=>document.getElementById('modelState').textContent.includes('已保存'));
 assert.equal(await page.locator('#save').textContent(),'保存配置');
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('modelState').textContent.includes('检查通过'));
 await page.locator('#concurrency').fill('2');
 assert.match(await page.locator('#modelState').textContent(),/未保存修改/);
 assert.match(await page.locator('#modelNext').textContent(),/重新检查/);
 assert.equal(state.posts,1);assert.deepEqual(errors,[]);
});

test('invalid fields are explained inline before writes; a typed key can be revealed but is cleared after save',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t);
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#model').fill('');await page.locator('#save').click();
 assert.equal(await page.locator('#model').getAttribute('aria-invalid'),'true');
 assert.match(await page.locator('#modelError').textContent(),/模型名称/);
 assert.equal(state.writes,0);assert.equal(state.posts,0);
 await page.locator('#model').fill('valid-model');
 await page.locator('#key').fill('synthetic-test-key');assert.match(await page.locator('#modelState').textContent(),/未保存修改/);await page.locator('#toggleKey').click();
 assert.equal(await page.locator('#key').getAttribute('type'),'text');
 await page.locator('#save').click();
 await page.waitForFunction(()=>document.getElementById('keyStatus').textContent.includes('本次会话已设置'));
 assert.equal(await page.locator('#key').inputValue(),'');assert.equal(await page.locator('#key').getAttribute('type'),'password');
 await page.locator('#key').fill('synthetic-replacement');assert.match(await page.locator('#modelState').textContent(),/未保存修改/);
 assert.deepEqual(errors,[]);
});

test('formal page exposes the same pending check recovery and never resubmits it',{skip:!chromium},async t=>{
 const {page,state,errors}=await fixture(t);
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('recoverOperation').hidden===false);
 await page.locator('#rail-experiment').click();
 assert.equal(await page.locator('#recoverOperationFormal').isVisible(),true);
 assert.match(await page.locator('#operationStatusFormal').textContent(),/检查/);
 assert.equal(state.posts,1);assert.deepEqual(errors,[]);
});

test('checking locks both recovery buttons and preserves inline error feedback',{skip:!chromium},async t=>{
 const {page,state}=await fixture(t);
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('recoverOperation').hidden===false);
 assert.equal(await page.locator('#recoverOperation').isDisabled(),true);
 await page.locator('#rail-experiment').click();
 assert.equal(await page.locator('#recoverOperationFormal').isDisabled(),true);
 assert.equal(state.posts,1);
});

test('a failed recheck cannot retain a previous success badge or next-step instruction',{skip:!chromium},async t=>{
 const {page,state}=await fixture(t);state.complete=true;
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('modelState').textContent.includes('检查通过'));
 state.rejectCheck=true;await page.locator('#check').click();
 await page.waitForFunction(()=>document.getElementById('configError').textContent.includes('synthetic check failure'));
 assert.match(await page.locator('#modelState').textContent(),/需重新检查/);
 assert.notEqual(await page.locator('#modelState').getAttribute('data-tone'),'ok');
 assert.doesNotMatch(await page.locator('#modelNext').textContent(),/检查通过/);
});

test('feedback has reserved space and all form controls fit laptop and zoom-equivalent layouts',{skip:!chromium},async t=>{
 const {page,errors}=await fixture(t);
 await page.waitForFunction(()=>document.getElementById('reviewed').disabled===false);
 await page.setViewportSize({width:1024,height:768});
 await page.locator('#model').fill('');await page.locator('#save').click();
 await page.waitForTimeout(100);
 const notice=await page.locator('#notice').boundingBox(),viewport=await page.locator('#viewport').boundingBox();
 assert.ok(notice.y+notice.height<=viewport.y+1);
 for(const size of [{width:640,height:450},{width:390,height:844}]){
  await page.setViewportSize(size);await page.locator('#model').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  for(const id of ['endpoint','model','key','toggleKey','save']){
   const box=await page.locator('#'+id).boundingBox();assert.ok(box.x>=0&&box.x+box.width<=size.width+1,id);
  }
 }
 assert.deepEqual(errors,[]);
});
