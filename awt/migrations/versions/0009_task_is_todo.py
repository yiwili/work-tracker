"""Separate explicit todos from manual daily worklog entries."""
from alembic import op
import sqlalchemy as sa

revision = '0009_task_is_todo'
down_revision = '0008_cloud_sync'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column['name'] for column in inspector.get_columns('work_tasks')}
    if 'is_todo' not in columns:
        op.add_column('work_tasks', sa.Column('is_todo', sa.Boolean(), nullable=False, server_default='1'))
        op.execute(
            """
            UPDATE work_tasks
            SET is_todo = 0
            WHERE origin != 'manual'
               OR id IN (SELECT task_id FROM worklogs WHERE confirmed_minutes > 0)
            """
        )


def downgrade():
    op.drop_column('work_tasks', 'is_todo')
