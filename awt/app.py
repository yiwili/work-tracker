from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from threading import Lock
import asyncio
import logging
from contextlib import suppress

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from . import db, service, daily_analysis, model_provider, prompts, style_reference, time_allocation
from .models import MergeInput, Settings, SplitInput, TaskInput, TaskPatch, ModelKeyInput, StyleImportInput
from .models import AllocationInput, GiteeConfig
from . import gitee
from . import gitee_enterprise
from .models import GiteeEnterpriseConfig,GiteePreviewInput,GiteePublishInput,GiteeResolveInput


def create_app(engine=None):
    engine = engine or db.make_engine()

    @asynccontextmanager
    async def lifespan(app):
        db.migrate(engine)
        async def poll():
            while True:
                try:
                    await asyncio.to_thread(gitee.scheduled_sync,engine)
                except Exception:
                    # A transient background failure must not stop future runs.
                    pass
                await asyncio.sleep(60)
        worker=asyncio.create_task(poll())
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    app = FastAPI(title="AI Work Tracker", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    analysis_locks = {}

    @contextmanager
    def transaction():
        with engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        if host not in ('localhost', '127.0.0.1', '::1', 'testserver'):
            return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            allowed_origins = ('http://localhost:5173', 'http://127.0.0.1:5173', str(request.base_url).rstrip('/'))
            if origin and origin not in allowed_origins:
                return JSONResponse({'detail': '拒绝跨站写入请求'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
            if request.method in ('POST', 'PUT', 'PATCH') and request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': '请使用 application/json'}, status_code=415)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(LookupError)
    async def missing(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(OperationalError)
    async def database_busy(request, exc):
        if 'locked' in str(exc).lower():
            return JSONResponse({'detail': '另一项保存操作正在进行，请稍后重试；记录未被删除'}, status_code=409)
        return JSONResponse({'detail': '本地数据库操作失败，请检查服务日志'}, status_code=500)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'application': 'ai-work-tracker', 'local_only': True}

    @app.get('/api/settings')
    def settings():
        with engine.connect() as conn:
            return service.get_settings(conn)

    @app.put('/api/settings')
    def put_settings(payload: Settings):
        with transaction() as conn:
            return service.save_settings(conn, payload)

    @app.get('/api/doctor')
    def doctor():
        with engine.connect() as conn:
            config = service.get_settings(conn)
            return {'sources': [dict(r) for r in conn.execute(select(db.sources)).mappings()],
                    'paths': [{'source': name, 'path': path, 'exists': Path(path).expanduser().exists()} for name, source in [('codex', config.codex), ('claude_code', config.claude_code)] for path in source.paths],
                    'llm_enabled': config.llm_enabled, 'timezone': config.timezone}

    @app.get('/api/conversations')
    def conversations(date: date):
        with engine.connect() as conn:
            value = daily_analysis.overview(conn, date, service.get_settings(conn))
            value['running'] = bool(analysis_locks.get(date) and analysis_locks[date].locked())
            return value

    @app.get('/api/model/presets')
    def model_presets():
        return model_provider.PRESETS

    @app.get('/api/model/status')
    def model_status():
        with engine.connect() as conn:
            return model_provider.status(service.get_settings(conn))

    @app.post('/api/model/key')
    def model_key(payload: ModelKeyInput):
        with transaction() as conn:
            settings = service.get_settings(conn)
            model_provider.save_key(settings, payload.api_key.get_secret_value())
            return model_provider.status(settings)

    @app.delete('/api/model/key')
    def delete_model_key():
        with transaction() as conn:
            settings = service.get_settings(conn)
            model_provider.remove_key(settings)
            return model_provider.status(settings)

    @app.post('/api/model/test')
    def test_model():
        with engine.connect() as conn:
            return model_provider.test_connection(service.get_settings(conn))

    @app.get('/api/agents')
    def agents():
        with engine.connect() as conn:
            return prompts.catalog(service.get_settings(conn))

    @app.post('/api/style/import')
    def import_style(payload:StyleImportInput):
        if not gitee.lock.acquire(blocking=False):
            raise HTTPException(status_code=409,detail='历史导入或Gitee同步正在进行')
        try:
            rows=style_reference.read_rows(payload)
            with transaction() as conn:
                result=style_reference.ingest(conn,rows,payload.since,label=payload.filename or payload.path,include_git=payload.include_git)
            with engine.connect() as conn:
                result['style']=style_reference.initial_template(conn)
                conn.commit()
            return result
        finally:
            gitee.lock.release()

    @app.get('/api/history')
    def history(offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100)):
        with engine.connect() as conn:
            return style_reference.listing(conn,offset,limit)

    @app.post('/api/style/generate')
    def generate_style():
        if not gitee.lock.acquire(blocking=False):
            raise HTTPException(status_code=409,detail='历史导入或风格生成正在进行')
        try:
            with engine.connect() as conn:
                result=style_reference.generate_template(conn,force=True)
                conn.commit()
                return result
        finally:
            gitee.lock.release()

    @app.get('/api/gitee/status')
    def gitee_status():
        with engine.connect() as conn:
            return gitee.status(conn)

    @app.put('/api/gitee/config')
    def gitee_config(payload:GiteeConfig):
        with transaction() as conn:
            settings=service.get_settings(conn)
            settings.gitee=payload
            service.save_settings(conn,settings)
            return gitee.status(conn)

    @app.post('/api/gitee/token')
    def gitee_token(payload:ModelKeyInput):
        gitee.save_token(payload.api_key.get_secret_value())
        return {'configured':True}

    @app.delete('/api/gitee/token')
    def delete_gitee_token():
        (db.data_dir()/'gitee-credential.json').unlink(missing_ok=True)
        return {'configured':bool(gitee.token())}

    @app.post('/api/gitee/sync')
    def gitee_sync():
        return gitee.sync(engine)

    @app.get('/api/gitee/enterprise/status')
    def enterprise_status():
        with engine.connect() as conn:
            return gitee_enterprise.status(conn)

    @app.put('/api/gitee/enterprise/config')
    def enterprise_config(payload:GiteeEnterpriseConfig):
        with transaction() as conn:
            config=service.get_settings(conn)
            config.gitee_enterprise=payload
            service.save_settings(conn,config)
            return gitee_enterprise.status(conn)

    @app.post('/api/gitee/enterprise/token')
    def enterprise_token(payload:ModelKeyInput):
        gitee.save_token(payload.api_key.get_secret_value(),'gitee-enterprise')
        return {'configured':True}

    @app.delete('/api/gitee/enterprise/token')
    def delete_enterprise_token():
        (db.data_dir()/'gitee-enterprise-credential.json').unlink(missing_ok=True)
        return {'configured':bool(gitee_enterprise.token())}

    @app.get('/api/gitee/enterprise/enterprises')
    def enterprises():
        return gitee_enterprise.enterprises()

    @app.get('/api/gitee/enterprise/programs')
    def enterprise_programs(enterprise_id:int=Query(...,ge=1)):
        return gitee_enterprise.cache_programs(engine,enterprise_id)

    @app.get('/api/gitee/enterprise/issue-types')
    def enterprise_issue_types(enterprise_id:int=Query(...,ge=1),program_id:int=Query(...,ge=1)):
        return gitee_enterprise.issue_types(enterprise_id,program_id)

    @app.post('/api/gitee/enterprise/preview')
    def preview_enterprise(payload:GiteePreviewInput):
        return gitee_enterprise.preview(engine,payload)

    @app.post('/api/gitee/enterprise/publish')
    def publish_enterprise(payload:GiteePublishInput):
        return gitee_enterprise.publish(engine,payload)

    @app.post('/api/gitee/enterprise/resolve')
    def resolve_enterprise(payload:GiteeResolveInput):
        return gitee_enterprise.resolve(engine,payload)

    @app.post('/api/reports/{day}/estimate-time')
    def estimate_time(day:date,payload:AllocationInput=AllocationInput()):
        lock=analysis_locks.setdefault(day,Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409,detail='该日期的AI分析或工时估算正在进行，请等待完成')
        try:
            with engine.connect() as conn:
                try:
                    result=time_allocation.estimate(conn,day,payload)
                except ValueError as exc:
                    logging.getLogger('awt.allocation').warning('工时分配失败 date=%s manual_count=%s reason=%s',day,len(payload.manual_items),str(exc))
                    raise
                conn.commit()
                return result
        finally:
            lock.release()

    @app.post('/api/collect')
    def collect(date: date):
        with transaction() as conn:
            return service.collect(conn, date)

    @app.post('/api/analyze')
    def analyze(date: date):
        lock = analysis_locks.setdefault(date, Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='该日期正在进行 AI 分析，请等待完成后刷新；未重复发送模型请求')
        try:
            with engine.connect() as conn:
                result = service.analyze(conn, date)
                conn.commit()
                return result
        except model_provider.ModelError as exc:
            logging.getLogger('awt.analysis').warning('全天分析失败 date=%s reason=%s',date,str(exc))
            raise
        finally:
            lock.release()

    @app.get('/api/events')
    def events(date: date):
        with engine.connect() as conn:
            return [dict(e) for e in conn.execute(service.event_query(date, service.get_settings(conn))).mappings()]

    @app.get('/api/repositories')
    def repositories():
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(db.repositories)).mappings()]

    @app.get('/api/tasks')
    def tasks(date: date):
        with engine.connect() as conn:
            return service.list_tasks(conn, date)

    @app.post('/api/tasks')
    def add_task(payload: TaskInput):
        with transaction() as conn:
            return {'id': service.save_task(conn, payload)}

    @app.patch('/api/tasks/{task_id}')
    def edit_task(task_id: str, payload: TaskPatch):
        with transaction() as conn:
            existing = service.get_task(conn, task_id)
            existing['confirmed_minutes'] = conn.execute(select(db.worklogs.c.confirmed_minutes).where(db.worklogs.c.task_id == task_id)).scalar_one()
            existing['time_locked'] = conn.execute(select(db.worklogs.c.time_locked).where(db.worklogs.c.task_id==task_id)).scalar_one()
            merged = {k: existing[k] for k in TaskInput.model_fields}
            merged.update(payload.model_dump(exclude_unset=True))
            if 'confirmed_minutes' in payload.model_fields_set and 'time_locked' not in payload.model_fields_set:
                merged['time_locked']=bool(merged['confirmed_minutes'])
            return {'id': service.save_task(conn, TaskInput.model_validate(merged), task_id)}

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: str):
        with transaction() as conn:
            service.delete_task(conn, task_id)
            return {'ok': True}

    @app.post('/api/tasks/merge')
    def merge(payload: MergeInput):
        with transaction() as conn:
            return {'id': service.merge_tasks(conn, payload.ids)}

    @app.post('/api/tasks/split')
    def split(payload: SplitInput):
        with transaction() as conn:
            return {'ids': service.split_task(conn, payload.id, payload.parts)}

    @app.get('/api/reports/{day}')
    def report(day: date):
        with engine.connect() as conn:
            value = service.report(conn, day)
            value['conversations']['running'] = bool(analysis_locks.get(day) and analysis_locks[day].locked())
            return value

    @app.post('/api/reports/{day}/confirm')
    def confirm(day: date):
        with transaction() as conn:
            return service.confirm(conn, day)

    @app.get('/api/reports/{day}/export.md')
    def export(day: date):
        with transaction() as conn:
            return Response(service.export_markdown(conn, day), media_type='text/markdown; charset=utf-8',
                            headers={'Content-Disposition': f'attachment; filename="worklog-{day}.md"'})

    dist = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app
