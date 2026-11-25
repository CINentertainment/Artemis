"""Offline, exact-name event-location resolution. No geocoder receives note text."""
import hashlib
import json
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from app.services.intelligence_entity_policy import narrative_spans
from app.services.research_scope import parse_date


def norm(text):
    text=unicodedata.normalize('NFKD',text).casefold()
    return ' '.join(re.sub(r'[^\w\s]',' ',''.join(c for c in text if not unicodedata.combining(c))).split())
@lru_cache(maxsize=1)
def places():return json.loads((Path(__file__).resolve().parents[1]/'data/world-places.json').read_text(encoding='utf-8'))
@lru_cache(maxsize=1)
def indexes():
    index={};by_id={}
    for p in places():
        by_id[p['id']]=p
        for name in p['aliases']+[p['name']]:
            rows=index.setdefault(norm(name),[])
            if p not in rows:rows.append(p)
    for alias,name in [('USA','United States of America'),('United States','United States of America'),('UK','United Kingdom')]:
        index[norm(alias)]=[p for p in places()if p['kind']=='country'and p['name']==name]
    return index,by_id

def resolve(location):
    parts=[p.strip()for p in location.split(',')if p.strip()];index,_=indexes()
    if not parts:return {'state':'unresolved','reason':'No event location supplied.'}
    candidates=index.get(norm(parts[0]),[])
    if len(parts)>1:
        countries=[p for p in index.get(norm(parts[-1]),[])if p['kind']=='country']
        if len(countries)!=1:return {'state':'unresolved','reason':'Country qualifier is unknown or ambiguous.'}
        candidates=[p for p in candidates if p['code']==countries[0]['code']and p['kind']=='city']
        if len(parts)>2:candidates=[p for p in candidates if norm(p.get('region')or'')==norm(','.join(parts[1:-1]))]
    if len(candidates)!=1:return {'state':'unresolved','reason':'Location has no unique exact gazetteer match.'}
    p=candidates[0]
    return {'state':'resolved','place_id':p['id'],'name':p['name'],'country':p['country'],'kind':p['kind'],'lat':p['lat'],'lon':p['lon'],'basis':'offline exact-name reference match','precision':'country reference point; not an incident coordinate'if p['kind']=='country'else'city reference point; not an exact incident site'}

def fields(body):
    output={}
    for line in body.splitlines():
        m=re.match(r'^(Event status|Who|What|When|Where|Why|How):\s*(.*)$',line,re.I)
        if m and m[1].casefold()not in output:output[m[1].casefold()]=re.sub(r'\s*\[\d+\]','',m[2]).strip()
    return output

def note_event(note):
    body=note.get('body')or'';f=fields(body);where=f.get('where')or note.get('location')or''
    if where.casefold()in {'unknown','none','n/a'}:where=''
    quotes=narrative_spans(body)
    support=next((q for q in quotes if where and where in q),'')
    location=resolve(where)if support else {'state':'unresolved','reason':'No event-location field supported by a saved narrative span.'}
    literal=f.get('when','Unknown');event_date=None
    # Exact full ISO dates only; publication/capture dates never become event dates.
    if re.search(r'\b[12]\d{3}\b',literal)and any(literal in q for q in quotes):
        parsed=parse_date(literal)
        if parsed:event_date=parsed.isoformat()
    return dict(note_id=note['id'],title=note.get('title')or'Untitled note',body=body,location_text=where,note_location=note.get('location'),location_quote=support,event_date=event_date,date_literal=literal,event_status=f.get('event status','reported'),geography=location,revision=hashlib.sha256(json.dumps([body,note.get('location')],ensure_ascii=False).encode()).hexdigest(),source_urls=list(dict.fromkeys(re.findall(r'^URL:\s*(https?://\S+)',body,re.M))))
