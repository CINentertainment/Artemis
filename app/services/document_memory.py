"""Encrypted lexical retrieval over saved extracted documents; no network or actions."""
import json
import re
from threading import RLock
from fastapi import HTTPException,Request
from pydantic import BaseModel, Field
from sqlalchemy import String, Text, select
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base,SessionLocal
from app.services.documents import router,DocumentRecord,get_doc,pack,unpack,same_origin

LOCK=RLock()
MAX_DOCS=200
INDEX_BATCH=10
STOP=set('a an the is are was were be been being i me my we us our you your he she they them their it its this that these those to of in on for from by and or with about as at do does did can could would should will have has had please tell explain summarize summary document documents file files uploaded saved according based know information what which how when where why who'.split())

class LibraryIndex(Base):
    __tablename__='private_document_search_index'
    document_id:Mapped[str]=mapped_column(String(36),primary_key=True)
    digest:Mapped[str]=mapped_column(String(64))
    payload:Mapped[str]=mapped_column(Text)
class LibraryPolicy(Base):
    __tablename__='private_document_search_policy'
    id:Mapped[str]=mapped_column(String(20),primary_key=True)
    payload:Mapped[str]=mapped_column(Text)

def terms(value):return set(re.findall(r'[\w-]{3,}',value.casefold()))-STOP

def policy(db):
    row=db.get(LibraryPolicy,'main')
    return unpack(row) if row else {'enabled':True,'excluded_ids':[]}

def index_document(db,data):
    row=db.get(LibraryIndex,data['id'])
    payload=pack({k:data[k] for k in ('id','name','created','digest','units','warnings','partial')})
    if row:row.digest=data['digest'];row.payload=payload
    else:db.add(LibraryIndex(document_id=data['id'],digest=data['digest'],payload=payload))

def search(db,query,limit=4):
    settings=policy(db)
    if not settings['enabled']:return {'documents':[],'matches':[],'indexed_now':0,'pending_index':0,'coverage':'Saved-document search disabled.'}
    entries=db.execute(select(DocumentRecord.id,DocumentRecord.digest).order_by(DocumentRecord.id.desc()).limit(MAX_DOCS+1)).all()
    limited=len(entries)>MAX_DOCS;entries=entries[:MAX_DOCS]
    eligible={id:digest for id,digest in entries if id not in settings['excluded_ids']}
    cached={row.document_id:row for row in db.scalars(select(LibraryIndex)).all() if row.document_id in eligible}
    missing=[id for id,digest in eligible.items() if id not in cached or cached[id].digest!=digest]
    indexed=0
    with LOCK:
        for id in missing[:INDEX_BATCH]:
            _,data=get_doc(db,id);index_document(db,data);indexed+=1
        db.commit()
    # Requery after additions; restrict to originals that still exist and are allowed.
    candidates=[];query_terms=terms(query);needle=query.casefold()
    for row in db.scalars(select(LibraryIndex)).yield_per(1):
        if row.document_id not in eligible:continue
        data=unpack(row);base=data['name'].rsplit('.',1)[0].casefold();named=base in needle and len(base)>3
        ranked=[]
        for unit in data['units']:
            words=terms(unit['text']);overlap=query_terms & words
            # Two distinctive term matches OR an explicit stored filename.
            if not named and len(overlap)<2:continue
            score=len(overlap)*3+(20 if named else 0)
            ranked.append((score,unit))
        if ranked:
            ranked.sort(key=lambda x:-x[0]);selected=[u for _,u in ranked[:20]]
            candidates.append((ranked[0][0],data|{'units':selected}))
    candidates.sort(key=lambda x:-x[0]);documents=[d for _,d in candidates[:limit]]
    coverage=f'Keyword retrieval across up to {MAX_DOCS} stored documents. '+str(max(0,len(missing)-indexed))+' document(s) still awaiting indexing.'
    if limited:coverage+=' Library exceeds the current document search limit; not all saved records were searched.'
    return {'documents':documents,'matches':[{'id':d['id'],'name':d['name'],'locations':[u['locator'] for u in d['units'][:3]]} for d in documents],'indexed_now':indexed,'pending_index':max(0,len(missing)-indexed),'coverage':coverage}

class SearchRequest(BaseModel):query:str=Field(min_length=1,max_length=3000)
class PolicyRequest(BaseModel):
    enabled:bool=True
    excluded_ids:list[str]=Field(default_factory=list,max_length=2000)

@router.get('/library/policy')
def get_policy():
    with SessionLocal() as db:return policy(db)|{'max_search_documents':MAX_DOCS,'index_batch':INDEX_BATCH}

@router.put('/library/policy')
async def set_policy(body:PolicyRequest,request:Request):
    await same_origin(request)
    with LOCK,SessionLocal() as db:
        for id in body.excluded_ids:get_doc(db,id)
        row=db.get(LibraryPolicy,'main');value=pack({'enabled':body.enabled,'excluded_ids':list(dict.fromkeys(body.excluded_ids))})
        if row:row.payload=value
        else:db.add(LibraryPolicy(id='main',payload=value))
        db.commit();return policy(db)

@router.post('/library/search')
async def search_preview(body:SearchRequest,request:Request):
    await same_origin(request)
    import asyncio
    def run():
        with SessionLocal() as db:
            result=search(db,body.query)
            return {k:v for k,v in result.items() if k!='documents'}
    return await asyncio.to_thread(run)
