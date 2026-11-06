"""Add cloud sync tables and change capture triggers.

Revision ID: 0008_cloud_sync
Revises: 0007_usage_calls
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_cloud_sync'
down_revision = '0007_usage_calls'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if 'sync_meta' not in existing_tables:
        op.create_table(
            'sync_meta',
            sa.Column('key', sa.String(), primary_key=True),
            sa.Column('value', sa.JSON(), nullable=True),
        )

    if 'sync_shadow' not in existing_tables:
        op.create_table(
            'sync_shadow',
            sa.Column('entity_type', sa.String(), primary_key=True),
            sa.Column('entity_id', sa.String(), primary_key=True),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('fields', sa.JSON(), nullable=False),
        )

    if 'sync_dirty' not in existing_tables:
        op.create_table(
            'sync_dirty',
            sa.Column('entity_type', sa.String(), primary_key=True),
            sa.Column('entity_id', sa.String(), primary_key=True),
            sa.Column('changed_at', sa.String(), nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
        )

    if 'sync_ops' not in existing_tables:
        op.create_table(
            'sync_ops',
            sa.Column('op_id', sa.String(), primary_key=True),
            sa.Column('entity_type', sa.String(), nullable=False),
            sa.Column('entity_id', sa.String(), nullable=False),
            sa.Column('base_version', sa.Integer(), nullable=False),
            sa.Column('fields', sa.JSON(), nullable=False),
            sa.Column('dirty_seq', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        )

    if 'sync_conflicts' not in existing_tables:
        op.create_table(
            'sync_conflicts',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('entity_type', sa.String(), nullable=False),
            sa.Column('entity_id', sa.String(), nullable=False),
            sa.Column('fields', sa.JSON(), nullable=False),
            sa.Column('local', sa.JSON(), nullable=False),
            sa.Column('remote', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('resolved_at', sa.String(), nullable=True),
        )

    if 'sync_suppress' not in existing_tables:
        op.create_table(
            'sync_suppress',
            sa.Column('id', sa.Integer(), primary_key=True),
        )

    # SQLite triggers for local change capture (disabled when sync_suppress has any row)
    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_tasks_ins AFTER INSERT ON work_tasks
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('work_item', NEW.id, datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_tasks_upd AFTER UPDATE ON work_tasks
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('work_item', NEW.id, datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_worklogs_ins AFTER INSERT ON worklogs
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('work_item', NEW.task_id, datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_worklogs_upd AFTER UPDATE ON worklogs
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('work_item', NEW.task_id, datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_reports_ins AFTER INSERT ON daily_reports
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('daily_report', NEW.date, datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_reports_upd AFTER UPDATE ON daily_reports
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('daily_report', NEW.date, datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_settings_ins AFTER INSERT ON settings
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('setting', '*', datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)

    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_dirty_settings_upd AFTER UPDATE ON settings
    WHEN NOT EXISTS (SELECT 1 FROM sync_suppress)
    BEGIN
        INSERT INTO sync_dirty (entity_type, entity_id, changed_at, seq)
        VALUES ('setting', '*', datetime('now'), COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1)
        ON CONFLICT (entity_type, entity_id) DO UPDATE SET
            changed_at = datetime('now'),
            seq = COALESCE((SELECT MAX(seq) FROM sync_dirty), 0) + 1;
    END;
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_settings_upd")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_settings_ins")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_reports_upd")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_reports_ins")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_worklogs_upd")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_worklogs_ins")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_tasks_upd")
    op.execute("DROP TRIGGER IF EXISTS trg_dirty_tasks_ins")
    op.drop_table('sync_suppress')
    op.drop_table('sync_conflicts')
    op.drop_table('sync_ops')
    op.drop_table('sync_dirty')
    op.drop_table('sync_shadow')
    op.drop_table('sync_meta')
