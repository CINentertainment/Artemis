from threading import Lock

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.research_trial import run_research


router = APIRouter(prefix="/research", tags=["research"])
research_lock = Lock()


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)


@router.post("")
def research(request: ResearchRequest) -> dict:
    topic = request.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Enter a research topic.")

    if not research_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A research run is already in progress.",
        )

    try:
        return run_research(topic)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    finally:
        research_lock.release()