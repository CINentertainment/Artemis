/* Local, read-only board projection. No dependencies, remote assets, or model calls. */
(function(root){
'use strict';
function project(board,options={}){
 const mode=options.mode||'relationships', status=options.status||'all', needle=(options.search||'').trim().toLocaleLowerCase();
 const roots=board.identity_roots||{}, canonical=id=>roots[id]||id;
 const entities=Object.values(board.presentation?.entities||{});
 const nodes=entities.filter(e=>!e.type_review_needed).map(e=>{
  const mentions=e.mentions.filter(m=>{const note=board.notes[String(m.note_id)];return note&&note.status!=='archived'&&note.revisions.at(-1).id===m.revision_id&&!m.policy_exclusion&&m.review_status!=='rejected';});
  return {id:e.id,name:e.name,kind:e.kind,aliases:e.aliases||[e.name],mentions,note_ids:[...new Set(mentions.map(m=>m.note_id))],label:e.option_label||e.name};
 }).filter(e=>e.mentions.length);
 const index=new Map(nodes.map(n=>[n.id,n]));let edges=[];
 if(mode==='co_mentions'){
  for(const pair of board.matrix||[]){const left=canonical(pair.left),right=canonical(pair.right);if(left===right||!index.has(left)||!index.has(right))continue;
   const note_ids=[...new Set(pair.shared_note_ids||[])].filter(id=>index.get(left).note_ids.includes(id)&&index.get(right).note_ids.includes(id));
   if(note_ids.length)edges.push({id:'co-'+edges.length,left,right,label:note_ids.length+' shared note'+(note_ids.length===1?'':'s'),status:'co_mention',note_ids,kind:'co_mention'});
  }
 }else{
  for(const link of board.links||[]){const left=canonical(link.left),right=canonical(link.right),note=board.notes[String(link.note_id)];
   if(!['proposed','reviewed'].includes(link.status)||!['all',link.status].includes(status)||link.stale||link.endpoint_review_needed||link.role_review_needed||link.identity_self_link||left===right||!index.has(left)||!index.has(right))continue;
   if(link.origin==='model_candidate'&&link.role_review_needed!==false)continue;
   if(!note||note.status==='archived'||note.revisions.at(-1).id!==link.revision_id)continue;
   if(![left,right].every(id=>index.get(id).mentions.some(m=>m.revision_id===link.revision_id)))continue;
   edges.push({id:link.id,left,right,label:link.label,status:link.status,note_ids:[link.note_id],kind:'relationship',record:link});
  }
 }
 let shown=nodes;
 if(needle){const found=new Set(nodes.filter(n=>[n.name,...n.aliases].some(s=>s.toLocaleLowerCase().includes(needle))).map(n=>n.id));
  edges=edges.filter(e=>found.has(e.left)||found.has(e.right));const keep=new Set([...found,...edges.flatMap(e=>[e.left,e.right])]);shown=nodes.filter(n=>keep.has(n.id));
 }else if(!options.isolated){const connected=new Set(edges.flatMap(e=>[e.left,e.right]));shown=nodes.filter(n=>connected.has(n.id));}
 return {mode,nodes:shown,edges,total_nodes:nodes.length};
}
// Stable breadth-first ordering keeps neighbours nearby; fixed cells guarantee clearance.
function layout(nodes,edges){
 const ordered=[...nodes].sort((a,b)=>a.name.localeCompare(b.name)||a.id.localeCompare(b.id));
 const neighbours=new Map(ordered.map(n=>[n.id,new Set()]));
 for(const e of edges){if(neighbours.has(e.left)&&neighbours.has(e.right)){neighbours.get(e.left).add(e.right);neighbours.get(e.right).add(e.left);}}
 const rank=new Map(ordered.map((n,i)=>[n.id,i])),seen=new Set(),sequence=[];
 for(const n of ordered){if(seen.has(n.id))continue;const queue=[n.id];seen.add(n.id);for(let i=0;i<queue.length;i++){const id=queue[i];sequence.push(id);for(const next of [...neighbours.get(id)].sort((a,b)=>rank.get(a)-rank.get(b))){if(!seen.has(next)){seen.add(next);queue.push(next);}}}}
 const columns=Math.max(1,Math.ceil(Math.sqrt(sequence.length||1))),rows=Math.ceil(sequence.length/columns);
 const width=Math.max(620,columns*290+80),height=Math.max(360,rows*180+80),positions=new Map();
 sequence.forEach((id,i)=>positions.set(id,{x:(i%columns+.5)*290+(width-columns*290)/2,y:(Math.floor(i/columns)+.5)*180+(height-rows*180)/2}));
 return {width,height,positions};
}
function fitScale(width,height,viewportWidth,viewportHeight){return Math.max(.05,Math.min(1,(viewportWidth-24)/width,(viewportHeight-24)/height));}
function pointerPosition(canvas,event){const matrix=canvas.getScreenCTM();if(!matrix)return null;const p=canvas.createSVGPoint();p.x=event.clientX;p.y=event.clientY;return p.matrixTransform(matrix.inverse());}
const NS='http://www.w3.org/2000/svg';
function svg(tag,attrs={},text){const n=document.createElementNS(NS,tag);for(const [key,value]of Object.entries(attrs))n.setAttribute(key,String(value));if(text!==undefined)n.textContent=text;return n;}
function text(tag,value){const n=document.createElement(tag);n.textContent=value;return n;}
function render(container,detail,board,options={}){
 container.replaceChildren();detail.replaceChildren();const graph=project(board,options),map=layout(graph.nodes,graph.edges);
 const summary=text('p',graph.nodes.length+' shown identities · '+graph.edges.length+' '+(graph.mode==='co_mentions'?'shared-note connections':'relationship records')+' · '+graph.total_nodes+' eligible identities on board');container.append(summary);
 if(!graph.nodes.length){container.append(text('p',graph.mode==='co_mentions'?'No shared-note connections match these filters.':'No eligible relationships match these filters. Try Both statuses, or show isolated identities.'));return graph;}
 const frame=document.createElement('div');frame.className='chart-scroll';frame.tabIndex=0;frame.setAttribute('aria-label','Scrollable link chart. Select a node or connection; use the accessible lists below as an alternative.');
 const canvas=svg('svg',{viewBox:`0 0 ${map.width} ${map.height}`,width:map.width,height:map.height,role:'group','aria-label':'Artemis intelligence link chart'});canvas.classList.add('link-chart-svg');
 const defs=svg('defs');const marker=svg('marker',{id:'artemis-chart-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#b69bfa'}));defs.append(marker);canvas.append(defs);
 function evidence(ids){for(const id of ids){const note=board.notes[String(id)];if(!note)continue;const snap=note.revisions.at(-1),box=document.createElement('details');box.append(text('summary','Intel Note '+id+' · '+(snap.record.title||'Untitled')),text('pre',snap.record.body));detail.append(box);}}
 function selectNode(node){detail.replaceChildren(text('h3',node.name+' · '+node.kind.replace('_',' ')),text('p','Identity evidence; shared mentions do not establish a relationship.'));if(node.aliases.length>1)detail.append(text('p','Analyst-linked names: '+node.aliases.join(' · ')));
  const seen=new Set();for(const mention of node.mentions){detail.append(text('p','Intel Note '+mention.note_id+' · '+(mention.review_status||'pending review')));const displayed=mention.display_quotes?.length?mention.display_quotes:[mention.quote];for(const quote of displayed){const key=mention.note_id+':'+quote;if(!seen.has(key)){detail.append(text('blockquote',quote));seen.add(key);}}if(mention.display_quotes?.length){const raw=document.createElement('details');raw.append(text('summary','Original stored mention quote'),text('blockquote',mention.quote));detail.append(raw);}}evidence(node.note_ids);}
 function selectEdge(edge){const a=graph.nodes.find(n=>n.id===edge.left),b=graph.nodes.find(n=>n.id===edge.right);detail.replaceChildren(text('h3',a.name+(edge.kind==='co_mention'?' ↔ ':' → ')+b.name),text('p',edge.label+' · '+edge.status.replace('_',' ')));
  if(edge.record){detail.append(text('p','Source-reported proposal; reviewed does not mean independently verified.'),text('blockquote',edge.record.role_source_sentence||edge.record.source_sentence||edge.record.quote));if(edge.record.location_span)detail.append(text('p','Explicit location qualifier: '+edge.record.location_span));
   const raw=document.createElement('details');raw.append(text('summary','Original stored quote'),text('blockquote',edge.record.quote));detail.append(raw);
  }else detail.append(text('p','Co-occurrence in a shared note only; no action, affiliation or causation is asserted.'));
  evidence(edge.note_ids);}
 function interact(node,label,fn){node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',label);node.addEventListener('click',fn);node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();fn();}});}
 const parallel=new Map(),edgeViews=[],nodeViews=new Map();
 function refreshGeometry(){for(const update of edgeViews)update();for(const [id,g]of nodeViews){const p=map.positions.get(id);g.setAttribute('transform',`translate(${p.x},${p.y})`);}}
 graph.edges.forEach((edge,i)=>{let a=map.positions.get(edge.left),b=map.positions.get(edge.right);const key=[edge.left,edge.right].sort().join(':');const count=parallel.get(key)||0;parallel.set(key,count+1);
  const dx=b.x-a.x,dy=b.y-a.y,dist=Math.max(1,Math.hypot(dx,dy)),shift=count===0?0:((count%2?1:-1)*Math.ceil(count/2)*45);
  const startScale=Math.min(.4,90/Math.max(.01,Math.abs(dx)),42/Math.max(.01,Math.abs(dy))),endScale=startScale;
  const sx=a.x+dx*startScale,sy=a.y+dy*startScale,ex=b.x-dx*endScale,ey=b.y-dy*endScale,cx=(a.x+b.x)/2-dy/dist*shift,cy=(a.y+b.y)/2+dx/dist*shift;
  const group=svg('g',{'class':'chart-edge '+edge.status});const path=svg('path',{d:`M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`,fill:'none',stroke:edge.kind==='co_mention'?'#80bacd':'#b69bfa','stroke-width':2.5});if(edge.kind!=='co_mention')path.setAttribute('marker-end','url(#artemis-chart-arrow)');if(edge.status!=='reviewed')path.setAttribute('stroke-dasharray',edge.kind==='co_mention'?'2 7':'8 5');
  const hit=svg('path',{d:path.getAttribute('d'),fill:'none',stroke:'transparent','stroke-width':20});group.append(path,hit,svg('title',{},edge.label+' · '+edge.status));const label=svg('text',{x:(sx+2*cx+ex)/4,y:(sy+2*cy+ey)/4-8,'text-anchor':'middle','class':'chart-edge-label'},edge.label.length>36?edge.label.slice(0,33)+'…':edge.label);group.append(label);edgeViews.push(()=>{const a=map.positions.get(edge.left),b=map.positions.get(edge.right),dx=b.x-a.x,dy=b.y-a.y,dist=Math.max(1,Math.hypot(dx,dy)),scale=Math.min(.4,90/Math.max(.01,Math.abs(dx)),42/Math.max(.01,Math.abs(dy))),sx=a.x+dx*scale,sy=a.y+dy*scale,ex=b.x-dx*scale,ey=b.y-dy*scale,cx=(a.x+b.x)/2-dy/dist*shift,cy=(a.y+b.y)/2+dx/dist*shift,d=`M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`;path.setAttribute('d',d);hit.setAttribute('d',d);label.setAttribute('x',(sx+2*cx+ex)/4);label.setAttribute('y',(sy+2*cy+ey)/4-8);});interact(group,edge.label+(edge.kind==='co_mention'?' between ':' from ')+graph.nodes.find(n=>n.id===edge.left).name+(edge.kind==='co_mention'?' and ':' to ')+graph.nodes.find(n=>n.id===edge.right).name,()=>selectEdge(edge));canvas.append(group);
 });
 for(const node of graph.nodes){const p=map.positions.get(node.id),group=svg('g',{transform:`translate(${p.x},${p.y})`,'class':'chart-node'});group.append(svg('rect',{x:-90,y:-42,width:180,height:84,rx:node.kind==='person'?35:12}),svg('title',{},node.label),svg('text',{x:0,y:-9,'text-anchor':'middle','class':'chart-node-name'},node.name.length>22?node.name.slice(0,19)+'…':node.name),svg('text',{x:0,y:13,'text-anchor':'middle','class':'chart-node-kind'},node.kind.replace('_',' ')),svg('text',{x:0,y:30,'text-anchor':'middle','class':'chart-node-kind'},'Notes '+node.note_ids.slice(0,4).join(', ')+(node.note_ids.length>4?' +'+(node.note_ids.length-4):'')));let drag=null,suppressClick=false;
 interact(group,node.label+'. Drag to move, or use arrow keys; Shift moves faster.',event=>{if(suppressClick&&event?.type==='click'){suppressClick=false;return;}suppressClick=false;selectNode(node);});
 nodeViews.set(node.id,group);
 group.addEventListener('pointerdown',event=>{if(event.button!==0)return;const q=pointerPosition(canvas,event);if(!q)return;drag={pointer:event.pointerId,x:q.x,y:q.y,start:{...map.positions.get(node.id)},moved:false};group.setPointerCapture(event.pointerId);});
 group.addEventListener('pointermove',event=>{if(!drag||event.pointerId!==drag.pointer)return;const q=pointerPosition(canvas,event);if(!q)return;const dx=q.x-drag.x,dy=q.y-drag.y;if(Math.hypot(dx,dy)>3)drag.moved=true;if(!drag.moved)return;event.preventDefault();const point=map.positions.get(node.id);point.x=Math.max(100,Math.min(map.width-100,drag.start.x+dx));point.y=Math.max(52,Math.min(map.height-52,drag.start.y+dy));refreshGeometry();});
 function endDrag(event){if(!drag||event.pointerId!==drag.pointer)return;suppressClick=drag.moved;drag=null;if(group.hasPointerCapture(event.pointerId))group.releasePointerCapture(event.pointerId);}
 group.addEventListener('pointerup',endDrag);group.addEventListener('pointercancel',endDrag);
 group.addEventListener('keydown',event=>{const delta={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];if(!delta)return;event.preventDefault();const point=map.positions.get(node.id),step=event.shiftKey?40:10;point.x=Math.max(100,Math.min(map.width-100,point.x+delta[0]*step));point.y=Math.max(52,Math.min(map.height-52,point.y+delta[1]*step));refreshGeometry();});
 canvas.append(group);}
 frame.append(canvas);container.append(frame);
 const lists=document.createElement('details');lists.append(text('summary','Accessible entity and connection lists'));
 for(const node of graph.nodes){const b=text('button',node.label);b.type='button';b.addEventListener('click',()=>selectNode(node));lists.append(b);}
 for(const edge of graph.edges){const b=text('button',graph.nodes.find(n=>n.id===edge.left).name+(edge.kind==='co_mention'?' ↔ ':' → ')+edge.label+(edge.kind==='co_mention'?' ↔ ':' → ')+graph.nodes.find(n=>n.id===edge.right).name+' · '+edge.status);b.type='button';b.addEventListener('click',()=>selectEdge(edge));lists.append(b);}container.append(lists);
 detail.append(text('p','Select an identity or connection to inspect its evidence. Chart position and line length have no analytical meaning.'));
 return graph;
}
const api={project,layout,render,fitScale,pointerPosition};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ArtemisLinkChart=api;
})(typeof window!=='undefined'?window:globalThis);
