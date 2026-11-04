"""Track how many model requests a daily usage row represents."""
from alembic import op
import sqlalchemy as sa

revision = '0007_usage_calls'
down_revision = '0006_model_usage'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('model_usage')}
    if 'calls' not in columns:
        op.add_column('model_usage', sa.Column('calls', sa.Integer(), nullable=False, server_default='1'))


def downgrade():
    op.drop_column('model_usage', 'calls')
