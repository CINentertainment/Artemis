from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ResearchRequisition(Base):
    __tablename__ = "research_requisitions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    topic: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )
    questions_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
    )
    risk_tracker_case_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )
    requisition_id: Mapped[str] = mapped_column(
        ForeignKey(
            "research_requisitions.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="running",
        index=True,
    )
    request_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    result_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )