"""Generate a real saved-record PDF, rather than model-authored placeholders."""
import json
from pathlib import Path
from uuid import UUID,uuid4
from threading import RLock
import shutil
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.database import get_database
from app.config import get_settings
from app.services.daily_report import generate


def same_origin(request:Request):
    origin=request.headers.get('origin')
    if request.method!='GET'and(request.headers.get('sec-fetch-site')=='cross-site'or(origin and origin!=str(request.base_url).rstrip('/'))):raise HTTPException(403,'Report generation must come from this Artemis origin.')
router=APIRouter(prefix='/reports',dependencies=[Depends(same_origin)])
class ReportRequest(BaseModel):
    hours:int=Field(default=24,ge=1,le=168)
    timezone:Literal['America/Chicago','UTC']='America/Chicago'
    sources:list[Literal['notes','risk_tracker','market_lens']]=Field(default_factory=lambda:['notes','risk_tracker','market_lens'],min_length=1,max_length=3)
@router.post('')
def create(body:ReportRequest,database:Session=Depends(get_database)):
    try:return generate(database,body.hours,body.timezone,body.sources)
    except ImportError as e:raise HTTPException(503,'PDF dependency unavailable. Install reportlab and tzdata in the Artemis virtual environment.')from e
    except Exception as e:raise HTTPException(500,'Report generation failed: '+type(e).__name__+'. No placeholder report was generated.')from e
@router.get('')
def saved():
    folder=Path(get_settings().data_directory)/'reports';rows=[]
    for p in folder.glob('*.json'):
        try:
            value=json.loads(p.read_text(encoding='utf-8'))
            if isinstance(value,dict)and value.get('id'):rows.append(value)
        except (OSError,ValueError):continue
    return sorted(rows,key=lambda r:r.get('created_at',''),reverse=True)[:100]
DELETE_LOCK=RLock()
class DeleteReports(BaseModel):
    ids:list[UUID]=Field(min_length=1,max_length=100)
@router.post('/delete-selected')
def delete_selected(body:DeleteReports):
    folder=Path(get_settings().data_directory)/'reports';deleted=[];failed=[]
    with DELETE_LOCK:
        for identity in dict.fromkeys(body.ids):
            key=str(identity);stage=folder/('.delete-'+str(uuid4()));moved=[]
            try:
                stage.mkdir(parents=True)
                for suffix in ('.pdf','.json'):
                    src=folder/(key+suffix)
                    if src.is_file():
                        dst=stage/src.name;src.replace(dst);moved.append((src,dst))
            except OSError:
                for src,dst in reversed(moved):
                    if dst.exists():dst.replace(src)
                failed.append(key);continue
            # Removal from the visible saved-report set succeeded. Cleanup is best effort.
            deleted.append(key)
            shutil.rmtree(stage,ignore_errors=True)
    return {'deleted':deleted,'failed':failed}

@router.get('/{report_id}/pdf')
def pdf(report_id:UUID):
    target=Path(get_settings().data_directory)/'reports'/(str(report_id)+'.pdf')
    if not target.is_file():raise HTTPException(404,'Saved PDF not found.')
    return FileResponse(target,media_type='application/pdf',filename='Artemis-Collected-Information-'+str(report_id)[:8]+'.pdf')
