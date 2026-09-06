"""Profile Discovery / Gap Selection: track last-asked field per conversation."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013_discovery_tracking"
down_revision: Union[str, None] = "012_goal_centric"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("last_discovery_field_key", sa.String(128), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("last_discovery_asked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "last_discovery_asked_at")
    op.drop_column("conversations", "last_discovery_field_key")
