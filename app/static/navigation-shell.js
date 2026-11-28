/* Artemis shared navigation shell v1.0.0 — navigation only; no service mutations. */
(()=>{
'use strict';
if(document.getElementById('artemis-navigation-shell'))return;
const registry=window.ARTEMIS_NAVIGATION_REGISTRY;
if(!registry||registry.version!==1||!Array.isArray(registry.pages))return;
const groups=['Workspace','Research','Markets','Commerce','Briefings','System'];
const pages=registry.pages;
const norm=path=>path.replace(/\/+$/,'')||'/';
const path=norm(location.pathname);
const current=pages.find(p=>[p.href,...(p.aliases||[])].some(a=>norm(a)===path));
const root=document.createElement('div');root.id='artemis-navigation-shell';
const make=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const top=make('div','ans-top');top.setAttribute('role','navigation');top.setAttribute('aria-label','Artemis main navigation');
const brand=make('a','ans-brand');brand.href='/dashboard';brand.setAttribute('aria-label','Artemis dashboard');brand.append(make('span','ans-mark','A'),make('span','ans-wordmark','ARTEMIS'));
const context=make('div','ans-context');context.append(make('span','ans-category',current?current.group:'Artemis'),make('span','ans-page',current?current.label:'Workspace'));
const quick=make('div','ans-quick');
function anchor(p,cls){const a=make('a',cls,p.label);a.href=p.href;if(p===current)a.setAttribute('aria-current','page');return a;}
for(const id of ['dashboard','intelligence','inbox','commerce']){const p=pages.find(p=>p.id===id);if(p&&p.installed)quick.append(anchor(p,'ans-quick-link'));}
const toggle=make('button','ans-toggle','All tools');toggle.type='button';toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls','artemis-tool-menu');toggle.setAttribute('aria-haspopup','dialog');
toggle.append(make('span','ans-shortcut','Ctrl K'));
top.append(brand,context,quick,toggle);
const veil=make('div','ans-veil');veil.hidden=true;veil.setAttribute('aria-hidden','true');
const panel=make('div','ans-menu');panel.id='artemis-tool-menu';panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');panel.setAttribute('aria-labelledby','artemis-menu-title');
const menuHead=make('div','ans-menu-head');const title=make('h2','','All Artemis tools');title.id='artemis-menu-title';const close=make('button','ans-close','Close');close.type='button';close.setAttribute('aria-label','Close navigation menu');menuHead.append(title,close);
const label=make('label','ans-search-label','Find a page');label.htmlFor='artemis-menu-search';
const search=make('input','ans-search');search.id='artemis-menu-search';search.type='search';search.placeholder='Search tools, reports, voice, outreach…';search.autocomplete='off';
const hint=make('p','ans-hint','Choose a tool. Esc closes this menu. Uninstalled tools are labeled.');
const grid=make('div','ans-grid');const sections=[];const items=[];
for(const group of groups){const section=make('div','ans-group');section.append(make('h3','ans-group-title',group));const list=make('div','ans-list');
 for(const p of pages.filter(p=>p.group===group)){
  let item;
  if(p.installed){item=anchor(p,'ans-link');item.textContent='';item.append(make('span','ans-link-title',p.label),make('span','ans-link-desc',p.description));}
  else{item=make('div','ans-link ans-unavailable');item.setAttribute('aria-disabled','true');item.append(make('span','ans-link-title',p.label),make('span','ans-link-desc','Not installed on this instance'));}
  const needle=(p.label+' '+p.description+' '+p.group+' '+(p.keywords||'')).toLowerCase();items.push({node:item,needle,section});list.append(item);
 }
 section.append(list);grid.append(section);sections.push(section);
}
const empty=make('p','ans-empty','No matching tools. Try “voice”, “reports”, or “commerce”.');empty.hidden=true;empty.setAttribute('role','status');
panel.append(menuHead,label,search,hint,grid,empty);
root.append(top,veil,panel);document.body.prepend(root);
let previousFocus=null;let inertStates=[];
function filter(){const query=search.value.trim().toLowerCase();let count=0;for(const item of items){item.node.hidden=!item.needle.includes(query);if(!item.node.hidden)count++;}for(const section of sections)section.hidden=items.filter(i=>i.section===section).every(i=>i.node.hidden);empty.hidden=count!==0;}
function show(){
 if(!panel.hidden)return;
 previousFocus=document.activeElement;panel.hidden=false;veil.hidden=false;toggle.setAttribute('aria-expanded','true');root.classList.add('ans-open');
 inertStates=[...document.body.children].filter(e=>e!==root&&e.tagName!=='SCRIPT').map(e=>[e,e.inert]);for(const [e]of inertStates)e.inert=true;
 search.value='';filter();search.focus();
}
function hide(){
 if(panel.hidden)return;
 panel.hidden=true;veil.hidden=true;toggle.setAttribute('aria-expanded','false');root.classList.remove('ans-open');
 for(const [e,old]of inertStates)if(e.isConnected)e.inert=old;inertStates=[];
 if(previousFocus&&previousFocus.isConnected)previousFocus.focus();else toggle.focus();
}
toggle.addEventListener('click',()=>panel.hidden?show():hide());close.addEventListener('click',hide);veil.addEventListener('click',hide);search.addEventListener('input',filter);
search.addEventListener('keydown',e=>{if(e.key!=='ArrowDown'&&e.key!=='Enter')return;const first=items.find(i=>!i.node.hidden&&i.node.tagName==='A');if(!first)return;e.preventDefault();if(e.key==='Enter')first.node.click();else first.node.focus();});
document.addEventListener('keydown',e=>{
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();show();return;}
 if(panel.hidden)return;
 if(e.key==='Escape'){e.preventDefault();e.stopPropagation();hide();}
 if(e.key==='Tab'){
  const targets=[...panel.querySelectorAll('a[href],button,input')].filter(e=>!e.disabled&&!e.closest('[hidden]'));
  const first=targets[0],last=targets[targets.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
 }
},true);
root.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey||e.button>0)return;if(a.getAttribute('aria-current')==='page'){e.preventDefault();hide();}else if(!panel.hidden)hide();});
// Hide only primary cross-page navigation after the replacement exists. Keep local tabs, forms and archive controls.
for(const nav of document.querySelectorAll('body > header > nav, body > header > .artemis-nav')){nav.dataset.artemisLegacyNavigation='';nav.hidden=true;}
for(const a of document.querySelectorAll('body > header > a[href], .main-panel > header.topbar a.artemis-action-link[href]')){
 if(pages.some(p=>[p.href,...(p.aliases||[])].includes(a.getAttribute('href')))){a.dataset.artemisLegacyNavigation='';a.hidden=true;}
}
document.body.classList.add('artemis-shell-ready');if(current&&current.id==='dashboard')document.body.classList.add('artemis-shell-dashboard');
})();
