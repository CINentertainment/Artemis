"""Private Tailscale Serve portal on loopback 8201; never proxy arbitrary APIs."""
import argparse,base64,hashlib,hmac,json,re,secrets,time
from collections import defaultdict,deque
from pathlib import Path
from uuid import UUID
import httpx
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse,Response,JSONResponse
from app.config import get_settings
from app.services.encryption import protect_text,unprotect_text
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None);BOOT=secrets.token_bytes(32);RATE=defaultdict(deque)
BASE='http://127.0.0.1:8200';STATIC=Path(__file__).resolve().parent/'static'
def config_path():return Path(get_settings().data_directory)/'remote-gateway.dpapi'
def config():
    path=config_path();return json.loads(unprotect_text(path.read_text()))if path.exists()else{'enabled':False}
def configure(host,login):
    if not re.fullmatch(r'[a-z0-9-]+\.[a-z0-9.-]+\.ts\.net:8443',host):raise ValueError('Use this device MagicDNS hostname with :8443.')
    if not login or len(login)>200:raise ValueError('A Tailscale user login is required.')
    path=config_path();path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(protect_text(json.dumps({'enabled':True,'host':host,'login':login,'revision':secrets.token_hex(16)})));tmp.replace(path)
def token(login,revision):
    value=base64.urlsafe_b64encode(json.dumps({'login':login,'revision':revision,'until':time.time()+28800,'nonce':secrets.token_hex(16)}).encode()).decode().rstrip('=');return value+'.'+hmac.new(BOOT,value.encode(),hashlib.sha256).hexdigest()
def valid(value,login,revision):
    try:
        payload,signature=value.split('.');expected=hmac.new(BOOT,payload.encode(),hashlib.sha256).hexdigest()
        data=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        return hmac.compare_digest(signature,expected)and data['login']==login and data['revision']==revision and data['until']>time.time()
    except(Exception):return False
@app.middleware('http')
async def authorize(request:Request,call_next):
    cfg=config();login=request.headers.get('tailscale-user-login','')
    forwarded=request.headers.get('x-forwarded-host',request.headers.get('host',''))
    if not cfg.get('enabled')or not request.client or request.client.host not in {'127.0.0.1','::1'}or login!=cfg.get('login')or forwarded!=cfg.get('host')or request.headers.get('x-forwarded-proto')!='https':return JSONResponse({'detail':'Private portal access denied.'},status_code=403)
    key=login;times=RATE[key];stamp=time.time()
    while times and times[0]<stamp-60:times.popleft()
    if len(times)>=90:return JSONResponse({'detail':'Portal request rate exceeded.'},status_code=429)
    times.append(stamp)
    if request.url.path not in {'/','/portal.js'}:
        cookie=request.cookies.get('artemis_remote','')
        if not valid(cookie,login,cfg['revision']):return JSONResponse({'detail':'Reload the private portal to start a new session.'},status_code=401)
        if request.method!='GET'and(request.headers.get('origin')!='https://'+cfg['host']or request.headers.get('sec-fetch-site')in{'cross-site','same-site'}or not hmac.compare_digest(request.headers.get('x-artemis-csrf',''),cookie)):
            return JSONResponse({'detail':'Portal origin or session token mismatch.'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store';response.headers['X-Frame-Options']='DENY';response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'"
    return response
async def upstream(method,path,**kwargs):
    async with httpx.AsyncClient(timeout=150,trust_env=False,follow_redirects=False)as client:
        try:r=await client.request(method,BASE+path,**kwargs)
        except httpx.HTTPError:raise HTTPException(503,'Local Artemis is unavailable.')from None
    if r.status_code>=400:raise HTTPException(r.status_code,'Local Artemis could not complete this request. Check its dashboard.')
    return r
@app.get('/')
def home():
    cfg=config();csrf=token(cfg['login'],cfg['revision']);html=(STATIC/'remote.html').read_text().replace('__CSRF__',csrf)
    r=HTMLResponse(html);r.set_cookie('artemis_remote',csrf,secure=True,httponly=True,samesite='strict',max_age=28800,path='/');return r
@app.get('/portal.js')
def script():return Response((STATIC/'remote.js').read_text(),media_type='application/javascript')
@app.get('/api/status')
async def status():
    missions=(await upstream('GET','/missions')).json();reports=(await upstream('GET','/reports')).json();vault=(await upstream('GET','/continuity/status')).json()
    return {'missions':[{'id':m['id'],'name':m['name'],'state':m['state'],'objective':m['objective'],'waiting':m['waiting']}for m in missions['missions']],'reports':reports[:20],'backup':{'state':vault['status']['state'],'last_success':vault['status'].get('last_success')}}
@app.get('/api/missions/{mission_id}/briefing')
async def brief(mission_id:UUID):return(await upstream('GET','/missions/'+str(mission_id)+'/briefing')).json()
@app.get('/api/reports/{report_id}/pdf')
async def pdf(report_id:UUID):
    r=await upstream('GET','/reports/'+str(report_id)+'/pdf');return Response(r.content,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="Artemis-Brief.pdf"'})
SAFE={'system status','list missions','mission status','brief this mission','pause this mission','resume this mission','prioritize this mission','read my alerts','brief me on what changed','read my brief','stop speaking','pause briefing','resume briefing'}
@app.post('/api/command')
async def command(request:Request):
    body=await request.json();text=body.get('text','')
    if not isinstance(text,str)or not 1<=len(text)<=200:raise HTTPException(422,'Enter a short portal command.')
    clean=' '.join(text.casefold().split())
    if clean not in SAFE and not(re.fullmatch(r'open mission .{3,120}',clean)):raise HTTPException(422,'Use the listed mission/briefing controls, or ask a read-only question in a briefing session.')
    r=await upstream('POST','/voice-hub/command',json={'text':text,'speak':False,'source':'web'});return r.json()
@app.post('/api/session')
async def session():return(await upstream('POST','/voice-sessions',json={'title':'Private remote briefing Q&A','include_brief':True,'include_notes':True,'review_before_send':False})).json()
@app.get('/api/sessions/{session_id}')
async def session_status(session_id:UUID):return(await upstream('GET','/voice-sessions/'+str(session_id))).json()
@app.post('/api/sessions/{session_id}/ask')
async def ask(session_id:UUID,request:Request):
    body=await request.json();text=body.get('text','')
    if not isinstance(text,str)or not 1<=len(text)<=2000:raise HTTPException(422,'Question length must be 1–2000 characters.')
    return(await upstream('POST','/voice-sessions/'+str(session_id)+'/turns',json={'text':text,'speak':False})).json()
@app.post('/api/transcribe')
async def transcribe(request:Request):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>4*1024*1024:raise HTTPException(413,'Keep the recording under 4 MB.')
    return(await upstream('POST','/voice/transcribe?fast=true',files={'file':('remote.webm',bytes(data),'audio/webm')})).json()
@app.post('/api/speak')
async def speak(request:Request):
    body=await request.json();text=body.get('text','')
    if not isinstance(text,str)or not 1<=len(text)<=3000:raise HTTPException(422,'Spoken text must be 1–3000 characters.')
    r=await upstream('POST','/voice/speak',json={'text':text});return Response(r.content,media_type='audio/wav')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--configure',action='store_true');parser.add_argument('--host');parser.add_argument('--login');parser.add_argument('--disable',action='store_true');args=parser.parse_args()
    if args.disable:
        path=config_path();path.parent.mkdir(parents=True,exist_ok=True);path.write_text(protect_text(json.dumps({'enabled':False})));print('Remote portal disabled.')
    elif args.configure:configure(args.host or'',args.login or'');print('Private portal identity configured. No public listener was opened.')
    else:parser.error('Use --configure or --disable; run the server using the supplied PowerShell script.')
