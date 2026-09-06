"""Shared usage counters and durable worker heartbeat."""
from alembic import op
import sqlalchemy as sa

revision = "016_operational_guards"
down_revision = "015_native_qualifications"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("usage_counters",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("used", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_usage_counters_expiry", "usage_counters", ["expires_at"])
    op.create_table("worker_heartbeats",
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("worker_heartbeats")
    op.drop_table("usage_counters")
