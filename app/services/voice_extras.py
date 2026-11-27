"""Local playback preferences and fixed voice controls; no new actions or tools."""
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field

def same_origin(request:Request):
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(request.headers.get('origin')and request.headers['origin']!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Use the local Artemis origin.')
router=APIRouter(prefix='/voice-extras',dependencies=[Depends(same_origin)])
class Preferences(BaseModel):
    speech_speed:float=Field(default=1,ge=.75,le=1.4)
    reply_limit:Literal[1200,12000]=12000
@router.get('/settings')
def preferences():
    from app.services.voice_hub import settings
    s=settings();return {'speech_speed':s.get('speech_speed',1),'reply_limit':s.get('reply_limit',12000)}
@router.put('/settings')
def save(body:Preferences):
    from app.services.voice_hub import STATE,settings,atomic,LOCK
    with LOCK:s=settings();s.update(body.model_dump());atomic(STATE/'settings.json',s)
    return body.model_dump()
COMMANDS={'repeat that','repeat last reply','speak faster','speak slower','normal speaking speed','brief answers','full answers','sleep for ten minutes','backup status','specialist team status'}
def voice(text):
    from app.services.voice_hub import normalize,control
    clean=normalize(text);s=preferences()
    if clean in {'repeat that','repeat last reply'}:control('repeat');return {'speech':'Repeating the last desktop reply.','control':'repeat'}
    if clean=='sleep for ten minutes':control('sleep',{'minutes':10});return {'speech':'Microphone paused for ten minutes. Use Unmute to resume earlier.','control':'sleep'}
    if clean in {'speak faster','speak slower','normal speaking speed'}:
        s['speech_speed']=1.0 if clean=='normal speaking speed'else round(min(1.4,max(.75,s['speech_speed']+(.1 if clean=='speak faster'else-.1))),2)
        save(Preferences(**s));return {'speech':'Speaking speed set to '+str(s['speech_speed'])+'.'}
    if clean in {'brief answers','full answers'}:
        s['reply_limit']=1200 if clean=='brief answers'else 12000;save(Preferences(**s));return {'speech':'Short spoken readouts enabled. Full content stays in Artemis.'if clean=='brief answers'else'Full spoken readouts enabled within the existing reading limit.'}
    if clean=='backup status':
        from app.services.continuity import status
        d=status();v=d['status'];return {'speech':'Backup state '+v['state']+'. Last verified backup '+str(v.get('last_success')or'not recorded')+'.','link':'/static/continuity.html'}
    if clean=='specialist team status':
        from app.services.specialist_team import listing
        jobs=listing()['jobs'];return {'speech':str(sum(j['state']=='queued'for j in jobs))+' specialist drafts queued and '+str(sum(j['state']=='running'for j in jobs))+' running in the returned work queue. Drafts require review.','link':'/static/specialists.html'}
    return None
