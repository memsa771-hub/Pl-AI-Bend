"""Document Intelligence: statuses, analysis runs, facts, parties, verification."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_document_intelligence"
down_revision: Union[str, None] = "010_document_vault"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("category", sa.String(32), nullable=False, server_default="other"),
    )
    op.add_column(
        "documents",
        sa.Column("base_criticality", sa.String(16), nullable=False, server_default="normal"),
    )
    op.add_column(
        "documents",
        sa.Column("evidence_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "documents",
        sa.Column("trust_level", sa.String(32), nullable=False, server_default="student_provided"),
    )
    op.add_column(
        "documents",
        sa.Column("verification_status", sa.String(32), nullable=False, server_default="unverified"),
    )
    op.add_column(
        "documents",
        sa.Column("requirement_status", sa.String(16), nullable=False, server_default="optional"),
    )
    op.add_column(
        "documents",
        sa.Column("lifecycle_status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "documents",
        sa.Column("authenticity_status", sa.String(32), nullable=False, server_default="unverified"),
    )
    op.add_column(
        "documents",
        sa.Column("identity_status", sa.String(32), nullable=False, server_default="not_applicable"),
    )
    op.add_column("document_jobs", sa.Column("current_stage", sa.String(32)))
    op.add_column("document_jobs", sa.Column("analysis_run_id", postgresql.UUID(as_uuid=True)))

    op.execute(
        sa.text(
            """
            UPDATE documents
            SET evidence_eligible = FALSE,
                trust_level = 'pai_generated',
                category = 'generated'
            WHERE source_type = 'ai_generated'
            """
        )
    )

    op.create_table(
        "document_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("related_type", sa.String(32), nullable=False),
        sa.Column("related_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_document_relations_document_id", "document_relations", ["document_id"])
    op.create_index(
        "ix_document_relations_related", "document_relations", ["related_type", "related_id"]
    )

    op.create_table(
        "document_analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "document_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("pipeline_version", sa.String(32), nullable=False),
        sa.Column("ocr_provider", sa.String(64)),
        sa.Column("ocr_model", sa.String(128)),
        sa.Column("classifier_version", sa.String(32)),
        sa.Column("extractor_version", sa.String(32)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("normalization_version", sa.String(32)),
        sa.Column("reconciliation_policy_version", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("current_stage", sa.String(32), nullable=False, server_default="security"),
        sa.Column("completed_stages", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("digitization", postgresql.JSONB()),
        sa.Column("provider_artifact_path", sa.String(512)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_document_analysis_runs_document_id", "document_analysis_runs", ["document_id"]
    )
    op.create_index(
        "ix_document_analysis_runs_document_version_id",
        "document_analysis_runs",
        ["document_version_id"],
    )

    op.create_table(
        "document_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "analysis_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_analysis_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("entity_type", sa.String(64)),
        sa.Column("entity_reference", sa.String(128)),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("raw_value", postgresql.JSONB()),
        sa.Column("normalized_value", postgresql.JSONB()),
        sa.Column("page", sa.Integer()),
        sa.Column("bounding_box", postgresql.JSONB()),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("ocr_confidence", sa.Float()),
        sa.Column("extraction_confidence", sa.Float(), nullable=False),
        sa.Column("normalization_confidence", sa.Float()),
        sa.Column("document_quality", sa.String(32)),
        sa.Column("source_authority", sa.String(16), nullable=False, server_default="none"),
        sa.Column("field_criticality", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("identity_status", sa.String(32), nullable=False, server_default="not_applicable"),
        sa.Column("reconciliation_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sensitivity", sa.String(32), nullable=False, server_default="personal"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_document_facts_person_id", "document_facts", ["person_id"])
    op.create_index("ix_document_facts_document_id", "document_facts", ["document_id"])
    op.create_index("ix_document_facts_analysis_run_id", "document_facts", ["analysis_run_id"])

    op.create_table(
        "document_parties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "analysis_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_analysis_runs.id", ondelete="SET NULL"),
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(256)),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("identity_status", sa.String(32), nullable=False, server_default="not_applicable"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_document_parties_document_id", "document_parties", ["document_id"])

    op.create_table(
        "verification_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "incoming_document_fact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_facts.id", ondelete="SET NULL"),
        ),
        sa.Column("case_type", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="high"),
        sa.Column("target_entity", sa.String(128)),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("existing_value", postgresql.JSONB()),
        sa.Column("incoming_value", postgresql.JSONB()),
        sa.Column("existing_evidence", postgresql.JSONB()),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(48), nullable=False, server_default="open"),
        sa.Column("resolution_type", sa.String(48)),
        sa.Column("resolution_notes", sa.Text()),
        sa.Column("presented_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_verification_cases_person_id", "verification_cases", ["person_id"])
    op.create_index("ix_verification_cases_document_id", "verification_cases", ["document_id"])
    op.create_index("ix_verification_cases_open", "verification_cases", ["person_id", "status"])


def downgrade() -> None:
    op.drop_table("verification_cases")
    op.drop_table("document_parties")
    op.drop_table("document_facts")
    op.drop_table("document_analysis_runs")
    op.drop_table("document_relations")
    op.drop_column("document_jobs", "analysis_run_id")
    op.drop_column("document_jobs", "current_stage")
    for col in (
        "identity_status",
        "authenticity_status",
        "lifecycle_status",
        "requirement_status",
        "verification_status",
        "trust_level",
        "evidence_eligible",
        "base_criticality",
        "category",
    ):
        op.drop_column("documents", col)
