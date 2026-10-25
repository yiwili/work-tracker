"""Initial local event store and reviewed worklogs."""
from alembic import op
from awt.db import metadata

revision = "0001"
down_revision = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    metadata.drop_all(op.get_bind())
