"""Document Vault: logical documents, file versions, provenance."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010_document_vault"
down_revision: Union[str, None] = "009_person_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("original_filename", sa.String(256), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("content_text", sa.Text()),
        sa.Column("created_by", sa.String(16), nullable=False, server_default="student"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.add_column("documents", sa.Column("title", sa.String(256)))
    op.add_column(
        "documents",
        sa.Column("source_type", sa.String(32), nullable=False, server_default="document_vault"),
    )
    op.add_column(
        "documents",
        sa.Column("created_by", sa.String(16), nullable=False, server_default="student"),
    )
    op.add_column(
        "documents",
        sa.Column(
            "vault_extraction_policy",
            sa.String(16),
            nullable=False,
            server_default="extract",
        ),
    )
    op.add_column("documents", sa.Column("current_version_id", postgresql.UUID(as_uuid=True)))
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.execute(
        sa.text(
            """
            INSERT INTO document_versions (
                id, document_id, version_number, storage_path, original_filename,
                mime_type, size_bytes, created_by, created_at
            )
            SELECT gen_random_uuid(), id, 1, storage_path, original_filename,
                   mime_type, size_bytes, 'student', created_at
            FROM documents
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE documents d
            SET current_version_id = v.id,
                title = COALESCE(d.title, d.original_filename),
                document_type = CASE
                    WHEN d.document_type IS NULL OR d.document_type = 'generic'
                    THEN 'other' ELSE d.document_type END
            FROM document_versions v
            WHERE v.document_id = d.id AND v.version_number = 1
            """
        )
    )
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "document_jobs",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index(
        "ix_document_jobs_document_version_id", "document_jobs", ["document_version_id"]
    )
    op.create_foreign_key(
        "fk_document_jobs_version",
        "document_jobs",
        "document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            """
            UPDATE document_jobs j
            SET document_version_id = d.current_version_id
            FROM documents d
            WHERE j.document_id = d.id
            """
        )
    )

    op.add_column(
        "document_candidates",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "document_candidates",
        sa.Column("document_job_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index(
        "ix_document_candidates_document_version_id",
        "document_candidates",
        ["document_version_id"],
    )
    op.create_index(
        "ix_document_candidates_document_job_id",
        "document_candidates",
        ["document_job_id"],
    )
    op.create_foreign_key(
        "fk_document_candidates_version",
        "document_candidates",
        "document_versions",
        ["document_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_document_candidates_job",
        "document_candidates",
        "document_jobs",
        ["document_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "message_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
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
        sa.UniqueConstraint("message_id", "document_id", name="uq_message_documents"),
    )
    op.create_index("ix_message_documents_message_id", "message_documents", ["message_id"])
    op.create_index("ix_message_documents_document_id", "message_documents", ["document_id"])


def downgrade() -> None:
    op.drop_table("message_documents")
    op.drop_constraint("fk_document_candidates_job", "document_candidates", type_="foreignkey")
    op.drop_constraint("fk_document_candidates_version", "document_candidates", type_="foreignkey")
    op.drop_index("ix_document_candidates_document_job_id", table_name="document_candidates")
    op.drop_index("ix_document_candidates_document_version_id", table_name="document_candidates")
    op.drop_column("document_candidates", "document_job_id")
    op.drop_column("document_candidates", "document_version_id")
    op.drop_constraint("fk_document_jobs_version", "document_jobs", type_="foreignkey")
    op.drop_index("ix_document_jobs_document_version_id", table_name="document_jobs")
    op.drop_column("document_jobs", "document_version_id")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "current_version_id")
    op.drop_column("documents", "vault_extraction_policy")
    op.drop_column("documents", "created_by")
    op.drop_column("documents", "source_type")
    op.drop_column("documents", "title")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
