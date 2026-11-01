'use strict';
const $ = id => document.getElementById(id);
let match = null, roster = [], reviewData = null, candidates = null, busy = false;
const names = {}, zoneNames = {};
const actionNames = {MOVE:'机动',HOLD:'固守',OBSERVE:'观察',ATTACK:'攻击',SUPPORT:'支援',WITHDRAW:'撤退',REST:'休整'};
const errors = {MATCH_VERSION_CONFLICT:'对局已更新，已重新加载。请检查最新情报后操作。',TURN_IN_PROGRESS:'上一回合正在结算，请稍后重试。',MATCH_FINISHED:'演习已结束。',INVALID_INPUT:'输入格式不正确，请检查表单。',INVALID_ORDER_EXPIRY:'有效回合必须晚于当前回合。',INVALID_SUPERSEDES:'替换目标必须是同一单位的旧命令。',IDEMPOTENCY_KEY_REUSED:'请求标识与内容不符，请刷新页面。',REVIEW_ALREADY_COMPLETED:'本局经历已确认，可在角色详情中停用。'};
function node(tag, text, cls) { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; }
function notice(text, retry=false) { const n=$('notice'); n.replaceChildren(node('span',text)); n.hidden=false; if(retry){const b=node('button','重试同一个请求','secondary');b.onclick=()=>run(resumePending);n.append(b);} }
function show(section){ for(const id of ['prepare','game','review']) $(id).hidden=id!==section; }
function pending(){try{return JSON.parse(localStorage.getItem('battle-pending'));}catch{return null;}}
async function request(path, method='GET', body, key){
  let response;
  try {response=await fetch(path,{method,headers:{'Content-Type':'application/json',...(key?{'Idempotency-Key':key}:{})},body:body===undefined?undefined:JSON.stringify(body)});}
  catch{throw new Error('NETWORK');}
  const data=await response.json();
  if(!response.ok){const e=new Error(data.error||'SERVER_ERROR');e.http=true;throw e;}
  return data;
}
async function completeMutation(p){
  let data;
  try{data=await request(p.path,p.method,p.body,p.key);}catch(e){if(e.http)localStorage.removeItem('battle-pending');throw e;}
  if(p.path==='/api/matches')localStorage.setItem('battle-match',data.id);
  if(data.job_id){
    localStorage.setItem('battle-job',data.job_id);
    const job=await request('/api/jobs/'+data.job_id);
    if(job.state==='interrupted'||job.state==='failed')notice('上次结算未提交，存档停留在最后完整回合。你可以重新推进。');
    else if(job.state!=='completed')throw new Error('TURN_PENDING');
    localStorage.removeItem('battle-job');
  }
  localStorage.removeItem('battle-pending');
  return data;
}
async function mutate(path,body,method='POST'){
  if(pending())throw new Error('PENDING_EXISTS');
  const p={path,body,method,key:crypto.randomUUID()};
  localStorage.setItem('battle-pending',JSON.stringify(p));
  return completeMutation(p);
}
async function resumePending(){const p=pending();if(p)await completeMutation(p);await loadCurrent();}
async function run(fn){
  if(busy)return;
  busy=true;$('notice').hidden=true;
  document.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{await fn();}catch(e){
    if(e.message==='NETWORK'||e.message==='TURN_PENDING'||e.message==='PENDING_EXISTS')notice('连接中断或上次请求仍待确认。请求已保存，重试不会重复推进。',true);
    else {notice(errors[e.message]||'操作未完成：'+e.message);if(match)try{await refresh();}catch{}}
  }finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);if(candidates?.reviewed)$('save-memories').disabled=true;}
}
async function loadRoster(){
  roster=await request('/api/commanders');$('roster').replaceChildren();
  for(const c of roster){
    names[c.unit_id]=c.name;names[c.id]=c.name;
    const card=node('article',undefined,'roster-card');
    const check=node('label',undefined,'check');const input=node('input');input.type='checkbox';input.checked=true;input.value=c.id;input.name='commander';input.setAttribute('aria-label','选用'+c.name);check.append(input,node('span','参演'));
    const top=node('div',undefined,'roster-top');top.append(node('span',c.name[0],'avatar'));card.append(top,check,node('h3',c.name),node('p',c.role,'role'),node('p',c.trait,'trait'));
    const details=node('button',`查看经历 · ${c.memories.filter(m=>m.active).length} 条已启用 →`);details.onclick=()=>commanderDetail(c.id);card.append(details);$('roster').append(card);
  }
}
function commanderDetail(id){
  const c=roster.find(c=>c.id===id);const box=$('commander-detail');box.replaceChildren(node('h2',c.name+' / '+c.role),node('p',c.trait));
  const u=match?.known_units.find(u=>u.commander_id===id);
  if(u)box.append(node('p',`最后已知：${zoneNames[u.zone]}，人员 ${u.personnel}，弹药 ${u.ammo}。观察于第 ${u.observed_tick} 回合，${u.received_tick} 回合抵达。已确认命令：${u.confirmed_order_id||'初始任务'}。`));
  box.append(node('h3','可审阅的演习经历'));
  if(!c.memories.length)box.append(node('p','还没有保留经历。演习后可以确认下属实际知道的合作偏好。'));
  for(const m of c.memories){
    const p=node('div',undefined,'candidate');p.append(node('p',m.text),node('small',`来源演习 ${m.source_match_id.slice(0,8)} · 事件 ${m.source_event_ids.join(', ')} · ${m.active?'启用':'停用'}`));
    const b=node('button',m.active?'停用此经历':'启用此经历','secondary');b.onclick=()=>run(async()=>{await mutate('/api/memories/'+encodeURIComponent(m.id),{active:!m.active},'PATCH');await loadRoster();commanderDetail(id);});p.append(b);box.append(p);
  }
  if(!$('commander-dialog').open)$('commander-dialog').showModal();
}
function svg(tag,attrs,text){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;}
function drawMap(){
  const map=$('map');map.replaceChildren();const pos={};
  for(const z of match.zones)pos[z.id]=[z.x*4.2+20,z.y*3.8+10];
  map.append(svg('path',{d:'M0 208 Q100 180 210 212 T460 205 L460 245 Q330 270 210 242 T0 248 Z',class:'river'}));
  for(const z of match.zones)for(const other of z.adjacent)if(z.id<other)map.append(svg('line',{x1:pos[z.id][0],y1:pos[z.id][1],x2:pos[other][0],y2:pos[other][1],class:'road'}));
  const contactByZone={};
  for(const r of match.reports)for(const c of r.contacts||[]){if(!contactByZone[c.zone]||c.observed_tick>=contactByZone[c.zone].observed_tick)contactByZone[c.zone]=c;}
  for(const z of match.zones){
    const [x,y]=pos[z.id];map.append(svg('rect',{x:x-42,y:y-15,width:84,height:30,rx:4,class:z.id===match.bridge?'zone-bridge':'zone'}));map.append(svg('text',{x,y:y+4,'text-anchor':'middle'},z.name));
    const units=match.known_units.filter(u=>u.zone===z.id);
    units.forEach((u,i)=>{const ux=x+(i-(units.length-1)/2)*29;map.append(svg('circle',{cx:ux,cy:y-31,r:11,class:'unit-dot'}),svg('text',{x:ux,y:y-27,'text-anchor':'middle',class:'unit-letter'},names[u.id]?.[0]||'?'));});
    const c=contactByZone[z.id];if(c)map.append(svg('text',{x,y:y+31,'text-anchor':'middle',class:'contact-text'},`△ 约${c.estimated_personnel}人 · T${c.observed_tick}`));
  }
}
function reportNode(r){
  const box=node('article',undefined,'report');const meta=node('div',undefined,'report-meta');
  meta.append(node('strong',names[r.sender]||r.sender),node('span',`观察 T${r.observed_tick} → 抵达 T${r.received_tick}`));
  if(r.historical||match.tick-r.observed_tick>2)meta.append(node('span','历史 / 已过时','badge'));
  box.append(meta,node('p',r.text));
  for(const c of r.contacts||[])box.append(node('div',`${zoneNames[c.zone]}：估计约 ${c.estimated_personnel} 人（行动前观察）`,'fine'));
  return box;
}
function refreshOrderChoices(){
  const unit=$('unit').value;const previous=$('supersedes').value;$('supersedes').replaceChildren(new Option('不替换（按优先级选择）',''));
  for(const o of match.orders.filter(o=>o.unit_id===unit))$('supersedes').add(new Option(`${o.id} ${actionNames[o.action]} · ${o.public_status}`,o.id));
  if([...$('supersedes').options].some(o=>o.value===previous))$('supersedes').value=previous;
}
function renderMatch(){
  show('game');for(const z of match.zones)zoneNames[z.id]=z.name;
  $('tick').textContent=String(match.tick).padStart(2,'0');$('clock').textContent=`/ ${match.max_ticks} 回合 · ${match.tick*match.minutes_per_tick} 分钟`;
  $('radio').textContent='通信：'+match.communication;$('progress').textContent=match.status==='active'?'演习进行中':'演习已结束';
  $('outcome').hidden=match.status==='active';$('outcome').textContent=match.outcome||'';
  $('advance').hidden=$('end').hidden=$('orders-panel').hidden=match.status!=='active';$('show-review').hidden=match.status==='active';
  drawMap();$('units').replaceChildren();
  const selected=$('unit').value;$('unit').replaceChildren();$('target').replaceChildren();
  for(const z of match.zones)$('target').add(new Option(z.name,z.id));$('target').value='bridge';
  for(const u of match.known_units){
    $('unit').add(new Option(names[u.id]+' / '+u.id,u.id));
    const box=node('article',undefined,'unit-card');const b=node('button',`${names[u.id]} ↗`);b.onclick=()=>commanderDetail(u.commander_id);box.append(b,node('p',`${zoneNames[u.zone]} · 人员 ${u.personnel} · 弹药 ${u.ammo} · 士气 ${u.morale}`),node('p',`观察 T${u.observed_tick} / 抵达 T${u.received_tick} · ${match.tick-u.observed_tick>2?'已过时':'最后已知'} · ${actionNames[u.current_action]}`));$('units').append(box);
  }
  if([...$('unit').options].some(o=>o.value===selected))$('unit').value=selected;
  refreshOrderChoices();$('expiry').min=match.tick+1;$('expiry').max=match.max_ticks;if(Number($('expiry').value)<=match.tick)$('expiry').value=match.max_ticks;
  $('report-count').textContent=match.reports.length+' 条已送达';$('reports').replaceChildren();
  for(const r of [...match.reports].reverse())$('reports').append(reportNode(r));
  if(!match.reports.length)$('reports').append(node('p','无线电安静。出发前位置已记录；推进回合后，报告仍需要时间送达。','empty'));
  $('order-history').replaceChildren();for(const o of [...match.orders].reverse()){
    const p=node('div',undefined,'order-item');p.append(node('strong',`${o.id} · ${names[o.unit_id]} · ${actionNames[o.action]}`),node('div',`${o.direct?'越级直达':'正常链路'} / ${zoneNames[o.target]} / ${o.public_status} / T${o.expires_tick} 到期`));if(o.text)p.append(node('div',o.text));$('order-history').append(p);
  }
}
async function refresh(){if(match)match=await request('/api/matches/'+match.id);renderMatch();}
async function loadCurrent(){const mid=localStorage.getItem('battle-match');if(mid){match=await request('/api/matches/'+mid);renderMatch();}else show('prepare');}
function factText(e){
  const p=e.payload, who=names[e.actor_id]||e.actor_id;
  switch(e.type){case'MOVED':return `${who}：${zoneNames[p.from]} → ${zoneNames[p.to]}`;case'FIRED':return `${who} 向 ${names[p.target]||p.target} 射击，造成 ${p.loss} 人损失（演习内）。`;case'DECISION':return `${who}：${actionNames[p.action]}。${p.reason}`;case'COMMAND_RECEIVED':return `${who} 收到 ${p.id}，${p.direct?'越级命令，上级稍后同步':'正常指挥链路'}。`;case'ORDER_SUBMITTED':return `总部提交 ${p.id}，尚未送达。`;case'ORDER_EXPIRED':return `${p.order_id} 过期，未执行。`;case'MESSAGE_DELIVERED':return `${p.message_id} 已送达 ${who}${p.historical?'（历史情报）':''}。`;case'REPORT_CREATED':return `${who} 发出 ${p.message_id}：${p.text}`;case'MATCH_FINISHED':return p.outcome;case'MATCH_ABORTED':return '玩家主动结束演习。';case'MATCH_CREATED':return `演习开始，种子 ${p.seed}。`;case'MOVE_BLOCKED':return `${who}：${p.reason}`;default:return e.type;}
}
function renderTimeline(){
  const t=Number($('timeline').value);$('review-tick').textContent=`T${t} / ${t*match.minutes_per_tick} 分钟`;$('facts').replaceChildren();$('then-reports').replaceChildren();
  for(const e of reviewData.events.filter(e=>e.tick===t)){const box=node('article',undefined,'fact');box.append(node('small',`事件 #${e.seq} · ${e.type}`),node('div',factText(e)));$('facts').append(box);}
  if(!$('facts').children.length)$('facts').append(node('p','此回合没有记录。','empty'));
  for(const r of reviewData.reports.filter(r=>r.received_tick<=t).reverse())$('then-reports').append(reportNode(r));
  if(!$('then-reports').children.length)$('then-reports').append(node('p','总部尚未收到战报，只有出发前编组信息。','empty'));
}
async function openReview(){
  [reviewData,candidates]=await Promise.all([request(`/api/matches/${match.id}/replay`),request(`/api/matches/${match.id}/memory-candidates`)]);
  show('review');$('review-outcome').textContent=match.outcome;$('replay-proof').textContent=`${reviewData.verified?'✓ 事件重放校验通过':'重放校验失败'} · 种子 ${reviewData.seed} · ${reviewData.ruleset_version}\nSHA-256 ${reviewData.state_hash}`;
  $('timeline').max=match.tick;$('timeline').value=match.tick;renderTimeline();$('memory-candidates').replaceChildren();
  for(const c of candidates.candidates){const box=node('div',undefined,'candidate');const label=node('label',undefined,'check');const cb=node('input');cb.type='checkbox';cb.value=c.id;cb.name='memory';cb.checked=candidates.reviewed?candidates.accepted_ids.includes(c.id):true;cb.disabled=candidates.reviewed;label.append(cb,node('span',(names[c.commander_id]||c.commander_id)+'：'+c.text));box.append(label,node('small',`来源：本局已送达命令，事件 #${c.source_event_ids.join(', #')}`));$('memory-candidates').append(box);}
  if(!candidates.candidates.length)$('memory-candidates').append(node('p','本局没有符合条件的沟通经历。下一场可下达“撤退时先报告”的结构化任务。','fine'));
  $('save-memories').disabled=candidates.reviewed;$('memory-result').textContent=candidates.reviewed?'已经确认，重复操作不会新增经历。':'';window.scrollTo(0,0);
}
$('create-form').onsubmit=e=>{e.preventDefault();run(async()=>{const selected=[...document.querySelectorAll('input[name=commander]:checked')].map(e=>e.value);if(!selected.length)throw new Error('请至少选一位指挥官');await mutate('/api/matches',{seed:Number($('seed').value),commander_ids:selected,use_memories:$('use-memories').checked,provider_mode:'rules'});await loadCurrent();window.scrollTo(0,0);});};
$('order-form').onsubmit=e=>{e.preventDefault();run(async()=>{await mutate(`/api/matches/${match.id}/orders`,{match_version:match.version,unit_id:$('unit').value,action:$('action').value,target:$('target').value,priority:Number($('priority').value),expires_tick:Number($('expiry').value),retreat_threshold:Number($('threshold').value),silent_until:Number($('silent').value),direct:$('direct').checked,autonomy:$('autonomy').checked,report_before_withdraw:$('report-before').checked,supersedes_id:$('supersedes').value||null,text:$('order-text').value});await refresh();notice('任务已提交，正在传输。收到回执后才能确认执行。');});};
$('unit').onchange=refreshOrderChoices;
$('advance').onclick=()=>run(async()=>{await mutate(`/api/matches/${match.id}/advance`,{match_version:match.version});await refresh();});
$('end').onclick=()=>{if(confirm('中止当前演习？结果将标记为中止，并开放复盘。'))run(async()=>{await mutate(`/api/matches/${match.id}/end`,{match_version:match.version});await refresh();});};
$('show-review').onclick=()=>run(openReview);$('timeline').oninput=renderTimeline;
$('save-memories').onclick=()=>run(async()=>{const accepted_ids=[...document.querySelectorAll('input[name=memory]:checked')].map(e=>e.value);await mutate(`/api/matches/${match.id}/memory-review`,{accepted_ids});await loadRoster();await openReview();});
$('new-match').onclick=()=>run(async()=>{localStorage.removeItem('battle-match');match=null;candidates=null;await loadRoster();show('prepare');window.scrollTo(0,0);});
$('back-game').onclick=()=>renderMatch();$('close-dialog').onclick=()=>$('commander-dialog').close();
window.addEventListener('offline',()=>notice('浏览器已离线。对局不会自动推进，连接恢复后可重试原请求。',!!pending()));
run(async()=>{await loadRoster();if(pending())await resumePending();else await loadCurrent();});
