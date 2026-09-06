"""Person semantic memories for AgentSpan long-term recall."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_semantic_memory"
down_revision: Union[str, None] = "003_student_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "person_semantic_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_person_semantic_memories_person_id",
        "person_semantic_memories",
        ["person_id"],
    )
    op.create_index(
        "ix_person_semantic_memories_external_id",
        "person_semantic_memories",
        ["external_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_person_semantic_memories_external_id", table_name="person_semantic_memories")
    op.drop_index("ix_person_semantic_memories_person_id", table_name="person_semantic_memories")
    op.drop_table("person_semantic_memories")
