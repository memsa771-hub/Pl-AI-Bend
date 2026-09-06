"""Goal, GoalIntelligence, and GoalJob. Tables unchanged (goals, goal_intelligence, goal_jobs)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from pai.platform.database.base import Base


class Goal(Base):
    """Canonical goal identity record — one row per distinct pursuit."""

    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Lifecycle: draft | proposed | active | paused | archived
    lifecycle_status: Mapped[str | None] = mapped_column(String(32))
    # Intelligence pipeline: pending | running | ready | partial | failed | stale
    intelligence_status: Mapped[str | None] = mapped_column(String(32))

    # Legacy status column — kept for backward compat with existing queries
    status: Mapped[str | None] = mapped_column(String(64))
    target_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[str | None] = mapped_column(String(32))

    degree_level: Mapped[str | None] = mapped_column(String(64))
    program: Mapped[str | None] = mapped_column(String(128))
    target_country: Mapped[str | None] = mapped_column(String(128))
    intake_year: Mapped[int | None] = mapped_column(Integer)
    intake_term: Mapped[str | None] = mapped_column(String(32))
    budget_range: Mapped[str | None] = mapped_column(String(64))

    target_company: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str | None] = mapped_column(String(128))

    anchors: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_goals_person_lifecycle", "person_id", "lifecycle_status"),
    )


class GoalIntelligence(Base):
    """Background-computed intelligence summary for one goal. One row per goal."""

    __tablename__ = "goal_intelligence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("goals.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    research: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    assessment: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    gaps: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    plan: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    counselor_brief: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    freshness: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class GoalJob(Base):
    """Durable goal intelligence job. Same poll-loop pattern as PersonJob."""

    __tablename__ = "goal_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), default="goal_intelligence", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_goal_jobs_poll", "status", "available_at"),
        Index("ix_goal_jobs_goal", "goal_id"),
        Index("ix_goal_jobs_person_status", "person_id", "status"),
    )
