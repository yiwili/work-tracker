"""Add compressed_memories table for local semantic compression storage."""
from alembic import op
import sqlalchemy as sa

revision = '0012_memory_compression'
down_revision = '0011_analysis_exclusions'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    if 'compressed_memories' not in existing_tables:
        op.create_table(
            'compressed_memories',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('source', sa.String(), nullable=False),
            sa.Column('session_id', sa.String(), nullable=False),
            sa.Column('date_key', sa.String(), nullable=False),
            sa.Column('content_hash', sa.String(), nullable=False),
            sa.Column('model', sa.String(), nullable=False, server_default=''),
            sa.Column('raw_chars', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('compressed_chars', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('raw_events_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('segments_json', sa.JSON(), nullable=False),
            sa.Column('summary_text', sa.Text(), nullable=False, server_default=''),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('updated_at', sa.String(), nullable=False),
        )
        op.create_index('ix_compressed_memories_source', 'compressed_memories', ['source'])
        op.create_index('ix_compressed_memories_session_id', 'compressed_memories', ['session_id'])
        op.create_index('ix_compressed_memories_date_key', 'compressed_memories', ['date_key'])
        op.create_index('ix_compressed_memories_content_hash', 'compressed_memories', ['content_hash'])


def downgrade():
    op.drop_table('compressed_memories')
