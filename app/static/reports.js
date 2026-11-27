(()=>{'use strict';
const $=id=>document.getElementById(id),el=(tag,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
let busy=false,rows=[];const selected=new Set();
async function api(path='/reports',method='GET',body){const r=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Report request failed.');return d;}
const names={notes:'Intel Notes',risk_tracker:'Risk Tracker',market_lens:'Market Lens'};
function sync(){const n=selected.size;$('delete-reports').textContent=`Delete selected PDFs (${n})`;$('delete-reports').disabled=busy||!n;for(const id of ['select-all-reports','clear-reports'])$(id).disabled=busy;for(const input of document.querySelectorAll('#saved input'))input.disabled=busy;$('generate').querySelector('button').disabled=busy;}
function format(value,zone){try{return new Intl.DateTimeFormat('en-US',{dateStyle:'medium',timeStyle:'short',timeZone:zone||'America/Chicago'}).format(new Date(value));}catch{return value;}}
function render(){
 const available=new Set(rows.map(r=>r.id));for(const id of selected)if(!available.has(id))selected.delete(id);
 $('saved').replaceChildren();
 if(!rows.length)$('saved').append(el('p','No saved PDFs.'));
 for(const r of rows){
  const card=el('article');card.className='card';const label=el('label');label.className='report-select';const checkbox=el('input');checkbox.type='checkbox';checkbox.checked=selected.has(r.id);checkbox.setAttribute('aria-label','Select PDF generated '+format(r.created_at,r.timezone));checkbox.onchange=()=>{if(checkbox.checked)selected.add(r.id);else selected.delete(r.id);sync();};label.append(checkbox,el('span','Select PDF'));
  const a=el('a','Download PDF brief');a.className='artemis-action-link';a.href='/reports/'+encodeURIComponent(r.id)+'/pdf';
  card.append(label,a,el('p',format(r.start,r.timezone)+' – '+format(r.end,r.timezone)+' · '+r.entries+' entries'),el('p','Sources: '+(r.sources?r.sources.map(v=>names[v]||v).join(', '):'Legacy combined report')));
  if((r.warnings||[]).length){const details=el('details');details.append(el('summary','Coverage qualifications'),el('p',r.warnings.join(' · ')));card.append(details);}
  $('saved').append(card);
 }
 sync();
}
async function refresh(){rows=await api();render();}
$('select-all-reports').onclick=()=>{if(busy)return;for(const r of rows)selected.add(r.id);render();};
$('clear-reports').onclick=()=>{if(busy)return;selected.clear();render();};
$('delete-reports').onclick=async()=>{
 if(busy||!selected.size)return;const ids=[...selected];
 if(!window.confirm(`Delete ${ids.length} selected saved PDFs? Their report details will also be deleted. Original notes, Risk Tracker cases and Market Lens records will remain.`))return;
 busy=true;sync();
 try{const result=await api('/reports/delete-selected','POST',{ids});for(const id of result.deleted)selected.delete(id);$('delete-status').textContent=`Deleted ${result.deleted.length} PDFs.`+(result.failed.length?` ${result.failed.length} could not be deleted and remain selected.`:'');await refresh();}catch(e){$('delete-status').textContent=e.message+' Refresh before retrying.';}finally{busy=false;sync();}
};
$('generate').onsubmit=async e=>{
 e.preventDefault();if(busy)return;const sources=[...document.querySelectorAll('#source-options input:checked')].map(n=>n.value);
 if(!sources.length){$('status').textContent='Select at least one source.';return;}
 busy=true;sync();$('status').textContent='Retrieving selected sources and generating the PDF…';
 try{const r=await api('/reports','POST',{hours:Number($('hours').value),timezone:$('zone').value,sources});await refresh();$('status').textContent=`PDF generated with ${r.entries} entries from ${r.sources.map(v=>names[v]).join(', ')}.`+((r.warnings||[]).length?' Coverage qualifications are available below.':'');}catch(error){$('status').textContent=error.message;}finally{busy=false;sync();}
};
refresh().catch(e=>{$('status').textContent=e.message;});
})();
