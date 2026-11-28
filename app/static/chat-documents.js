/* Main-chat attachments. Files are data, never executable instructions. */
(()=>{'use strict';
const form=document.getElementById('message-form'),input=document.getElementById('message-input');if(!form||!input)return;
input.required=false;
let docs=[],uploading=false,loading=false,failed=false,generation=0,revision=0,pending=0;let writes=Promise.resolve();
const section=document.createElement('div');section.className='chat-document-attachments';
const toolbar=document.createElement('div');toolbar.className='chat-document-toolbar';
const button=document.createElement('button');button.type='button';button.textContent='＋ Attach documents';
const picker=document.createElement('input');picker.type='file';picker.multiple=true;picker.hidden=true;picker.setAttribute('aria-label','Attach documents to chat');
const label=document.createElement('label'),ocr=document.createElement('input');ocr.type='checkbox';label.append(ocr,document.createTextNode(' OCR for scans'));
const memoryLabel=document.createElement('label'),memory=document.createElement('input');memory.type='checkbox';memory.checked=true;memoryLabel.append(memory,document.createTextNode(' Search saved documents'));
function memoryKey(){return 'artemis-document-library:'+(state.currentConversationId||'unsent');}
function rememberMemory(){try{localStorage.setItem(memoryKey(),String(memory.checked));}catch{}}
function restoreMemory(){try{memory.checked=localStorage.getItem(memoryKey())!=='false';}catch{memory.checked=true;}}
memory.onchange=()=>{rememberMemory();message(memory.checked?'Saved-document search ON for this chat. Relevant matches will be cited.':'Saved-document search OFF for this chat. Explicit attachments still apply.');};
const clear=document.createElement('button');clear.type='button';clear.textContent='Clear documents / normal chat';
const chips=document.createElement('div');chips.className='chat-document-chips';
const notice=document.createElement('p');notice.className='chat-document-notice';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
toolbar.append(button,picker,label,clear,memoryLabel);section.append(toolbar,chips,notice);input.before(section);
function message(s){notice.textContent=s;}
async function api(path,options={}){const r=await fetch(path,options);const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
function render(){chips.replaceChildren();for(const doc of docs){const chip=document.createElement('div');chip.className='chat-document-chip';const name=document.createElement('span');name.textContent=doc.name+(doc.partial?' · partial extraction':'');const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remove '+doc.name+' from chat context');remove.disabled=false;remove.onclick=()=>changeContext(docs.filter(d=>d.id!==doc.id));chip.append(name,remove);chips.append(chip);}button.disabled=uploading||loading||state.sending;clear.disabled=false;ocr.disabled=uploading||state.sending;}
async function load(id){restoreMemory();const turn=++generation;docs=[];failed=false;loading=true;render();message('Loading this conversation’s attachments…');try{const d=await api('/conversations/'+encodeURIComponent(id)+'/documents');if(turn!==generation)return;docs=d.documents;revision=d.revision||0;if(d.missing.length){failed=true;message('A previously attached document was deleted. Clear attachments or reattach it before continuing.');}else message(docs.length?'Document context active for follow-up messages. Other source toggles are not used in document mode.':(memory.checked?'Saved-document search ON. Relevant library passages can be used without attaching files. Other source toggles and direct operational commands take priority.':'Saved-document search OFF. Drop files to attach them explicitly, or enable the search checkbox.'));}catch(e){if(turn!==generation)return;failed=true;message('Cannot load attachments: '+e.message+'. Refresh, or explicitly clear attachments for normal chat.');}finally{if(turn===generation){loading=false;render();}}}
async function attach(files){if(uploading||loading||state.sending){message('Wait for the current upload or response to finish.');return;}if(!files.length)return;if(docs.length+files.length>8){message('Up to eight documents can be active at once.');return;}uploading=true;const scope=state.currentConversationId;const turn=generation;render();const results=[];try{for(const file of files){if(file.size>20*1024*1024){results.push(file.name+': exceeds 20 MB');continue;}message('Reading '+file.name+' locally…');try{const image=/\.(png|jpe?g|tiff?|bmp|webp)$/i.test(file.name);const d=await api('/documents/upload?name='+encodeURIComponent(file.name)+'&ocr='+(ocr.checked||image),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});if(scope===state.currentConversationId&&turn===generation&&!docs.some(x=>x.id===d.document.id))docs.push(d.document);results.push(file.name+': '+(d.document.partial?'ready with extraction limits':'ready')+(d.document.warnings.length?' — '+d.document.warnings.join(' '):''));}catch(e){results.push(file.name+': '+e.message);}}if(turn===generation)message((scope!==state.currentConversationId?'Conversation changed. Imported files remain in Document reading; reattach them to the intended chat.\n':'')+results.join('\n'));}finally{uploading=false;picker.value='';render();}}
function changeContext(next){
 const scope=state.currentConversationId;const turn=++generation;docs=next;failed=false;loading=false;render();
 if(!scope){revision=0;message('Attachments cleared for this unsent chat.');return;}
 const ids=next.map(d=>d.id);pending++;
 message('Saving attachment selection. A response already processing may finish with its original files.');
 writes=writes.catch(()=>{}).then(async()=>{
  try{const result=await api('/conversations/'+encodeURIComponent(scope)+'/documents',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({document_ids:ids})});
   if(turn===generation&&scope===state.currentConversationId){revision=result.revision||0;message(ids.length?'Attachment selection saved for future messages.':'Attachments OFF. Normal chat restored. Stored files were not deleted. Saved-document search can be re-enabled with its checkbox.');}
  }catch(e){if(turn===generation&&scope===state.currentConversationId){failed=true;message('Attachment change was not saved: '+e.message+'. Retry Clear attachments or refresh.');}}
  finally{pending--;render();}
 });
}
button.onclick=()=>picker.click();picker.onchange=()=>attach([...picker.files]);clear.onclick=()=>{memory.checked=false;rememberMemory();changeContext([]);};
let depth=0;
document.addEventListener('dragenter',e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();depth++;section.classList.add('drag-over');}});
document.addEventListener('dragover',e=>{if(e.dataTransfer?.types.includes('Files')){e.preventDefault();e.dataTransfer.dropEffect='copy';}});
document.addEventListener('dragleave',e=>{if(e.dataTransfer?.types.includes('Files')){depth=Math.max(0,depth-1);if(!depth)section.classList.remove('drag-over');}});
document.addEventListener('drop',e=>{if(e.dataTransfer?.files.length){e.preventDefault();depth=0;section.classList.remove('drag-over');attach([...e.dataTransfer.files]);}});
form.addEventListener('submit',e=>{if(uploading||loading||failed||pending){e.preventDefault();e.stopImmediatePropagation();message('Wait for attachments to finish, resolve the error, or clear attachments for normal chat.');}},true);
window.artemisDocuments={ids:()=>docs.map(d=>d.id),revision:()=>revision,libraryEnabled:()=>memory.checked,busy:()=>uploading||loading||failed||pending>0,load,render,sent:async id=>{await writes.catch(()=>{});if(state.currentConversationId===id)await load(id);}};
api('/documents/status').then(s=>{picker.accept=s.formats.join(',');message('Drop documents into chat. Follow-up messages keep the attached context. Images use local OCR; scans require OCR setup.');}).catch(()=>{failed=true;message('Document service unavailable. Confirm Document Reading is installed and Artemis was restarted.');});
restoreMemory();if(state.currentConversationId)load(state.currentConversationId);
})();
