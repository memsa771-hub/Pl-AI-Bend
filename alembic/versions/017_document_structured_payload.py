"""Persist canonical document understanding and explicit document origin."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "017_document_structured_payload"
down_revision = "016_operational_guards"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("origin", sa.String(32), nullable=False, server_default="user_uploaded"),
    )
    op.execute(
        "UPDATE documents SET origin = 'pai_generated' WHERE source_type = 'ai_generated'"
    )
    op.add_column(
        "document_versions",
        sa.Column("structured_extraction", postgresql.JSONB()),
    )
    op.add_column(
        "document_analysis_runs",
        sa.Column("structured_payload", postgresql.JSONB()),
    )
    op.add_column(
        "document_analysis_runs",
        sa.Column("schema_version", sa.String(64)),
    )
    op.add_column(
        "document_analysis_runs",
        sa.Column("structured_document_type", sa.String(64)),
    )


def downgrade():
    op.drop_column("document_analysis_runs", "structured_document_type")
    op.drop_column("document_analysis_runs", "schema_version")
    op.drop_column("document_analysis_runs", "structured_payload")
    op.drop_column("document_versions", "structured_extraction")
    op.drop_column("documents", "origin")
