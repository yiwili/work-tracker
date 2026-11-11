"""Add analysis_exclusions table for excluding conversations from future AI analysis."""
from alembic import op
import sqlalchemy as sa

revision = '0011_analysis_exclusions'
down_revision = '0010_multiday_bindings'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if 'analysis_exclusions' not in existing_tables:
        op.create_table(
            'analysis_exclusions',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('scope', sa.String(), nullable=False),
            sa.Column('target_key', sa.String(), nullable=False),
            sa.Column('source', sa.String(), nullable=False),
            sa.Column('session_id', sa.String(), nullable=False),
            sa.Column('event_id', sa.String(), nullable=True),
            sa.Column('reason', sa.String(), nullable=False, server_default=''),
            sa.Column('note', sa.Text(), nullable=False, server_default=''),
            sa.Column('created_at', sa.String(), nullable=False),
        )
        op.create_index('ix_analysis_exclusions_scope', 'analysis_exclusions', ['scope'])
        op.create_index('ix_analysis_exclusions_target_key', 'analysis_exclusions', ['target_key'])
        op.create_index('ix_analysis_exclusions_source', 'analysis_exclusions', ['source'])
        op.create_index('ix_analysis_exclusions_session_id', 'analysis_exclusions', ['session_id'])
        op.create_index('ix_analysis_exclusions_event_id', 'analysis_exclusions', ['event_id'])
        op.create_index(
            'uq_analysis_exclusions_scope_target',
            'analysis_exclusions',
            ['scope', 'target_key'],
            unique=True,
        )


def downgrade():
    op.drop_table('analysis_exclusions')
