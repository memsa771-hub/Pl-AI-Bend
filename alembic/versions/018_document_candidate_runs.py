"""Bind reviewable document candidates to their producing analysis run."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "018_document_candidate_runs"
down_revision = "017_document_structured_payload"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_candidates",
        sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_document_candidates_analysis_run",
        "document_candidates",
        "document_analysis_runs",
        ["analysis_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_document_candidates_analysis_run_id",
        "document_candidates",
        ["analysis_run_id"],
    )
    op.execute(
        """
        UPDATE document_candidates AS candidate
        SET analysis_run_id = job.analysis_run_id
        FROM document_jobs AS job
        WHERE candidate.document_job_id = job.id
          AND candidate.analysis_run_id IS NULL
        """
    )


def downgrade():
    op.drop_index("ix_document_candidates_analysis_run_id", table_name="document_candidates")
    op.drop_constraint(
        "fk_document_candidates_analysis_run", "document_candidates", type_="foreignkey"
    )
    op.drop_column("document_candidates", "analysis_run_id")
