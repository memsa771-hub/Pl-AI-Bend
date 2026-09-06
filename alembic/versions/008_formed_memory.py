"""Formed-memory columns on person_semantic_memories."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_formed_memory"
down_revision: Union[str, None] = "007_person_journey"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "person_semantic_memories",
        sa.Column("memory_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="note"),
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column(
            "formation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_person_memories_person_status",
        "person_semantic_memories",
        ["person_id", "status"],
    )
    op.create_index(
        "uq_person_memories_live_key",
        "person_semantic_memories",
        ["person_id", "memory_key"],
        unique=True,
        postgresql_where=sa.text(
            "memory_key IS NOT NULL AND status IN ('active', 'candidate')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_person_memories_live_key", table_name="person_semantic_memories")
    op.drop_index("ix_person_memories_person_status", table_name="person_semantic_memories")
    op.drop_column("person_semantic_memories", "formation")
    op.drop_column("person_semantic_memories", "valid_until")
    op.drop_column("person_semantic_memories", "last_confirmed_at")
    op.drop_column("person_semantic_memories", "version")
    op.drop_column("person_semantic_memories", "status")
    op.drop_column("person_semantic_memories", "kind")
    op.drop_column("person_semantic_memories", "memory_key")
