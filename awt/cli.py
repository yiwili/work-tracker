from datetime import date
import json
import sys

import typer
from . import db, service, daily_analysis, time_allocation

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
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


@cli.command()
def serve(port: int = 8765):
    import uvicorn
    uvicorn.run('awt.app:create_app', factory=True, host='127.0.0.1', port=port)


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
