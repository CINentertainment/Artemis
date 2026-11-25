(function(root){'use strict';
const PRESETS=[
 {name:'Conflict & civilian impacts',topic:'recent armed conflict'},
 {name:'Natural disasters',topic:'recent natural disasters'},
 {name:'Public health outbreaks',topic:'recent disease outbreaks'},
 {name:'Critical infrastructure',topic:'recent critical infrastructure disruptions'},
 {name:'Economic disruption',topic:'recent economic disruptions'},
 {name:'Cybersecurity incidents',topic:'recent cybersecurity incidents'}
];
// Reuses the durable server scheduler and delivery worker; no browser timers trigger research.
async function requisitions(api){const rows=[];for(let offset=0;offset<1000;offset+=100){const batch=await api('/research/requisitions?limit=100&offset='+offset);rows.push(...batch);if(batch.length<100)return rows;}throw Error('More than 1000 requisitions: use Research archive to select a topic. No new watch topic was created.');}
async function ensureWatch(api,preset,minutes,delay){
 let reqs=await requisitions(api),req=reqs.find(r=>r.topic===preset.topic);
 if(!req)req=await api('/research/requisitions','POST',{topic:preset.topic,questions:[]});
 let boards=await api('/intelligence/boards'),summary=boards.find(b=>b.title==='World watch · '+preset.name),board;
 if(summary)board=await api('/intelligence/boards/'+summary.id);
 else board=await api('/intelligence/boards','POST',{title:'World watch · '+preset.name});
 if(!board.research_bindings?.[req.id])board=await api('/intelligence/boards/'+board.id+'/research','POST',{version:board.version,action:'bind',reference:req.id});
 const schedule=await api('/research/requisitions/'+req.id+'/schedule');
 if(!schedule.enabled||schedule.interval_minutes!==minutes)await api('/research/requisitions/'+req.id+'/schedule','PUT',{enabled:true,interval_minutes:minutes,initial_delay_minutes:delay});
 return {req,board};
}
const exported={PRESETS,ensureWatch,requisitions};if(typeof module!=='undefined'&&module.exports){module.exports=exported;return;}root.ArtemisAutomation=exported;
const $=id=>document.getElementById(id),el=(tag,value)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=value;return n;};
let busy=false,reqs=[],boards=[],offset=0;
async function api(url,method='GET',body){const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Request failed ('+response.status+').');return data;}
function message(value){$('message').textContent=value;}
function link(label,url){const a=el('a',label);a.href=url;return a;}
function button(label,fn){const b=el('button',label);b.type='button';b.onclick=()=>action(fn);return b;}
async function action(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();}catch(e){message(e.message+' Any earlier saved steps remain saved; refresh status before retrying.');}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
function date(value){return value?new Date(value).toLocaleString():'None';}
function interval(value){const s=el('select');for(const [v,label]of [[360,'Every 6 hours'],[720,'Every 12 hours'],[1440,'Every day'],[10080,'Every week']]){const o=el('option',label);o.value=v;s.append(o);}if(![...s.options].some(o=>o.value===String(value))){const o=el('option','Every '+value+' minutes');o.value=value;s.append(o);}s.value=String(value||1440);return s;}
for(const [i,p]of PRESETS.entries()){const card=el('article');card.className='card';const label=el('label');label.className='check';const checkbox=el('input');checkbox.type='checkbox';checkbox.checked=true;checkbox.dataset.preset=i;label.append(checkbox,el('strong',p.name));card.append(label,el('p',p.topic));$('starter').append(card);}
function selected(){return [...document.querySelectorAll('[data-preset]')].filter(c=>c.checked).map(c=>PRESETS[Number(c.dataset.preset)]);}
async function loadTopics(){
 reqs=await requisitions(api);const summaries=await api('/intelligence/boards');boards=[];
 for(const b of summaries)boards.push(await api('/intelligence/boards/'+b.id));
 $('topics').replaceChildren();if(!reqs.length)$('topics').append(el('p','No standing topics yet. Start the selected world watch topics above.'));
 for(const req of reqs.slice(0,100)){const card=el('article');card.className='card';card.append(el('h3',req.topic),el('p',req.reference||req.id));
 try{const schedule=await api('/research/requisitions/'+req.id+'/schedule'),minutes=interval(schedule.interval_minutes);card.append(el('p',(schedule.enabled?'Monitoring enabled':'Paused')+' · Next attempt: '+date(schedule.next_run_at)),el('p','Last status: '+(schedule.last_status||'Not run yet')+' · Finished: '+date(schedule.last_finished_at)));
 if(schedule.last_error)card.append(el('p','Attention: '+schedule.last_error));
 const bound=boards.filter(b=>b.research_bindings?.[req.id]);if(!bound.length)card.append(el('p','No board assignment: research can run, but workspace candidate delivery needs a board binding.'));
 for(const b of bound)card.append(link('Open '+b.title,'/intelligence#board='+encodeURIComponent(b.id)),el('p','Delivery destination: '+b.title));
 const controls=el('div');controls.className='controls';controls.append(minutes,button(schedule.enabled?'Update interval':'Enable monitoring',async()=>{await api('/research/requisitions/'+req.id+'/schedule','PUT',{enabled:true,interval_minutes:Number(minutes.value),initial_delay_minutes:0});await loadTopics();message('Monitoring saved. The scheduler will start when available.');}),button('Pause',async()=>{await api('/research/requisitions/'+req.id+'/schedule','PUT',{enabled:false,interval_minutes:schedule.interval_minutes});await loadTopics();message('Monitoring paused. An active run may finish.');}));card.append(controls);
 }catch(e){card.append(el('p','Schedule unavailable: '+e.message));}$('topics').append(card);
 }
 $('topics').append(el('p','Showing up to 100 saved requisitions. All enabled server schedules continue independently of this list.'));
}
async function loadInbox(reset=true){if(reset){offset=0;$('inbox').replaceChildren();}$('update-detail').replaceChildren();const data=await api('/research/updates?limit=20&offset='+offset+'&unread_only='+$('unread').checked);for(const item of data.items){const card=el('article');card.className='card';card.append(el('h3',item.topic),el('p',(item.reference||item.run_id)+' · '+item.status+' · '+date(item.finished_at)+(item.reviewed?' · reviewed':' · unreviewed')));if(item.error)card.append(el('p',item.error));card.append(button('Open saved result',()=>openUpdate(item.run_id)),button(item.reviewed?'Mark unreviewed':'Mark run reviewed',async()=>{await api('/research/updates/'+item.run_id+'/review','PUT',{reviewed:!item.reviewed});await loadInbox();message('Run review saved. Board candidate reviews were not changed.');}));$('inbox').append(card);}offset+=data.items.length;$('more').hidden=offset>=data.total_count;if(!offset)$('inbox').append(el('p','No matching saved runs. Scheduled results will appear after collection finishes.'));}
async function openUpdate(id){const data=await api('/research/updates/'+id),box=$('update-detail');box.replaceChildren(el('h3',(data.reference||id)+' · '+data.status),el('p',data.topic));if(data.changes?.brief)box.append(el('pre',data.changes.brief));const details=el('details');details.append(el('summary','Full saved output and warnings'),el('pre',JSON.stringify(data.run?.result||data,null,2)));box.append(details);box.scrollIntoView({behavior:'smooth',block:'start'});}
async function refresh(){await loadTopics();await loadInbox();message('Monitoring status and research inbox refreshed.');}
$('start').onclick=()=>action(async()=>{const picks=selected();if(!picks.length)throw Error('Choose at least one watch topic.');let index=0;for(const preset of picks){message('Setting up '+preset.name+'…');await ensureWatch(api,preset,Number($('interval').value),index++*10);}await refresh();message('Selected world watch topics are enabled. The first attempts are staggered; Artemis must remain running.');});
$('pause').onclick=()=>action(async()=>{const picks=selected();if(!picks.length)throw Error('Choose at least one watch topic.');const all=await requisitions(api);for(const p of picks){for(const req of all.filter(r=>r.topic===p.topic)){const s=await api('/research/requisitions/'+req.id+'/schedule');await api('/research/requisitions/'+req.id+'/schedule','PUT',{enabled:false,interval_minutes:s.interval_minutes});}}await refresh();message('Selected watch topics paused. Other standing topics retain their schedules.');});
$('refresh').onclick=()=>action(refresh);$('more').onclick=()=>action(()=>loadInbox(false));$('unread').onchange=()=>action(()=>loadInbox());
$('investigate').onsubmit=e=>{e.preventDefault();const topic=new FormData(e.target).get('topic').trim();action(async()=>{const req=await api('/research/requisitions','POST',{topic,questions:[]});let board=await api('/intelligence/boards','POST',{title:topic.slice(0,150)});board=await api('/intelligence/boards/'+board.id+'/research','POST',{version:board.version,action:'bind',reference:req.id});$('investigation').replaceChildren(el('p','Saved '+(req.reference||req.id)+'. Investigation is running…'),link('Open investigation board','/intelligence#board='+board.id));message('Investigating your event. Existing monitoring remains enabled.');const result=await api('/research/requisitions/'+req.id+'/runs','POST',{});$('investigation').append(el('p','Run '+(result.reference||result.id)+' · '+result.status+'. Board delivery follows separately.'));await refresh();});};
// Refresh only the local display; the server owns all research scheduling.
setInterval(()=>{if(!document.hidden&&!busy&&!$('update-detail').childElementCount&&!['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))action(refresh);},60000);
action(refresh);
})(typeof window!=='undefined'?window:globalThis);
