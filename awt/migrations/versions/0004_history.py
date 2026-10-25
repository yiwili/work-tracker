"""Historical work items and integration checkpoints."""
from alembic import op
from awt import db
revision = '0004_history'
down_revision = '0003'
branch_labels = None
depends_on = None

def upgrade():
    for table in (db.history, db.integrations, db.allocation_requests):
        table.create(op.get_bind(), checkfirst=True)

def downgrade():
    for table in (db.allocation_requests, db.integrations, db.history):
        table.drop(op.get_bind(), checkfirst=True)
