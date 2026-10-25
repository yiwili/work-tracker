"""Track pinned manual minutes separately from AI allocation suggestions."""
from alembic import op
from sqlalchemy import Boolean, Column, JSON, MetaData, String, Table, Text, inspect, text

revision = '0003'
down_revision = '0002'


def upgrade():
    connection = op.get_bind()
    columns = {c['name'] for c in inspect(connection).get_columns('worklogs')}
    for column in [Column('time_locked', Boolean, nullable=False, server_default='0'), Column('time_source', String, nullable=False, server_default='manual'), Column('time_reason', Text, nullable=False, server_default='')]:
        if column.name not in columns:
            op.add_column('worklogs', column)
    connection.execute(text("UPDATE worklogs SET time_locked=1 WHERE confirmed_minutes>0 AND time_source='manual'"))
    Table('time_allocations', MetaData(), Column('date', String, primary_key=True), Column('snapshot', JSON)).create(connection, checkfirst=True)


def downgrade():
    op.drop_table('time_allocations')
    with op.batch_alter_table('worklogs') as batch:
        for name in ('time_locked', 'time_source', 'time_reason'):
            batch.drop_column(name)
