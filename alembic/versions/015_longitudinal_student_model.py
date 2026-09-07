"""Longitudinal student world model: canonical education levels, entity-level
evidence and history, standardized test attempts, and profile issues.

Revision ID: 015_longitudinal_student_model
Revises: 014_memory_embeddings
Create Date: 2026-09-07
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015_longitudinal_student_model"
down_revision: Union[str, None] = "014_memory_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_education_levels() -> None:
    """Map existing education rows onto canonical levels using their wording."""
    from pai.domains.student.education.levels import classify_qualification

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, degree, major FROM educations")
    ).fetchall()
    for row_id, degree, major in rows:
        spec = classify_qualification(" ".join(str(p) for p in (degree, major) if p))
        if spec is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE educations SET canonical_level = :level, framework = :framework, "
                "country = :country, original_name = COALESCE(original_name, :original) "
                "WHERE id = :id"
            ),
            {
                "level": spec.canonical_level,
                "framework": spec.framework,
                "country": spec.country,
                "original": degree,
                "id": row_id,
            },
        )


def _backfill_test_attempts() -> None:
    """Move `application.test_scores` vault values into typed attempts.

    The field changed storage, so without this the scores would still exist but
    no longer be visible anywhere. Superseded rather than deleted, keeping the
    original claim inspectable.
    """
    from pai.domains.student.test_attempts import (
        attempt_status,
        parse_test_observation,
        validity_until,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT vv.id, pv.person_id, vv.value FROM vault_values vv "
            "JOIN person_vaults pv ON pv.id = vv.vault_id "
            "WHERE vv.field_key = 'application.test_scores' AND vv.status = 'active'"
        )
    ).fetchall()

    for value_id, person_id, raw in rows:
        items = raw if isinstance(raw, list) else ([raw] if raw not in (None, "") else [])
        counters: dict[str, int] = {}
        migrated = False
        for item in items:
            observation = parse_test_observation(item)
            if observation is None:
                continue
            test_type = observation["test_type"]
            counters[test_type] = counters.get(test_type, 0) + 1
            expires = validity_until(test_type, observation["test_date"])
            bind.execute(
                sa.text(
                    "INSERT INTO test_attempts (id, person_id, test_type, original_name, "
                    "attempt_number, test_date, overall_score, overall_numeric, sections, "
                    "validity_until, status) VALUES (:id, :person_id, :test_type, :original, "
                    ":attempt, :test_date, :overall, :numeric, :sections, :validity, :status) "
                    "ON CONFLICT ON CONSTRAINT uq_test_attempts_attempt DO NOTHING"
                ).bindparams(sa.bindparam("sections", type_=postgresql.JSONB)),
                {
                    "id": uuid.uuid4(),
                    "person_id": person_id,
                    "test_type": test_type,
                    "original": observation["original_name"],
                    "attempt": counters[test_type],
                    "test_date": observation["test_date"],
                    "overall": observation["overall_score"],
                    "numeric": observation["overall_numeric"],
                    "sections": observation["sections"],
                    "validity": expires,
                    "status": attempt_status(expires),
                },
            )
            migrated = True
        if migrated:
            bind.execute(
                sa.text("UPDATE vault_values SET status = 'superseded' WHERE id = :id"),
                {"id": value_id},
            )


def upgrade() -> None:
    # --- Education: qualification identity -------------------------------
    op.add_column("educations", sa.Column("original_name", sa.String(256), nullable=True))
    op.add_column("educations", sa.Column("canonical_level", sa.String(32), nullable=True))
    op.add_column("educations", sa.Column("framework", sa.String(64), nullable=True))
    op.add_column("educations", sa.Column("country", sa.String(64), nullable=True))
    op.create_index("ix_educations_canonical_level", "educations", ["canonical_level"])

    # --- Evidence and history become entity-aware ------------------------
    op.alter_column(
        "vault_evidence",
        "vault_value_id",
        existing_type=postgresql.UUID(),
        nullable=True,
    )
    op.add_column("vault_evidence", sa.Column("entity_type", sa.String(32), nullable=True))
    op.add_column(
        "vault_evidence", sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("vault_evidence", sa.Column("attribute", sa.String(64), nullable=True))
    op.add_column(
        "vault_evidence",
        sa.Column(
            "verification_level",
            sa.String(32),
            nullable=False,
            server_default="self_reported",
        ),
    )
    op.create_index(
        "ix_vault_evidence_entity", "vault_evidence", ["entity_type", "entity_id", "attribute"]
    )
    op.create_check_constraint(
        "ck_vault_evidence_target",
        "vault_evidence",
        "(vault_value_id IS NOT NULL) OR (entity_type IS NOT NULL AND entity_id IS NOT NULL)",
    )

    op.add_column("vault_history", sa.Column("entity_type", sa.String(32), nullable=True))
    op.add_column(
        "vault_history", sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_index("ix_vault_history_entity", "vault_history", ["entity_type", "entity_id"])

    # --- Standardized test attempts --------------------------------------
    op.create_table(
        "test_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_type", sa.String(32), nullable=False),
        sa.Column("original_name", sa.String(128)),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("test_date", sa.Date),
        sa.Column("overall_score", sa.String(32)),
        sa.Column("overall_numeric", sa.Float),
        sa.Column("sections", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("validity_until", sa.Date),
        sa.Column("status", sa.String(16), nullable=False, server_default="valid"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "person_id", "test_type", "attempt_number", name="uq_test_attempts_attempt"
        ),
    )
    op.create_index("ix_test_attempts_person_id", "test_attempts", ["person_id"])

    # --- Profile issues ---------------------------------------------------
    op.create_table(
        "profile_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("issue_type", sa.String(48), nullable=False),
        sa.Column("fingerprint", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("confidence", sa.Float),
        sa.Column("related_entity_type", sa.String(32)),
        sa.Column("related_entity_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("detected_from", sa.String(64)),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("clarification_needed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("clarification_prompt", sa.Text),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("resolution", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("person_id", "fingerprint", name="uq_profile_issues_fingerprint"),
    )
    op.create_index("ix_profile_issues_person_id", "profile_issues", ["person_id"])
    op.create_index("ix_profile_issues_person_status", "profile_issues", ["person_id", "status"])

    _backfill_education_levels()
    _backfill_test_attempts()


def downgrade() -> None:
    op.drop_index("ix_profile_issues_person_status", table_name="profile_issues")
    op.drop_index("ix_profile_issues_person_id", table_name="profile_issues")
    op.drop_table("profile_issues")

    op.drop_index("ix_test_attempts_person_id", table_name="test_attempts")
    op.drop_table("test_attempts")

    op.drop_index("ix_vault_history_entity", table_name="vault_history")
    op.drop_column("vault_history", "entity_id")
    op.drop_column("vault_history", "entity_type")

    op.drop_constraint("ck_vault_evidence_target", "vault_evidence", type_="check")
    op.drop_index("ix_vault_evidence_entity", table_name="vault_evidence")
    op.execute("DELETE FROM vault_evidence WHERE vault_value_id IS NULL")
    op.drop_column("vault_evidence", "verification_level")
    op.drop_column("vault_evidence", "attribute")
    op.drop_column("vault_evidence", "entity_id")
    op.drop_column("vault_evidence", "entity_type")
    op.alter_column(
        "vault_evidence",
        "vault_value_id",
        existing_type=postgresql.UUID(),
        nullable=False,
    )

    op.drop_index("ix_educations_canonical_level", table_name="educations")
    op.drop_column("educations", "country")
    op.drop_column("educations", "framework")
    op.drop_column("educations", "canonical_level")
    op.drop_column("educations", "original_name")
