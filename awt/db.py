import os
from pathlib import Path

from sqlalchemy import (JSON, Boolean, Column, ForeignKey, Integer, MetaData,
                        String, Table, Text, create_engine, event)

metadata = MetaData()
sources = Table("sources", metadata, Column("name", String, primary_key=True), Column("config", JSON), Column("health", JSON))
cursors = Table("collection_cursors", metadata, Column("source_key", String, primary_key=True), Column("file_id", String), Column("offset", Integer), Column("prefix_hash", String), Column("last_seen_at", String))
sessions = Table("ai_sessions", metadata, Column("id", String, primary_key=True), Column("source", String), Column("cwd", Text), Column("last_seen_at", String))
events = Table("work_events", metadata, Column("id", String, primary_key=True), Column("source", String, index=True), Column("session_id", String, index=True), Column("occurred_at", String, index=True), Column("kind", String), Column("summary", Text), Column("cwd", Text), Column("project", Text), Column("repo_id", String), Column("raw_ref", Text), Column("metadata", JSON))
repositories = Table("repositories", metadata, Column("id", String, primary_key=True), Column("root", Text), Column("remote", Text), Column("branch", String), Column("project", Text), Column("current_changes", Text), Column("observed_at", String))
commits = Table("git_commits", metadata, Column("id", String, primary_key=True), Column("repo_id", String), Column("sha", String), Column("authored_at", String), Column("committed_at", String), Column("subject", Text), Column("changes", Text))
tasks = Table("work_tasks", metadata, Column("id", String, primary_key=True), Column("date", String, index=True), Column("title", Text), Column("details", Text), Column("project", Text), Column("category", String), Column("status", String), Column("origin", String), Column("confidence", String), Column("evidence_level", String), Column("fingerprint", String, unique=True), Column("edited", Boolean, default=False), Column("deleted", Boolean, default=False), Column("started_at", String), Column("ended_at", String))
evidence = Table("task_evidence", metadata, Column("task_id", String, ForeignKey("work_tasks.id"), primary_key=True), Column("event_id", String, ForeignKey("work_events.id"), primary_key=True))
worklogs = Table("worklogs", metadata, Column("task_id", String, ForeignKey("work_tasks.id"), primary_key=True), Column("suggested_minutes", Integer), Column("confirmed_minutes", Integer, nullable=False, default=0), Column("is_confirmed", Boolean, default=False))
worklogs.append_column(Column('time_locked', Boolean, nullable=False, server_default='0'))
worklogs.append_column(Column('time_source', String, nullable=False, server_default='manual'))
worklogs.append_column(Column('time_reason', Text, nullable=False, server_default=''))
reports = Table("daily_reports", metadata, Column("date", String, primary_key=True), Column("target_minutes", Integer), Column("confirmed", Boolean, default=False), Column("confirmed_at", String), Column("exported_at", String))
settings_table = Table("settings", metadata, Column("id", Integer, primary_key=True), Column("value", JSON))
analyses = Table('daily_analyses', metadata, Column('date', String, primary_key=True), Column('snapshot', JSON))
allocations = Table('time_allocations', metadata, Column('date', String, primary_key=True), Column('snapshot', JSON))
history = Table('history_work_items', metadata, Column('id', String, primary_key=True), Column('title', Text), Column('details', Text), Column('project', Text), Column('work_date', String, index=True), Column('source', String), Column('source_id', String), Column('url', Text), Column('imported_at', String))
integrations = Table('integration_state', metadata, Column('id', String, primary_key=True), Column('value', JSON))
allocation_requests = Table('allocation_requests', metadata, Column('id', String, primary_key=True), Column('payload_hash', String), Column('result', JSON))
gitee_batches = Table('gitee_export_batches', metadata, Column('id', String, primary_key=True), Column('snapshot', JSON))
gitee_links = Table('gitee_work_item_links', metadata, Column('id', String, primary_key=True), Column('enterprise_id', Integer), Column('task_id', String, index=True), Column('program_id', Integer), Column('status', String), Column('issue_id', String), Column('url', Text), Column('error', Text), Column('updated_at', String))


def data_dir():
    return Path(os.environ.get("AWT_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def make_engine(path=None):
    path = Path(path) if path else data_dir() / "work.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + path.as_posix(), connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def setup(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    return engine


def migrate(engine):
    from alembic import command
    from alembic.config import Config
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
