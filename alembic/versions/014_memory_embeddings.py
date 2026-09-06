"""Semantic memory embeddings: pgvector column + ANN index.

Additive and nullable. Rows without an embedding stay readable and recall
falls back to lexical ranking for them, so this is safe to apply before the
backfill runs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014_memory_embeddings"
down_revision: Union[str, None] = "013_discovery_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match Settings.embedding_dimensions (text-embedding-3-small -> 1536).
DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"ALTER TABLE person_semantic_memories ADD COLUMN IF NOT EXISTS embedding vector({DIM})"
    )
    op.add_column(
        "person_semantic_memories",
        sa.Column("embedding_model", sa.String(64), nullable=True),
    )
    # Partial HNSW index: only live rows are recalled, and superseded rows would
    # otherwise bloat the graph.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_person_memories_embedding
        ON person_semantic_memories
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL AND status IN ('active', 'candidate')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_person_memories_embedding")
    op.drop_column("person_semantic_memories", "embedding_model")
    op.execute("ALTER TABLE person_semantic_memories DROP COLUMN IF EXISTS embedding")
