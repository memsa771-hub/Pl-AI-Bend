"""Initial Person and Vault schema (Phase 2).

Revision ID: 001_phase2_person_vault
Revises:
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_phase2_person_vault"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("auth_provider", sa.String(64), nullable=False),
        sa.Column("external_auth_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("full_name", sa.String(256)),
        sa.Column("preferred_name", sa.String(256)),
        sa.Column("phone", sa.String(64)),
        sa.Column("account_status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("auth_provider", "external_auth_id", name="uq_persons_auth_identity"),
    )
    op.create_index("ix_persons_email_lower", "persons", [sa.text("lower(email)")])

    op.create_table(
        "person_vaults",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("catalog_version", sa.String(32), nullable=False),
        sa.Column("applicable_scopes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[\"universal\"]'::jsonb")),
        sa.Column("critical_completion", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("important_completion", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enrichment_completion", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overall_completion", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    for name, extra in [
        (
            "educations",
            [
                sa.Column("institution", sa.String(256), nullable=False),
                sa.Column("degree", sa.String(128)),
                sa.Column("major", sa.String(128)),
                sa.Column("start_date", sa.Date()),
                sa.Column("end_date", sa.Date()),
                sa.Column("graduation_year", sa.Integer()),
                sa.Column("gpa", sa.Float()),
                sa.Column("gpa_scale", sa.Float()),
                sa.Column("percentage", sa.Float()),
                sa.Column("status", sa.String(64)),
            ],
        ),
        (
            "work_experiences",
            [
                sa.Column("organization", sa.String(256), nullable=False),
                sa.Column("title", sa.String(256), nullable=False),
                sa.Column("employment_type", sa.String(64)),
                sa.Column("start_date", sa.Date()),
                sa.Column("end_date", sa.Date()),
                sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.Column("description", sa.Text()),
            ],
        ),
        (
            "projects",
            [
                sa.Column("name", sa.String(256), nullable=False),
                sa.Column("description", sa.Text()),
                sa.Column("role", sa.String(128)),
                sa.Column("start_date", sa.Date()),
                sa.Column("end_date", sa.Date()),
                sa.Column("url", sa.String(512)),
            ],
        ),
        (
            "skills",
            [
                sa.Column("name", sa.String(128), nullable=False),
                sa.Column("proficiency", sa.String(64)),
                sa.Column("years_experience", sa.Float()),
            ],
        ),
        (
            "certifications",
            [
                sa.Column("name", sa.String(256), nullable=False),
                sa.Column("issuer", sa.String(256)),
                sa.Column("issue_date", sa.Date()),
                sa.Column("expiry_date", sa.Date()),
                sa.Column("credential_url", sa.String(512)),
            ],
        ),
        (
            "goals",
            [
                sa.Column("goal_type", sa.String(64), nullable=False),
                sa.Column("title", sa.String(256), nullable=False),
                sa.Column("description", sa.Text()),
                sa.Column("status", sa.String(64)),
                sa.Column("target_date", sa.Date()),
                sa.Column("priority", sa.String(32)),
            ],
        ),
    ]:
        op.create_table(
            name,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            *extra,
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(f"ix_{name}_person_id", name, ["person_id"])

    op.create_table(
        "vault_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("person_vaults.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("value", postgresql.JSONB()),
        sa.Column("value_encrypted", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("verification_level", sa.String(32), nullable=False, server_default="self_reported"),
        sa.Column("confidence", sa.Float()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vault_values.id")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_vault_values_vault_id", "vault_values", ["vault_id"])
    op.create_index("ix_vault_values_vault_field", "vault_values", ["vault_id", "field_key"])

    op.create_table(
        "vault_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vault_value_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vault_values.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_reference", sa.String(512)),
        sa.Column("evidence_text", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vault_evidence_vault_value_id", "vault_evidence", ["vault_value_id"])

    op.create_table(
        "vault_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("person_vaults.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB()),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_vault_history_vault_id", "vault_history", ["vault_id"])

    op.create_table(
        "person_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("granted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("person_id", "category", name="uq_person_consents_category"),
    )
    op.create_index("ix_person_consents_person_id", "person_consents", ["person_id"])


def downgrade() -> None:
    op.drop_table("person_consents")
    op.drop_table("vault_history")
    op.drop_table("vault_evidence")
    op.drop_table("vault_values")
    for name in ("goals", "certifications", "skills", "projects", "work_experiences", "educations"):
        op.drop_table(name)
    op.drop_table("person_vaults")
    op.drop_index("ix_persons_email_lower", table_name="persons")
    op.drop_table("persons")
