"""Enterprise work-item export previews and durable duplicate protection."""
from alembic import op
from awt import db
revision = '0005_gitee_export'
down_revision = '0004_history'
branch_labels = None
depends_on = None

def upgrade():
    for table in (db.gitee_batches,db.gitee_links):
        table.create(op.get_bind(),checkfirst=True)

def downgrade():
    for table in (db.gitee_links,db.gitee_batches):
        table.drop(op.get_bind(),checkfirst=True)
