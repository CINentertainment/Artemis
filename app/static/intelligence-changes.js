/* Read-only comparison of saved board deliveries. No inference, writes or model calls. */
(function(root){
'use strict';
function entries(job){return (job?.report?.notes||[]).filter(n=>n.imported===true&&Number.isInteger(n.note_id)&&typeof n.revision_id==='string');}
function compare(board,target,baseline){
 const prior=new Map(entries(baseline).map(n=>[String(n.note_id),n.revision_id]));
 const seen=new Set(),notes=[];
 for(const item of entries(target)){
  const key=String(item.note_id)+':'+item.revision_id;if(seen.has(key))continue;seen.add(key);
  const note=board.notes[String(item.note_id)],snap=note?.revisions.find(r=>r.id===item.revision_id);
  const state=!baseline?'baseline_not_selected':!prior.has(String(item.note_id))?'not_in_baseline':prior.get(String(item.note_id))===item.revision_id?'same_snapshot':'different_snapshot';
  const current=!!snap&&note.revisions.at(-1).id===snap.id,active=current&&note.status!=='archived';
  const mentions=[],relationships=[],observations=[];
  for(const entity of Object.values(board.entities||{}))for(const m of entity.mentions||[]){if(m.note_id!==item.note_id||m.revision_id!==item.revision_id)continue;
   mentions.push({entity_id:entity.id,name:m.name||entity.name,kind:entity.kind,quote:m.quote,status:m.review_status||(m.origin==='model_candidate'?'pending':'analyst entered'),excluded:!!m.policy_exclusion||!!entity.type_review_needed,reason:m.policy_exclusion|| (entity.type_review_needed?'Identity type requires review.':''),origin:m.origin});}
  for(const l of board.links||[]){if(l.note_id!==item.note_id||l.revision_id!==item.revision_id)continue;relationships.push({id:l.id,left:board.entities[l.left]?.name||'Unknown identity',right:board.entities[l.right]?.name||'Unknown identity',label:l.label,quote:l.role_source_sentence||l.source_sentence||l.quote,status:l.status,excluded:!!(l.role_review_needed||l.endpoint_review_needed||l.identity_self_link||l.stale),reason:l.role_review_reason||'Endpoint, identity or evidence check requires review.'});}
  for(const o of board.observations||[]){if(o.note_id===item.note_id&&o.revision_id===item.revision_id)observations.push({kind:o.kind,text:o.text,status:o.review_status||'pending',quotes:o.quotes||[o.quote]});}
  const pending={mentions:active?mentions.filter(m=>m.origin==='model_candidate'&&m.status==='pending'&&!m.excluded).length:0,relationships:active?relationships.filter(l=>l.status==='proposed'&&!l.excluded).length:0,observations:active?observations.filter(o=>o.status==='pending').length:0};
  notes.push({note_id:item.note_id,revision_id:item.revision_id,state,current,active,title:snap?.record.title||'Snapshot unavailable',body:snap?.record.body||'',status:note?.status||'unavailable',captured_at:snap?.captured_at,processing:item.candidates,mentions,relationships,observations,pending});
 }
 return {notes,pending:notes.reduce((sum,n)=>sum+n.pending.mentions+n.pending.relationships+n.pending.observations,0),unresolved:(target?.report?.notes||[]).filter(n=>!n.imported||!n.revision_id).map(n=>({note_id:n.note_id,status:n.candidates||'unknown',error:n.error||'No confirmed imported snapshot in this delivery report.'})),absent_from_target:entries(baseline).filter(n=>!notes.some(x=>x.note_id===n.note_id)).map(n=>n.note_id)};
}
function render(container,board,rows,options={}){
 container.replaceChildren();const el=(tag,value)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=value;return n;};
 if(!rows.length){container.append(el('p','No recorded research deliveries for this board. Manual imports remain available in Notes and History.'));return;}
 const target=rows.find(r=>r.id===options.target)||rows[0],index=rows.indexOf(target),baseline=rows.find(r=>r.id===options.baseline)|| (options.baseline===''?null:rows[index+1]||null);
 const controls=el('div');controls.className='controls';
 function chooser(label,value,items,change){const wrap=el('label',label),select=el('select');for(const item of items){const o=el('option',item.text);o.value=item.id;select.append(o);}select.value=value;select.onchange=()=>change(select.value);wrap.append(select);controls.append(wrap);}
 const labels=rows.map(r=>({id:r.id,text:(r.reference||r.run_id)+' · '+r.status}));
 chooser('Delivery to inspect',target.id,labels,id=>render(container,board,rows,{...options,target:id,baseline:undefined}));
 chooser('Compare with delivery',baseline?.id||'',[{id:'',text:'No baseline'},...labels.filter(r=>r.id!==target.id)],id=>render(container,board,rows,{...options,target:target.id,baseline:id}));container.append(controls);
 const result=compare(board,target,baseline);
 container.append(el('h3',(target.reference||target.run_id)+' · '+target.status),el('p',target.report?.message||''),el('p',result.notes.length+' confirmed imported snapshot(s) · '+result.pending+' current eligible review item(s). Counts are records, not independent corroboration.'));
 if(['queued','running'].includes(target.status))container.append(el('p','Delivery is still in progress. Refresh after it finishes; this is not a final change summary.'));
 if(!result.notes.length)container.append(el('p','No imported note snapshots are confirmed in this delivery report. This does not establish that no research evidence exists.'));
 const names={baseline_not_selected:'No baseline selected',not_in_baseline:'Note absent from comparison delivery',same_snapshot:'Same saved snapshot as comparison delivery',different_snapshot:'Different saved snapshot from comparison delivery'};
 for(const n of result.notes){const card=el('article');card.className='card';card.append(el('h4','Intel Note '+n.note_id+' · '+n.title),el('p',names[n.state]),el('p','Snapshot '+n.revision_id.slice(0,8)+' · '+(n.captured_at||'Capture time unavailable')+' · '+n.processing+' · Note status: '+n.status));
 if(!n.active)card.append(el('p','Earlier, archived or unavailable snapshot: excluded from the current review queue.'));
 card.append(el('p',n.pending.mentions+' pending named mentions · '+n.pending.relationships+' eligible proposed relationships · '+n.pending.observations+' pending date / infrastructure observations'));
 const open=el('button','Open note and review controls');open.type='button';open.disabled=!board.notes[String(n.note_id)];open.onclick=()=>options.openNote?.(n.note_id);card.append(open);
 const evidence=el('details');evidence.append(el('summary','Saved snapshot evidence'),el('pre',n.body));card.append(evidence);
 const records=el('details');records.append(el('summary','Mentions, relationships and observations attached to this snapshot'));
 for(const m of n.mentions){records.append(el('p',m.name+' · '+m.kind+' · '+m.status+(m.excluded?' · excluded: '+m.reason:'')),el('blockquote',m.quote));}
 for(const l of n.relationships){records.append(el('p',l.left+' → '+l.label+' → '+l.right+' · '+l.status+(l.excluded?' · excluded: '+l.reason:'')),el('blockquote',l.quote));}
 for(const o of n.observations){records.append(el('p',o.kind+': '+o.text+' · '+o.status));for(const q of o.quotes)if(q)records.append(el('blockquote',q));}
 card.append(records);container.append(card);
 }
 for(const n of result.unresolved)container.append(el('p','Intel Note '+n.note_id+' · '+n.status+' · '+n.error));
 if(result.absent_from_target.length)container.append(el('p','Comparison notes not delivered in the selected run: '+[...new Set(result.absent_from_target)].join(', ')+'. They have not been deleted, disproved or resolved by this comparison.'));
}
const api={compare,render};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ArtemisBoardChanges=api;
})(typeof window!=='undefined'?window:globalThis);
