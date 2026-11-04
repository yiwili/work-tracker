"""Local model token usage ledger for the AI usage statistics panel."""
from alembic import op
from awt import db
revision = '0006_model_usage'
down_revision = '0005_gitee_export'
branch_labels = None
depends_on = None


def upgrade():
    db.model_usage.create(op.get_bind(), checkfirst=True)


def downgrade():
    db.model_usage.drop(op.get_bind(), checkfirst=True)
