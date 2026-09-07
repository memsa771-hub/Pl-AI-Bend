"""Encrypt sensitive Document Intelligence payloads at the application layer."""

from alembic import op
import sqlalchemy as sa


revision = "019_document_sensitive_payloads"
down_revision = "018_document_candidate_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_versions", sa.Column("structured_extraction_encrypted", sa.Text()))
    op.add_column("document_analysis_runs", sa.Column("structured_payload_encrypted", sa.Text()))
    op.add_column("document_facts", sa.Column("raw_value_encrypted", sa.Text()))
    op.add_column("document_facts", sa.Column("normalized_value_encrypted", sa.Text()))
    op.add_column("document_facts", sa.Column("evidence_text_encrypted", sa.Text()))
    op.add_column("document_candidates", sa.Column("value_encrypted", sa.Text()))
    op.add_column("document_candidates", sa.Column("evidence_text_encrypted", sa.Text()))
    op.add_column("verification_cases", sa.Column("existing_value_encrypted", sa.Text()))
    op.add_column("verification_cases", sa.Column("incoming_value_encrypted", sa.Text()))


def downgrade():
    op.drop_column("verification_cases", "incoming_value_encrypted")
    op.drop_column("verification_cases", "existing_value_encrypted")
    op.drop_column("document_candidates", "evidence_text_encrypted")
    op.drop_column("document_candidates", "value_encrypted")
    op.drop_column("document_facts", "evidence_text_encrypted")
    op.drop_column("document_facts", "normalized_value_encrypted")
    op.drop_column("document_facts", "raw_value_encrypted")
    op.drop_column("document_analysis_runs", "structured_payload_encrypted")
    op.drop_column("document_versions", "structured_extraction_encrypted")
