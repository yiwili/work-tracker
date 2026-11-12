from datetime import date
import json
import os
import sys
import threading
import time

import typer
from . import db, service, daily_analysis, time_allocation

for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8')

cli = typer.Typer(help="本地 AI 工作记录与日报管理")


def run(action, day):
    engine = db.make_engine()
    db.migrate(engine)
    with engine.connect() as conn:
        if action not in (service.analyze,time_allocation.estimate,daily_analysis.overview):
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        result = action(conn, date.fromisoformat(day))
        conn.commit()
        return result


@cli.command()
def doctor():
    import shutil
    engine = db.make_engine()
    db.migrate(engine)
    with engine.connect() as conn:
        settings = service.get_settings(conn)
    typer.echo(json.dumps({'database': str(engine.url), 'git': shutil.which('git'), 'settings': settings.model_dump(mode='json')}, ensure_ascii=False, indent=2))


@cli.command()
def collect(date: str = typer.Option(...)):
    typer.echo(json.dumps(run(service.collect, date), ensure_ascii=False, indent=2))


@cli.command()
def analyze(date: str = typer.Option(...)):
    typer.echo(json.dumps(run(service.analyze, date), ensure_ascii=False, indent=2))


@cli.command()
def conversations(date:str=typer.Option(...)):
    typer.echo(json.dumps(run(lambda conn,day:daily_analysis.overview(conn,day,service.get_settings(conn)),date),ensure_ascii=False,indent=2))


@cli.command()
def estimate(date:str=typer.Option(...)):
    typer.echo(json.dumps(run(time_allocation.estimate,date),ensure_ascii=False,indent=2))


@cli.command()
def report(date: str = typer.Option(...), format: str = 'markdown'):
    if format != 'markdown':
        raise typer.BadParameter('V1 仅支持 markdown')
    typer.echo(run(service.export_markdown, date))


def _prepare_sidecar_stdio():
    """Redirect None or requested stdio in frozen --noconsole binaries to a bounded log file."""
    force_log = os.environ.get('AWT_LOG_TO_FILE', '').strip().lower() in ('1', 'true', 'yes')
    if not force_log and sys.stdout is not None and sys.stderr is not None:
        return None
    opened = None
    try:
        log_dir = db.data_dir() / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / 'sidecar.log'
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            backup = log_dir / 'sidecar.log.1'
            backup.unlink(missing_ok=True)
            log_path.replace(backup)
        opened = open(log_path, 'a', encoding='utf-8', buffering=1)
    except OSError:
        opened = open(os.devnull, 'w', encoding='utf-8')
    if force_log or sys.stdout is None:
        sys.stdout = opened
    if force_log or sys.stderr is None:
        sys.stderr = opened
    return opened


def _wait_for_stdin_eof():
    if sys.stdin is None:
        return
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        try:
            while sys.stdin.read(1):
                pass
        except (OSError, ValueError):
            pass
        return
    while True:
        try:
            chunk = os.read(fd, 1024)
            if not chunk:
                return
        except OSError:
            return


def _start_stdin_watchdog(server, done_event: threading.Event, grace_seconds: float = 5.0):
    def _watch():
        _wait_for_stdin_eof()
        server.should_exit = True
        if not done_event.wait(grace_seconds):
            os._exit(0)

    thread = threading.Thread(target=_watch, daemon=True, name='awt-stdin-watchdog')
    thread.start()
    return thread


@cli.command()
def serve(
    port: int = 8765,
    host: str = '127.0.0.1',
    watch_stdin: bool = typer.Option(False, '--watch-stdin', help='在父进程关闭标准输入管道时自动退出服务'),
):
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise typer.BadParameter('仅允许绑定本机地址：127.0.0.1、localhost 或 ::1')
    import uvicorn
    from .app import create_app

    log_stream = _prepare_sidecar_stdio()
    done_event = threading.Event()
    use_colors = bool(sys.stderr is not None and hasattr(sys.stderr, 'isatty') and sys.stderr.isatty())
    config = uvicorn.Config(create_app(), host=host, port=port, use_colors=use_colors)
    server = uvicorn.Server(config)
    if watch_stdin and sys.stdin is not None:
        _start_stdin_watchdog(server, done_event)
    try:
        server.run()
    finally:
        done_event.set()
        if log_stream is not None:
            try:
                log_stream.flush()
                log_stream.close()
            except OSError:
                pass


@cli.command()
def terminal(work_date: str | None = typer.Option(None,'--date'), url: str = 'http://127.0.0.1:8765', no_start: bool = typer.Option(False,'--no-start')):
    """交互式终端版，无需浏览器；可自动启动本机后台。"""
    from .terminal import Terminal
    from .terminal_client import LocalApi,Backend
    api=None
    try:
        if work_date:date.fromisoformat(work_date)
        api=LocalApi(url)
        with Backend(api,auto_start=not no_start):
            Terminal(api,day=work_date).run()
    except (EOFError,KeyboardInterrupt):
        typer.echo('\n已退出终端版。')
    except (ValueError,OSError) as exc:
        typer.echo('终端版无法完成操作：'+str(exc),err=True)
        raise typer.Exit(1)
    finally:
        if api:api.close()


if __name__ == '__main__':
    cli()
