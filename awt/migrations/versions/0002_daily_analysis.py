"""Daily AI analysis provenance, separate from imported conversations."""
from alembic import op
from sqlalchemy import Column, JSON, MetaData, String, Table

revision = '0002'
down_revision = '0001'


def upgrade():
    Table('daily_analyses', MetaData(), Column('date', String, primary_key=True), Column('snapshot', JSON)).create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('daily_analyses')
