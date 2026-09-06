"""Preserve native qualification metadata without requiring an institution."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "015_native_qualifications"
down_revision = "014_memory_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("educations", sa.Column("qualification_data", postgresql.JSONB(), nullable=True))
    op.alter_column("educations", "institution", existing_type=sa.String(256), nullable=True)


def downgrade():
    # Keep nullable institutions: fabricating names would corrupt existing records.
    op.drop_column("educations", "qualification_data")
