"""Scheduled encrypted backups; credentials stay Windows-user protected locally."""
import json,os,shutil,sqlite3,time,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
from threading import Event,Thread,RLock
from uuid import uuid4,UUID
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from app.config import get_settings
from app.services.encryption import protect_text,unprotect_text
from app.services import continuity_core as core
LOG=logging.getLogger(__name__);LOCK=RLock();PROJECT=Path(__file__).resolve().parents[2]
def state_dir():
    p=Path(get_settings().data_directory)/'continuity';p.mkdir(parents=True,exist_ok=True);return p

def atomic(path,value):
    tmp=path.with_suffix('.'+str(uuid4())+'.tmp');tmp.write_text(json.dumps(value),encoding='utf-8');tmp.replace(path)
def config():
    p=state_dir()/'settings.dpapi'
    return json.loads(unprotect_text(p.read_text()))if p.exists()else None

def saved_status():
    try:return json.loads((state_dir()/'status.json').read_text())
    except(OSError,ValueError):return {'state':'not_configured','last_success':None}
def save_status(value):atomic(state_dir()/'status.json',value)
def discovery():
    roots={'notes':PROJECT.parent/'intel-notes-manager','risk_tracker':PROJECT.parent/'protective-intelligence-risk-tracker','market_lens':PROJECT.parent/'market-lens'}
    result={'artemis':[str(get_settings().database_path.resolve())]}
    for label,root in roots.items():
        found=[]
        if root.is_dir():
            # Deliberately bounded; no home-drive or environment-secret crawling.
            for base,dirs,files in os.walk(root):
                relative=Path(base).relative_to(root)
                dirs[:]=[d for d in dirs if d not in {'.venv','.git','.runtime','node_modules','__pycache__'}and len(relative.parts)<2]
                for name in files:
                    p=Path(base)/name
                    if p.suffix.lower()not in {'.db','.sqlite','.sqlite3'}or p.is_symlink():continue
                    try:
                        with p.open('rb')as f:valid=f.read(16)==b'SQLite format 3\x00'
                        if valid:found.append(str(p.resolve()))
                    except OSError:pass
                    if len(found)>=20:break
        result[label]=sorted(found)[:20]
    return result

class Settings(BaseModel):
    enabled:bool=True
    passphrase:str=Field(default='',max_length=500)
    destination:str=Field(min_length=3,max_length=500)
    databases:dict[str,str]
    interval_hours:int=Field(default=24,ge=6,le=168)
    retain:int=Field(default=7,ge=2,le=30)
    include_workspace:bool=True
    include_reports:bool=True
    include_settings:bool=True

def configure(body):
    with LOCK:
        old=config()or{};password=body.passphrase or old.get('passphrase','')
        if len(password)<16:raise HTTPException(422,'Set a recovery passphrase of at least sixteen characters and keep an independent copy.')
        allowed={'artemis','notes','risk_tracker','market_lens'}
        if set(body.databases)!=allowed:raise HTTPException(422,'Provide explicit paths for all four databases. Missing services must be configured before claiming suite coverage.')
        values={}
        for label,path in body.databases.items():
            target=Path(path).expanduser().resolve()
            if not target.is_file()or target.is_symlink():raise HTTPException(422,label+' database is unavailable.')
            with target.open('rb')as f:
                if f.read(16)!=b'SQLite format 3\x00':raise HTTPException(422,label+' is not a SQLite database.')
            values[label]=str(target)
        if values['artemis']!=str(get_settings().database_path.resolve()):raise HTTPException(422,'Use the configured live Artemis database path.')
        if len(set(values.values()))!=4:raise HTTPException(422,'Each service needs its own distinct database.')
        dest=Path(body.destination).expanduser().resolve()
        if dest.exists()and not dest.is_dir():raise HTTPException(422,'Choose a backup directory, not an existing file.')
        if any(dest==Path(p).parent for p in values.values()):raise HTTPException(422,'Use a dedicated backup directory.')
        dest.mkdir(parents=True,exist_ok=True)
        value=body.model_dump(exclude={'passphrase'});value.update(passphrase=password,databases=values,destination=str(dest),next_at=time.time(),changed_at=core.now())
        path=state_dir()/'settings.dpapi';tmp=path.with_suffix('.tmp');tmp.write_text(protect_text(json.dumps(value)));tmp.replace(path)
        return {'saved':True,'message':'Backup configured. The next worker pass performs and verifies a backup. Keep the recovery passphrase separately.'}

def attachments(cfg):
    result={};data=Path(get_settings().data_directory)
    if cfg['include_reports']:
        for sub in ['reports','research']:
            root=data/sub
            if not root.exists():continue
            for path in root.rglob('*'):
                if path.is_file()and not path.is_symlink()and path.suffix.lower()in{'.pdf','.json','.txt','.md'}:
                    label='artemis/'+str(path.relative_to(data)).replace('\\','/')
                    if len(label)>200:raise ValueError('An attachment path exceeds the recovery bound.')
                    result[label]=path
                    if len(result)>9000:raise ValueError('Attachment count exceeds the backup bound.')
    if cfg.get('include_workspace',True):
        root=data/'workspace'
        if root.exists():
            for path in root.rglob('*'):
                if path.is_file()and not path.is_symlink():
                    label='artemis/workspace/'+path.relative_to(root).as_posix()
                    if not core.safe_label(label):raise ValueError('Workspace filename exceeds the recovery path bound.')
                    result[label]=path
                    if len(result)>9000:raise ValueError('Attachment count exceeds the backup bound.')
    if cfg['include_settings']:
        for label,root in [('artemis',PROJECT),('notes',PROJECT.parent/'intel-notes-manager'),('risk_tracker',PROJECT.parent/'protective-intelligence-risk-tracker'),('market_lens',PROJECT.parent/'market-lens')]:
            if(root/'.env').is_file():result[label+'/settings.env']=root/'.env'
        for name in ['settings.json','active-event.json','active-mission.json']:
            path=PROJECT/'.runtime/voice-agent'/name
            if path.is_file():result['artemis/voice-agent/'+name]=path
    return result

def perform():
    if not LOCK.acquire(blocking=False):return
    try:
        cfg=config()
        if not cfg or not cfg['enabled']:return
        status=saved_status();status.update(state='running',last_started=core.now(),error=None);save_status(status)
        identity=uuid4().hex;destination=Path(cfg['destination']);destination.mkdir(parents=True,exist_ok=True)
        raw,manifest=core.build_archive(cfg['databases'],attachments(cfg),cfg['passphrase'],unprotect_text)
        if shutil.disk_usage(destination).free<len(raw)+50*1024*1024:raise OSError('Insufficient free space for backup publication.')
        tmp=destination/(identity+'.building');target=destination/(identity+'.arb')
        try:
            with tmp.open('wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
            core.inspect_archive(tmp.read_bytes(),cfg['passphrase'],encode=protect_text,decode=unprotect_text)
            tmp.replace(target)
        finally:tmp.unlink(missing_ok=True)
        receipt={'format':'artemis-vault-v1','id':identity,'created_at':manifest['created_at'],'file':target.name,'bytes':len(raw),'sha256':core.sha(raw),'verified_at':core.now(),'services':list(manifest['databases']),'dpapi_values':manifest['dpapi_values'],'included':{k:cfg.get(k,True)for k in ['include_reports','include_settings','include_workspace']},'verification':'Authenticated archive, SQLite integrity/foreign keys/row counts and in-memory DPAPI rebind rehearsal passed on this Windows account.','consistency':manifest['consistency']}
        atomic(destination/(identity+'.json'),receipt)
        status.update(state='verified',last_success=core.now(),latest=receipt,error=None,failures=0);save_status(status)
        # Remove only older known verified backup pairs after the new one passes.
        known=[]
        for p in destination.glob('*.json'):
            if len(p.stem)!=32:continue
            try:
                value=json.loads(p.read_text())
                if value.get('format')=='artemis-vault-v1'and value.get('id')==p.stem and value.get('verified_at'):known.append((value['created_at'],p))
            except(OSError,ValueError,KeyError):pass
        for _,p in sorted(known,reverse=True)[cfg['retain']:]:
            (destination/(p.stem+'.arb')).unlink(missing_ok=True);p.unlink(missing_ok=True)
    except Exception as error:
        status=saved_status();status.update(state='failed',failures=status.get('failures',0)+1,error='Backup failed ('+type(error).__name__+'). Check configured paths, passphrase availability, free space and Windows account encryption access. No new backup is marked verified.');save_status(status);LOG.warning('Continuity backup failed: %s',type(error).__name__)
    finally:
        try:
            cfg=config()
            if cfg:
                latest=saved_status();delay=min(21600,900*2**min(latest.get('failures',1)-1,5))if latest['state']=='failed'else cfg['interval_hours']*3600
                cfg['next_at']=time.time()+delay;path=state_dir()/'settings.dpapi';tmp=path.with_suffix('.tmp');tmp.write_text(protect_text(json.dumps(cfg)));tmp.replace(path)
        finally:LOCK.release()

def status():
    cfg=config();value=saved_status();safe={k:v for k,v in(cfg or{}).items()if k!='passphrase'}
    return {'configured':bool(cfg),'settings':safe,'status':value,'overdue':bool(cfg and cfg['enabled']and(time.time()-(datetime.fromisoformat(value['last_success']).timestamp()if value.get('last_success')else datetime.fromisoformat(cfg['changed_at']).timestamp())>cfg['interval_hours']*7200))}
def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use the local Artemis origin.')
router=APIRouter(prefix='/continuity',dependencies=[Depends(same_origin)])
@router.get('/status')
def get_status():return status()
@router.get('/discover')
def discover():return {'candidates':discovery(),'default_destination':str(Path(get_settings().data_directory)/'encrypted-backups')}
@router.put('/settings')
def save_settings(body:Settings):return configure(body)
@router.post('/run')
def request_backup():
    with LOCK:
        cfg=config()
        if not cfg or not cfg['enabled']:raise HTTPException(409,'Configure and enable backups first.')
        cfg['next_at']=time.time();path=state_dir()/'settings.dpapi';tmp=path.with_suffix('.tmp');tmp.write_text(protect_text(json.dumps(cfg)));tmp.replace(path)
    return {'queued':True}
class Worker:
    def __init__(self):self.event=Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.event.clear();self.thread=Thread(target=self.loop,daemon=True,name='artemis-encrypted-backup');self.thread.start()
    def stop(self):
        self.event.set()
        if self.thread:self.thread.join(timeout=2)
    def loop(self):
        while not self.event.is_set():
            try:
                cfg=config()
                if cfg and cfg['enabled']and cfg.get('next_at',0)<=time.time():perform()
            except Exception:LOG.warning('Backup configuration unavailable; backup not claimed successful.')
            self.event.wait(30)
worker=Worker()
