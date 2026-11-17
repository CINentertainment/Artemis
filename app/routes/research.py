from threading import Lock

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.research_trial import run_research


router = APIRouter(prefix="/research", tags=["research"])
research_lock = Lock()


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=300)
    risk_tracker_case_id: int | None = Field(default=None, gt=0)


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
        if request.risk_tracker_case_id is None:
            return run_research(topic)

        return run_research(
            topic,
            risk_tracker_case_id=request.risk_tracker_case_id,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    finally:
        research_lock.release()