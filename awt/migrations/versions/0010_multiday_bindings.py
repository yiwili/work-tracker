"""Add persistent multi-day merge bindings table."""
from alembic import op
import sqlalchemy as sa

revision = '0010_multiday_bindings'
down_revision = '0009_task_is_todo'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if 'multiday_bindings' not in existing_tables:
        op.create_table(
            'multiday_bindings',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('start_date', sa.String(), nullable=False),
            sa.Column('end_date', sa.String(), nullable=False),
            sa.Column('anchor_date', sa.String(), nullable=False),
            sa.Column('target_minutes', sa.Integer(), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.String(), nullable=True),
            sa.Column('unbound_at', sa.String(), nullable=True),
        )
        op.create_index('ix_multiday_bindings_start_date', 'multiday_bindings', ['start_date'])
        op.create_index('ix_multiday_bindings_end_date', 'multiday_bindings', ['end_date'])


def downgrade():
    op.drop_table('multiday_bindings')
