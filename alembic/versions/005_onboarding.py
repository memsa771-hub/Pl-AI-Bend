"""Person onboarding completion flag."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_onboarding"
down_revision: Union[str, None] = "004_semantic_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "persons",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing profiles already using PAI skip the new required onboarding gate.
    op.execute(
        sa.text(
            "UPDATE persons SET onboarding_completed_at = created_at "
            "WHERE onboarding_completed_at IS NULL AND deleted_at IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("persons", "onboarding_completed_at")
