from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_database
from app.services.tasks import ArtemisTask, utc_now


router = APIRouter(prefix="/tasks", tags=["tasks"])


def task_data(task: ArtemisTask, today: date) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "due_date": task.due_date,
        "completed": task.completed_at is not None,
        "overdue": (
            task.completed_at is None
            and task.due_date is not None
            and task.due_date < today
        ),
    }


@router.get("")
def get_tasks(
    view: Literal["due", "open", "completed"] = "due",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    database: Session = Depends(get_database),
) -> dict:
    today = date.today()

    due_filter = (
        ArtemisTask.completed_at.is_(None),
        ArtemisTask.due_date.is_not(None),
        ArtemisTask.due_date <= today,
    )

    due_count = database.scalar(
        select(func.count())
        .select_from(ArtemisTask)
        .where(*due_filter)
    ) or 0

    statement = select(ArtemisTask)

    if view == "due":
        statement = statement.where(*due_filter)
    elif view == "open":
        statement = statement.where(
            ArtemisTask.completed_at.is_(None)
        )
    else:
        statement = statement.where(
            ArtemisTask.completed_at.is_not(None)
        )

    total = database.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0

    if view == "completed":
        statement = statement.order_by(
            ArtemisTask.completed_at.desc(),
            ArtemisTask.id,
        )
    else:
        statement = statement.order_by(
            ArtemisTask.due_date.is_(None),
            ArtemisTask.due_date,
            ArtemisTask.created_at,
            ArtemisTask.id,
        )

    tasks = database.scalars(
        statement.offset(offset).limit(limit)
    ).all()

    return {
        "items": [task_data(task, today) for task in tasks],
        "due_count": due_count,
        "total_count": total,
        "today": today.isoformat(),
        "limit": limit,
        "offset": offset,
    }


@router.put("/{task_id}/complete")
def finish_task(
    task_id: UUID,
    database: Session = Depends(get_database),
) -> dict:
    task = database.get(ArtemisTask, task_id.hex)

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found.",
        )

    if task.completed_at is None:
        task.completed_at = utc_now()
        try:
            database.commit()
        except Exception:
            database.rollback()
            raise

    return task_data(task, date.today())