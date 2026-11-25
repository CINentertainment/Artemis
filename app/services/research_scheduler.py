import json
import logging
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base, SessionLocal, engine, get_database
from app.research_models import ResearchRequisition, ResearchRun
from app.routes.research import (
    RunRequest,
    compare_runs,
    get_requisition,
    run_requisition,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/research/requisitions",
    tags=["research schedules"],
)

POLL_SECONDS = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat()


class ResearchSchedule(Base):
    __tablename__ = "research_schedules"

    requisition_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer,
        default=1440,
        nullable=False,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        index=True,
    )
    last_started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    last_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    last_run_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    last_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    comparison_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


class ScheduleRequest(BaseModel):
    enabled: bool = False

    initial_delay_minutes: int | None = Field(default=None, ge=0, le=10080)

    interval_minutes: int = Field(
        default=1440,
        ge=5,
        le=10080,
    )


def schedule_data(record: ResearchSchedule) -> dict:
    return {
        "requisition_id": record.requisition_id,
        "enabled": record.enabled,
        "interval_minutes": record.interval_minutes,
        "next_run_at": timestamp(record.next_run_at),
        "last_started_at": timestamp(record.last_started_at),
        "last_finished_at": timestamp(record.last_finished_at),
        "last_run_id": record.last_run_id,
        "last_status": record.last_status,
        "last_error": record.last_error,
        "comparison": (
            json.loads(record.comparison_json)
            if record.comparison_json
            else None
        ),
    }


@router.get("/{requisition_id}/schedule")
def read_schedule(
    requisition_id: UUID,
    database: Session = Depends(get_database),
) -> dict:
    get_requisition(database, requisition_id)

    record = database.get(
        ResearchSchedule,
        str(requisition_id),
    )

    if record is None:
        return {
            "requisition_id": str(requisition_id),
            "enabled": False,
            "interval_minutes": 1440,
            "next_run_at": None,
            "last_started_at": None,
            "last_finished_at": None,
            "last_run_id": None,
            "last_status": None,
            "last_error": None,
            "comparison": None,
        }

    return schedule_data(record)


@router.put("/{requisition_id}/schedule")
def save_schedule(
    requisition_id: UUID,
    request: ScheduleRequest,
    database: Session = Depends(get_database),
) -> dict:
    get_requisition(database, requisition_id)

    record = database.get(
        ResearchSchedule,
        str(requisition_id),
    )

    if record is None:
        record = ResearchSchedule(
            requisition_id=str(requisition_id),
        )
        database.add(record)

    record.enabled = request.enabled
    record.interval_minutes = request.interval_minutes

    # Enabling or changing a schedule starts a fresh interval.
    # Disabling it prevents future starts; an active run can finish.
    record.next_run_at = (
        utc_now() + timedelta(minutes=request.initial_delay_minutes if request.initial_delay_minutes is not None else request.interval_minutes)
        if request.enabled
        else None
    )

    database.commit()
    return schedule_data(record)


class ResearchScheduler:
    def __init__(self) -> None:
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        # Create only this new table. Existing tables remain intact.
        ResearchSchedule.__table__.create(
            bind=engine,
            checkfirst=True,
        )

        self._recover_previous_run()

        self._stop.clear()
        self._thread = Thread(
            target=self._loop,
            name="artemis-research-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Research scheduler started.")

    def stop(self) -> None:
        self._stop.set()

        # Let an active research run finish and save its result.
        # The application lifespan will await this outside the event loop.
        if self._thread is not None:
            self._thread.join()
            self._thread = None

        logger.info("Research scheduler stopped.")

    def _recover_previous_run(self) -> None:
        with SessionLocal() as database:
            records = database.scalars(
                select(ResearchSchedule).where(
                    ResearchSchedule.last_status == "running"
                )
            ).all()

            for schedule in records:
                run = (
                    database.get(ResearchRun, schedule.last_run_id)
                    if schedule.last_run_id
                    else None
                )

                if run is not None and run.status != "running":
                    schedule.last_status = run.status
                    schedule.last_error = run.error
                else:
                    schedule.last_status = "interrupted"
                    schedule.last_error = (
                        "Artemis stopped before the scheduled attempt "
                        "finished. Review its saved run before retrying."
                    )

                schedule.last_finished_at = utc_now()

            database.commit()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_due()
            except Exception:
                logger.exception("Research scheduler check failed.")

            self._stop.wait(POLL_SECONDS)

    def _check_due(self) -> None:
        with SessionLocal() as database:
            requisition_ids = database.scalars(
                select(ResearchSchedule.requisition_id)
                .where(
                    ResearchSchedule.enabled.is_(True),
                    ResearchSchedule.next_run_at <= utc_now(),
                )
                .order_by(
                    ResearchSchedule.next_run_at,
                    ResearchSchedule.requisition_id,
                )
            ).all()

        for requisition_id in requisition_ids:
            if self._stop.is_set():
                return

            try:
                self._run_due(requisition_id)
            except Exception:
                logger.exception(
                    "Scheduled research failed for %s.",
                    requisition_id,
                )

    def _run_due(self, requisition_id: str) -> None:
        with SessionLocal() as database:
            schedule = database.get(
                ResearchSchedule,
                requisition_id,
            )

            if (
                schedule is None
                or not schedule.enabled
                or schedule.next_run_at is None
                or schedule.next_run_at > utc_now()
            ):
                return

            requisition = database.get(
                ResearchRequisition,
                requisition_id,
            )

            if requisition is None:
                schedule.enabled = False
                schedule.next_run_at = None
                schedule.last_status = "failed"
                schedule.last_error = "Research requisition not found."
                database.commit()
                return

            previous_run_id = database.scalar(
                select(ResearchRun.id)
                .where(
                    ResearchRun.requisition_id == requisition_id,
                    ResearchRun.status == "completed",
                )
                .order_by(
                    ResearchRun.started_at.desc(),
                    ResearchRun.id,
                )
                .limit(1)
            )

            request_id = uuid4()
            schedule.last_run_id = str(request_id)
            schedule.last_started_at = utc_now()
            schedule.last_finished_at = None
            schedule.last_status = "running"
            schedule.last_error = None
            schedule.comparison_json = None

            # Reserve the next interval before starting.
            # Missed intervals do not create a backlog of runs.
            schedule.next_run_at = utc_now() + timedelta(
                minutes=schedule.interval_minutes
            )
            database.commit()

        try:
            result = run_requisition(
                requisition_id=UUID(requisition_id),
                request=RunRequest(request_id=request_id),
            )
        except HTTPException as error:
            if error.status_code == 409:
                self._defer_busy(requisition_id)
                return

            self._record_failure(
                requisition_id,
                f"HTTP {error.status_code}: {error.detail}",
            )
            return
        except Exception as error:
            self._record_failure(
                requisition_id,
                f"{type(error).__name__}: {error}",
            )
            return

        comparison_json = None
        comparison_error = None

        if result["status"] == "completed" and previous_run_id:
            try:
                with SessionLocal() as database:
                    comparison = compare_runs(
                        requisition_id=UUID(requisition_id),
                        baseline_run_id=UUID(previous_run_id),
                        target_run_id=UUID(result["id"]),
                        database=database,
                    )

                comparison_json = json.dumps(
                    comparison,
                    ensure_ascii=False,
                    default=str,
                )
            except Exception as error:
                comparison_error = (
                    "Research result saved, but comparison failed: "
                    f"{type(error).__name__}: {error}"
                )

        with SessionLocal() as database:
            schedule = database.get(
                ResearchSchedule,
                requisition_id,
            )
            if schedule is None:
                return

            schedule.last_status = result["status"]
            schedule.last_error = result.get("error") or comparison_error
            schedule.last_finished_at = utc_now()
            schedule.comparison_json = comparison_json

            # Preserve pause/resume changes made during collection.
            if schedule.enabled:
                schedule.next_run_at = utc_now() + timedelta(
                    minutes=schedule.interval_minutes
                )

            database.commit()

    def _defer_busy(self, requisition_id: str) -> None:
        with SessionLocal() as database:
            schedule = database.get(
                ResearchSchedule,
                requisition_id,
            )
            if schedule is None:
                return

            schedule.last_status = "deferred"
            schedule.last_error = (
                "Another research run is active. Retrying shortly."
            )
            schedule.last_run_id = None
            schedule.last_finished_at = utc_now()

            if schedule.enabled:
                schedule.next_run_at = utc_now() + timedelta(
                    seconds=60
                )

            database.commit()

    def _record_failure(
        self,
        requisition_id: str,
        message: str,
    ) -> None:
        with SessionLocal() as database:
            schedule = database.get(
                ResearchSchedule,
                requisition_id,
            )
            if schedule is None:
                return

            schedule.last_status = "failed"
            schedule.last_error = message
            schedule.last_finished_at = utc_now()

            if schedule.enabled:
                schedule.next_run_at = utc_now() + timedelta(
                    minutes=schedule.interval_minutes
                )

            database.commit()


research_scheduler = ResearchScheduler()