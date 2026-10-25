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
    typer.echo(json.dumps({'database': str(engine.url), 'git': shutil.which('git'), 'settings': settings.model_dump()}, ensure_ascii=False, indent=2))


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


if __name__ == '__main__':
    cli()
