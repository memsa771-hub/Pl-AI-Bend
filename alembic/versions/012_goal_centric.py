"""Goal-centric PAI: enrich goals table + add goal_intelligence + conversations.active_goal_id."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012_goal_centric"
down_revision: Union[str, None] = "011_document_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Enrich goals ────────────────────────────────────────────────────────
    op.add_column("goals", sa.Column("lifecycle_status", sa.String(32), nullable=True))
    op.add_column("goals", sa.Column("intelligence_status", sa.String(32), nullable=True))
    op.add_column("goals", sa.Column("degree_level", sa.String(64), nullable=True))
    op.add_column("goals", sa.Column("program", sa.String(128), nullable=True))
    op.add_column("goals", sa.Column("target_country", sa.String(128), nullable=True))
    op.add_column("goals", sa.Column("target_company", sa.String(128), nullable=True))
    op.add_column("goals", sa.Column("role", sa.String(128), nullable=True))
    op.add_column("goals", sa.Column("intake_year", sa.Integer(), nullable=True))
    op.add_column("goals", sa.Column("intake_term", sa.String(32), nullable=True))
    op.add_column("goals", sa.Column("budget_range", sa.String(64), nullable=True))
    op.add_column(
        "goals",
        sa.Column(
            "anchors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("goals", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("goals", sa.Column("source_conversation_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.create_index("ix_goals_person_lifecycle", "goals", ["person_id", "lifecycle_status"])

    # ── 2. goal_intelligence table ─────────────────────────────────────────────
    op.create_table(
        "goal_intelligence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "research",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "assessment",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "gaps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("counselor_brief", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column(
            "freshness",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_goal_intelligence_person", "goal_intelligence", ["person_id"])

    # ── 3. goal_jobs table ─────────────────────────────────────────────────────
    op.create_table(
        "goal_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False, server_default="goal_intelligence"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_goal_jobs_poll", "goal_jobs", ["status", "available_at"])
    op.create_index("ix_goal_jobs_goal", "goal_jobs", ["goal_id"])
    op.create_index("ix_goal_jobs_person_status", "goal_jobs", ["person_id", "status"])

    # ── 4. conversations.active_goal_id ────────────────────────────────────────
    op.add_column(
        "conversations",
        sa.Column("active_goal_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "active_goal_id")

    op.drop_index("ix_goal_jobs_person_status", table_name="goal_jobs")
    op.drop_index("ix_goal_jobs_goal", table_name="goal_jobs")
    op.drop_index("ix_goal_jobs_poll", table_name="goal_jobs")
    op.drop_table("goal_jobs")

    op.drop_index("ix_goal_intelligence_person", table_name="goal_intelligence")
    op.drop_table("goal_intelligence")

    op.drop_index("ix_goals_person_lifecycle", table_name="goals")
    op.drop_column("goals", "source_conversation_id")
    op.drop_column("goals", "confidence")
    op.drop_column("goals", "anchors")
    op.drop_column("goals", "budget_range")
    op.drop_column("goals", "intake_term")
    op.drop_column("goals", "intake_year")
    op.drop_column("goals", "role")
    op.drop_column("goals", "target_company")
    op.drop_column("goals", "target_country")
    op.drop_column("goals", "program")
    op.drop_column("goals", "degree_level")
    op.drop_column("goals", "intelligence_status")
    op.drop_column("goals", "lifecycle_status")
