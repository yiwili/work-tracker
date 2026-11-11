from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Literal
import asyncio
import logging
from contextlib import suppress

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from . import db, service, daily_analysis, model_provider, prompts, style_reference, time_allocation, matching, similarity, multiday
from . import usage, cloud_sync, history_upload
from . import desktop_config
from .cloud_client import CloudClient
from .models import ManualTemplate, MergeInput, Settings, SplitInput, TaskInput, TaskPatch, ModelKeyInput, StyleImportInput, ModelProbeInput
from .models import AllocationInput, GiteeConfig, GitIdentityQuery, GitVerifyQuery, GitScanInput, SourceCheckInput, CONVERSATION_SOURCES, TaskFromEventsInput, AnalysisImportInput, ReportTargetInput, MultiDayBindInput, MultiDayUnbindInput, CloudBindInput, CloudModeInput, CloudResolveInput, HistoryCloudUploadInput, SimilarityCheckInput, SimilarityResolveInput
from .git_tools import identity_options, global_author, verify_author, scan_repositories
from . import gitee
from . import analysis_progress
from . import gitee_enterprise
from . import gitee_sync as gitee_sync_svc
from .models import GiteeEnterpriseConfig,GiteePreviewInput,GiteePublishInput,GiteeResolveInput,GiteeDedupConfig,GiteeConfirmMatchInput


def create_app(engine=None):
    engine = engine or db.make_engine()
    usage.bind(engine)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate(engine)
        usage.backfill(engine)
        async def poll():
            while True:
                try:
                    await asyncio.to_thread(gitee.scheduled_sync,engine)
                except Exception:
                    # A transient background failure must not stop future runs.
                    pass
                await asyncio.sleep(60)
        worker=asyncio.create_task(poll())
        scheduler = cloud_sync.SyncScheduler(engine)
        scheduler.start()
        app.state.cloud_scheduler = scheduler
        try:
            yield
        finally:
            scheduler.stop()
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    app = FastAPI(title="AI Work Tracker", version="0.1.0", lifespan=lifespan)
    desktop_config.configure_desktop_cors(app)
    app.state.engine = engine
    analysis_locks = {}
    collect_states = {}
    analysis_runs = {}
    allocation_runs = {}

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
            allowed_origins = ('http://localhost:5173', 'http://127.0.0.1:5173', str(request.base_url).rstrip('/'), *desktop_config.get_allowed_origins())
            if origin and origin not in allowed_origins:
                return JSONResponse({'detail': '拒绝跨站写入请求'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site' and origin not in desktop_config.get_allowed_origins():
                return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
            if request.method in ('POST', 'PUT', 'PATCH') and request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': '请使用 application/json'}, status_code=415)
        response = await call_next(request)
        if request.url.path in ('/', '/index.html'):
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

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

    @app.get('/api/usage/stats')
    def usage_stats(period: Literal['day', 'week', 'month', 'year', 'all'] = 'day'):
        with engine.connect() as conn:
            return usage.report(conn, period, service.get_settings(conn).timezone)

    @app.put('/api/settings')
    def put_settings(payload: Settings):
        with transaction() as conn:
            return service.save_settings(conn, model_provider.normalize(payload))

    @app.post('/api/sources/check')
    def source_check(payload: SourceCheckInput):
        items = []
        detected = {}
        seen = set()
        defaults = Settings() if payload.rediscover else None

        def probe(name, raw, from_default=False):
            path = Path(raw).expanduser()
            try:
                key = (name, str(path.resolve()))
            except OSError:
                key = (name, str(path))
            if key in seen:
                return
            seen.add(key)
            try:
                exists = path.exists()
            except OSError:
                exists = False
            items.append({'source': name, 'path': str(path), 'exists': exists, 'default': from_default})
            detected[name] = detected.get(name, False) or exists

        for name, paths in payload.sources.items():
            for raw in paths:
                probe(name, raw)
            if defaults is not None:
                # 用户填写的路径优先展示；默认目录只用于补齐“新安装但未配置”的来源。
                for raw in getattr(defaults, name).paths:
                    probe(name, raw, True)
        return {'items': items, 'detected': [name for name, ok in detected.items() if ok]}

    @app.post('/api/git/scan')
    def git_scan(payload: GitScanInput):
        return scan_repositories(payload.root)

    @app.post('/api/git/identities')
    def git_identities(payload:GitIdentityQuery):
        with engine.connect() as conn:
            settings=service.get_settings(conn)
            roots=[*payload.roots,*settings.git_roots,*conn.execute(select(db.repositories.c.root)).scalars()]
            return identity_options(roots,settings)

    @app.get('/api/git/global-identity')
    def git_global_identity():
        return global_author()

    @app.post('/api/git/verify-author')
    def git_verify_author(payload:GitVerifyQuery):
        with engine.connect() as conn:
            settings=service.get_settings(conn)
            roots=[*payload.roots,*settings.git_roots,*conn.execute(select(db.repositories.c.root)).scalars()]
        if payload.author is not None:
            settings=settings.model_copy(update={'git_author':payload.author})
        return verify_author(roots,settings)

    @app.get('/api/doctor')
    def doctor():
        with engine.connect() as conn:
            config = service.get_settings(conn)
            sources = {name: getattr(config, name) for name in CONVERSATION_SOURCES}
            return {'sources': [dict(r) for r in conn.execute(select(db.sources)).mappings()],
                    'paths': [{'source': name, 'path': path, 'exists': Path(path).expanduser().exists()} for name, source in sources.items() for path in source.paths],
                    'llm_enabled': config.llm_enabled, 'timezone': config.timezone}

    @app.get('/api/themes')
    def get_themes():
        from . import themes as theme_svc
        with engine.connect() as conn:
            config = service.get_settings(conn)
            return theme_svc.list_themes(getattr(config, 'theme_id', 'apple-hig'))

    @app.put('/api/themes/active')
    def set_active_theme(payload: dict):
        from . import themes as theme_svc
        next_id = str(payload.get('theme_id') or 'apple-hig').strip()[:64]
        with transaction() as conn:
            config = service.get_settings(conn)
            catalog = theme_svc.list_themes(next_id)
            config.theme_id = catalog['active_theme']
            service.save_settings(conn, config)
            return catalog

    @app.put('/api/themes/{theme_id}')
    def put_theme(theme_id: str, payload: dict):
        from . import themes as theme_svc
        return theme_svc.update_theme(theme_id.strip()[:64], payload)

    @app.delete('/api/themes/{theme_id}')
    def remove_theme(theme_id: str):
        from . import themes as theme_svc
        with transaction() as conn:
            config = service.get_settings(conn)
            res = theme_svc.delete_theme(theme_id.strip()[:64])
            if config.theme_id == theme_id:
                config.theme_id = res['active_theme']
                service.save_settings(conn, config)
            return res

    @app.get('/api/conversations')
    def conversations(date: date, start_date: date | None = None, end_date: date | None = None):
        with engine.connect() as conn:
            binding = multiday.get_active_binding(conn, date)
            s_day = start_date or (type(date).fromisoformat(binding['start_date']) if binding else date)
            e_day = end_date or (type(date).fromisoformat(binding['end_date']) if binding else date)
            r_day = type(date).fromisoformat(binding['anchor_date']) if binding else date
            value = daily_analysis.overview(conn, s_day, service.get_settings(conn), end_day=e_day, report_day=r_day)
            value['running'] = bool(analysis_locks.get(r_day) and analysis_locks[r_day].locked())
            return value

    @app.get('/api/multiday/binding')
    def multiday_binding(date: date):
        with engine.connect() as conn:
            binding = multiday.get_active_binding(conn, date)
            return {'bound': bool(binding), 'binding': binding}

    @app.post('/api/multiday/bind')
    def multiday_bind(payload: MultiDayBindInput):
        with transaction() as conn:
            binding = multiday.bind_days(
                conn,
                payload.start_date,
                payload.end_date,
                anchor_date=payload.anchor_date,
                target_minutes=payload.target_minutes,
            )
        trigger_cloud_sync()
        return {'bound': True, 'binding': binding}

    @app.post('/api/multiday/unbind')
    def multiday_unbind(payload: MultiDayUnbindInput):
        with transaction() as conn:
            binding = multiday.unbind_days(conn, day=payload.date, binding_id=payload.binding_id)
        trigger_cloud_sync()
        return {'unbound': binding is not None, 'binding': binding}

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
    def test_model(payload:ModelProbeInput|None=None):
        with engine.connect() as conn:
            # A wizard probe may test an unsaved candidate; nothing is persisted here.
            candidate = payload.settings if payload and payload.settings else service.get_settings(conn)
            key = payload.api_key.get_secret_value() if payload and payload.api_key else None
            return model_provider.test_connection(candidate, key)

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

    @app.get('/api/manual-options')
    def manual_options(refresh: bool = False):
        ent_token_configured = bool(gitee_enterprise.token())
        with engine.connect() as conn:
            s = service.get_settings(conn)
            ent_id = s.gitee_enterprise.enterprise_id

        if (refresh or not s.project_options) and ent_id and ent_token_configured:
            try:
                gitee_enterprise.cache_programs(engine, ent_id)
                with engine.connect() as conn:
                    s = service.get_settings(conn)
            except Exception:
                if not s.project_options:
                    with engine.begin() as conn:
                        cached = conn.execute(
                            select(db.integrations.c.value).where(
                                db.integrations.c.id == f'gitee-enterprise-programs:{ent_id}'
                            )
                        ).scalar() or {}
                        items = cached.get('items', [])
                        if items:
                            names = list(dict.fromkeys(r.get('name', '') for r in items if r.get('name')))[:500]
                            if names:
                                s.project_options = names
                                service.save_settings(conn, s)
        elif not s.project_options and ent_id:
            with engine.begin() as conn:
                cached = conn.execute(
                    select(db.integrations.c.value).where(
                        db.integrations.c.id == f'gitee-enterprise-programs:{ent_id}'
                    )
                ).scalar() or {}
                items = cached.get('items', [])
                if items:
                    names = list(dict.fromkeys(r.get('name', '') for r in items if r.get('name')))[:500]
                    if names:
                        s.project_options = names
                        service.save_settings(conn, s)

        with engine.connect() as conn:
            prefix_meta = matching.get_prefix_metadata(conn, s)

        return {
            'templates': [t.model_dump(mode='json') for t in s.manual_templates],
            'projects': s.project_options,
            'enterprise_configured': ent_token_configured,
            'enterprise_id': ent_id,
            'project_prefixes': prefix_meta['project_prefixes'],
            'bracket_style': prefix_meta['bracket_style'],
            'default_prefix': prefix_meta['default_prefix'],
        }

    @app.put('/api/manual-templates')
    def put_manual_templates(payload:list[ManualTemplate]):
        if len(payload)>50:
            raise HTTPException(422,'模板最多50个')
        with transaction() as conn:
            s=service.get_settings(conn)
            s.manual_templates=payload
            service.save_settings(conn,s)
            res = [t.model_dump(mode='json') for t in s.manual_templates]
        scheduler = getattr(app.state, 'cloud_scheduler', None)
        if scheduler is not None:
            scheduler.trigger_now()
        return res

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

    @app.get('/api/gitee/enterprise/issues')
    def enterprise_issues_by_date(
        date: str = Query(default='all'),
        program_id: int | None = Query(default=None),
        state: str = Query(default='all'),
        source: str = Query(default='auto'),
    ):
        return gitee_enterprise.issues_for_date(engine, date, program_id=program_id, state=state, source=source)

    @app.post('/api/gitee/enterprise/issues/retain')
    def retain_enterprise_issues(payload: dict):
        target_date = str(payload.get('date') or 'all')
        issue_ids = payload.get('issue_ids')
        if issue_ids is not None and not isinstance(issue_ids, list):
            raise HTTPException(422, 'issue_ids 必须为列表')
        return gitee_enterprise.retain_issues_locally(engine, target_date, issue_ids=issue_ids)

    @app.get('/api/gitee/enterprise/verify')
    def verify_enterprise_day(date: date):
        return gitee_enterprise.verify_day_publish(engine, str(date))

    @app.get('/api/gitee/unfinished')
    def gitee_unfinished():
        return gitee_sync_svc.fetch_all_unfinished(engine)

    @app.get('/api/gitee/dedup/config')
    def get_dedup_config():
        with engine.connect() as conn:
            return service.get_settings(conn).gitee_dedup

    @app.put('/api/gitee/dedup/config')
    def update_dedup_config(payload: GiteeDedupConfig):
        with transaction() as conn:
            cfg = service.get_settings(conn)
            cfg.gitee_dedup = payload
            service.save_settings(conn, cfg)
            return cfg.gitee_dedup

    @app.post('/api/reports/{day}/sync-gitee')
    def sync_gitee_day(day: date):
        return gitee_sync_svc.sync_gitee_tasks(engine, str(day))

    @app.post('/api/reports/{day}/confirm-gitee-match')
    def confirm_gitee_match_day(day: date, payload: GiteeConfirmMatchInput):
        return gitee_sync_svc.apply_user_decisions(engine, str(day), [d.model_dump() for d in payload.decisions])

    @app.get('/api/reports/{day}/similarity-status')
    def get_similarity_status_day(day: date):
        with engine.connect() as conn:
            tasks = service.list_tasks(conn, day)
            return similarity.get_similarity_status(conn, day, tasks)

    @app.post('/api/reports/{day}/check-similarity')
    def check_similarity_day(day: date, payload: SimilarityCheckInput = SimilarityCheckInput()):
        with engine.connect() as conn:
            settings = service.get_settings(conn)
            res = similarity.check_day_similarity(conn, day, settings=settings, force=payload.force)
            conn.commit()
            return res

    @app.post('/api/reports/{day}/resolve-similarity')
    def resolve_similarity_day(day: date, payload: SimilarityResolveInput):
        with transaction() as conn:
            settings = service.get_settings(conn)
            res = similarity.resolve_similarity(conn, day, [r.model_dump() for r in payload.resolutions], settings=settings)
            return res

    def _canonical_day(d: date) -> date:
        with engine.connect() as conn:
            return date.fromisoformat(multiday.canonical_day(conn, d))

    @app.get('/api/allocation/progress')
    def allocation_status(date: date):
        key = _canonical_day(date)
        progress = allocation_runs.get(key)
        return progress.snapshot() if progress else {
            'state': 'idle', 'elapsed_seconds': 0, 'logs': [], 'thinking': '', 'thinking_live': False, 'model': '',
            'thinking_seconds': 0,
            'tokens': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'estimated': False}}

    @app.post('/api/reports/{day}/estimate-time')
    def estimate_time(day:date,payload:AllocationInput=AllocationInput()):
        lock_day = _canonical_day(day)
        lock=analysis_locks.setdefault(lock_day,Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409,detail='该日期的AI分析或工时估算正在进行，请等待完成')
        progress=analysis_progress.Progress()
        allocation_runs[lock_day]=progress
        for old in list(allocation_runs):
            if len(allocation_runs)>32 and old!=lock_day and allocation_runs[old].state!='running':
                del allocation_runs[old]
        progress.add('开始估算工时')
        try:
            with analysis_progress.track(progress), engine.connect() as conn:
                if not payload.force_skip_similarity:
                    sim_check = similarity.check_day_similarity(conn, day, force=False)
                    conn.commit()
                    if sim_check.get("has_duplicates"):
                        raise HTTPException(
                            status_code=409,
                            detail="检测到人工工作项与AI工作项存在高度重复，请先核对并处理相似工作项后再计算工时"
                        )
                try:
                    result=time_allocation.estimate(conn,day,payload)
                except ValueError as exc:
                    progress.add('工时分配未完成，原有记录已保留')
                    progress.finish('error')
                    logging.getLogger('awt.allocation').warning('工时分配失败 date=%s manual_count=%s reason=%s',day,len(payload.manual_items),str(exc))
                    raise
                conn.commit()
                progress.add('工时分配结果已保存')
                progress.finish('success')
                return result
        finally:
            lock.release()

    @app.post('/api/reports/{day}/apply-allocation')
    def apply_allocation(day:date):
        lock_day = _canonical_day(day)
        lock=analysis_locks.setdefault(lock_day,Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409,detail='该日期的AI分析或工时估算正在进行，请等待完成')
        try:
            with transaction() as conn:
                return time_allocation.apply(conn,day)
        finally:
            lock.release()

    @app.post('/api/collect')
    def collect(date: date, start_date: date | None = None, end_date: date | None = None):
        c_day = _canonical_day(date)
        state = collect_states[str(c_day)] = {"running": True, "source": "", "files_done": 0, "files_total": 0, "new_events": 0}
        try:
            with transaction() as conn:
                binding = multiday.get_active_binding(conn, date) if (start_date is None and end_date is None) else None
                s_day = start_date or (binding['start_date'] if binding else None)
                e_day = end_date or (binding['end_date'] if binding else None)
                return service.collect(conn, c_day, state.update, start_day=s_day, end_day=e_day)
        finally:
            state["running"] = False

    @app.get('/api/collect/status')
    def collect_status(date: date):
        c_day = _canonical_day(date)
        return collect_states.get(str(c_day), {"running": False})

    @app.get('/api/analysis/progress')
    def analysis_status(date: date):
        c_day = _canonical_day(date)
        progress = analysis_runs.get(c_day)
        return progress.snapshot() if progress else {
            'state': 'idle', 'elapsed_seconds': 0, 'logs': [], 'thinking': '', 'thinking_live': False, 'model': '',
            'thinking_seconds': 0,
            'tokens': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'estimated': False}}

    @app.post('/api/analyze')
    def analyze(date: date, start_date: date | None = None, end_date: date | None = None, target_minutes: int | None = None):
        lock_day = _canonical_day(date)
        lock = analysis_locks.setdefault(lock_day, Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='该日期正在进行 AI 分析，请等待完成后刷新；未重复发送模型请求')
        progress = analysis_progress.Progress()
        analysis_runs[lock_day] = progress
        for old in list(analysis_runs):
            if len(analysis_runs) > 32 and old != lock_day and analysis_runs[old].state != 'running':
                del analysis_runs[old]
        label = f"{start_date} 至 {end_date}" if start_date and end_date and start_date != end_date else '全天'
        progress.add(f'开始分析{label}对话')
        try:
            with analysis_progress.track(progress), engine.connect() as conn:
                result = service.analyze(conn, date, start_day=start_date, end_day=end_date, target_minutes=target_minutes)
                conn.commit()
                progress.add('分析完成，结果已保存')
                progress.finish('success')
                return result
        except model_provider.ModelError as exc:
            progress.add('分析失败，原有记录已保留；请查看页面错误提示')
            progress.finish('error')
            logging.getLogger('awt.analysis').warning('全天分析失败 date=%s reason=%s',date,str(exc))
            raise
        except Exception:
            progress.add('分析未完成，原有记录已保留')
            progress.finish('error')
            raise
        finally:
            lock.release()

    @app.post('/api/analysis/import')
    def import_analysis(payload: AnalysisImportInput):
        lock_day = _canonical_day(payload.date)
        lock = analysis_locks.setdefault(lock_day, Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='该日期正在进行 AI 分析或导入，请稍后重试')
        try:
            with transaction() as conn:
                return daily_analysis.import_pending(conn, payload.date, payload.items)
        finally:
            lock.release()

    @app.post('/api/tasks/from-events')
    def tasks_from_events(payload: TaskFromEventsInput):
        with transaction() as conn:
            binding = multiday.get_active_binding(conn, payload.date) if (payload.start_date is None and payload.end_date is None) else None
            s_day = payload.start_date or (binding['start_date'] if binding else None)
            e_day = payload.end_date or (binding['end_date'] if binding else None)
            return service.tasks_from_events(conn, payload.date, payload.event_ids, start_day=s_day, end_day=e_day)

    @app.get('/api/events')
    def events(date: date, start_date: date | None = None, end_date: date | None = None):
        with engine.connect() as conn:
            binding = multiday.get_active_binding(conn, date) if (start_date is None and end_date is None) else None
            s_day = start_date or (type(date).fromisoformat(binding['start_date']) if binding else date)
            e_day = end_date or (type(date).fromisoformat(binding['end_date']) if binding else date)
            return service.visible_events(conn, s_day, service.get_settings(conn), end_day=e_day)

    @app.get('/api/repositories')
    def repositories():
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(db.repositories)).mappings()]

    def trigger_cloud_sync():
        scheduler = getattr(app.state, 'cloud_scheduler', None)
        if scheduler is not None:
            scheduler.trigger_now()

    @app.get('/api/todos')
    def todos():
        with engine.connect() as conn:
            return service.list_todos(conn)

    @app.get('/api/tasks')
    def tasks(date: date):
        with engine.connect() as conn:
            return service.list_tasks(conn, date)

    @app.post('/api/tasks/suggest-prefix')
    def suggest_prefix(payload: dict):
        title = str(payload.get('title') or '').strip()
        project = str(payload.get('project') or '').strip()
        with engine.connect() as conn:
            s = service.get_settings(conn)
            has_pfx = matching.has_bracket_prefix(title)
            formatted = matching.format_task_title_with_prefix(
                conn, title, project, settings=s, allow_ai=True
            )
            pfx, _ = matching.extract_prefix_and_content({'title': formatted})
            return {
                'has_prefix': has_pfx,
                'prefix': pfx,
                'formatted_title': formatted,
            }

    @app.post('/api/tasks')
    def add_task(payload: TaskInput, auto_prefix: bool = Query(False)):
        with transaction() as conn:
            if auto_prefix and not matching.has_bracket_prefix(payload.title):
                s = service.get_settings(conn)
                payload.title = matching.format_task_title_with_prefix(
                    conn, payload.title, payload.project, settings=s, allow_ai=False
                )
            res = {'id': service.save_task(conn, payload)}
        trigger_cloud_sync()
        return res

    @app.patch('/api/tasks/{task_id}')
    def edit_task(task_id: str, payload: TaskPatch):
        with transaction() as conn:
            existing = service.get_task(conn, task_id)
            existing['confirmed_minutes'] = conn.execute(select(db.worklogs.c.confirmed_minutes).where(db.worklogs.c.task_id == task_id)).scalar_one()
            existing['suggested_minutes'] = conn.execute(select(db.worklogs.c.suggested_minutes).where(db.worklogs.c.task_id == task_id)).scalar_one_or_none()
            existing['time_locked'] = conn.execute(select(db.worklogs.c.time_locked).where(db.worklogs.c.task_id==task_id)).scalar_one()
            merged = {k: existing.get(k) for k in TaskInput.model_fields}
            if merged.get('is_todo') is None:
                merged['is_todo'] = True
            merged.update(payload.model_dump(exclude_unset=True))
            if 'confirmed_minutes' in payload.model_fields_set and 'time_locked' not in payload.model_fields_set:
                merged['time_locked']=bool(merged['confirmed_minutes'])
            res = {'id': service.save_task(conn, TaskInput.model_validate(merged), task_id)}
        trigger_cloud_sync()
        return res

    @app.delete('/api/tasks/{task_id}')
    def delete_task(task_id: str):
        with transaction() as conn:
            service.delete_task(conn, task_id)
        trigger_cloud_sync()
        return {'ok': True}

    @app.post('/api/tasks/merge')
    def merge(payload: MergeInput):
        with transaction() as conn:
            return {'id': service.merge_tasks(conn, payload.ids)}

    @app.post('/api/tasks/split')
    def split(payload: SplitInput):
        with transaction() as conn:
            return {'ids': service.split_task(conn, payload.id, payload.parts)}

    @app.put('/api/reports/{day}/target')
    def set_target(day: date, payload: ReportTargetInput):
        with engine.connect() as conn:
            res = service.set_report_target(conn, day, payload.target_minutes)
            conn.commit()
            return res

    @app.get('/api/reports/{day}')
    def report(day: date):
        with engine.connect() as conn:
            value = service.report(conn, day)
            c_day = date.fromisoformat(value.get('canonical_date') or str(day))
            analysis_run = analysis_runs.get(c_day)
            value['conversations']['running'] = bool(analysis_run and analysis_run.state == 'running')
            value['allocation_running'] = bool(allocation_runs.get(c_day) and allocation_runs[c_day].state == 'running')
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

    @app.get('/api/cloud/status')
    def cloud_status():
        with engine.connect() as conn:
            bound = cloud_sync.get_meta(conn, 'bound', False)
            server_url = cloud_sync.get_meta(conn, 'server_url', '')
            account = cloud_sync.get_meta(conn, 'account', {})
            device_id = cloud_sync.get_meta(conn, 'device_id', '')
            mode = cloud_sync.get_meta(conn, 'mode', {'mode': 'auto', 'interval_minutes': 5, 'times': []})
            last_sync_at = cloud_sync.get_meta(conn, 'last_sync_at', None)
            last_error = cloud_sync.get_meta(conn, 'last_error', '')
            unified_publish_enabled = cloud_sync.get_meta(conn, 'unified_publish_enabled', False)
            dirty_cnt = len(list(conn.execute(select(db.sync_dirty.c.entity_id)).scalars()))
            ops_cnt = len(list(conn.execute(select(db.sync_ops.c.op_id)).scalars()))
            conflicts_cnt = len(cloud_sync.list_conflicts(conn))

            return {
                'bound': bound,
                'server_url': server_url,
                'account': account,
                'device_id': device_id,
                'mode': mode,
                'last_sync_at': last_sync_at,
                'last_error': last_error,
                'pending': dirty_cnt,
                'in_flight': ops_cnt,
                'conflicts': conflicts_cnt,
                'unified_publish_enabled': unified_publish_enabled,
            }

    @app.post('/api/cloud/bind')
    def cloud_bind(payload: CloudBindInput):
        return cloud_sync.bind(engine, payload.server_url, payload.email, payload.password, payload.device_name)

    @app.post('/api/cloud/unbind')
    def cloud_unbind():
        cloud_sync.unbind(engine)
        return {'status': 'unbound'}

    @app.get('/api/cloud/migration/preview')
    def cloud_migration_preview():
        return cloud_sync.migration_preview(engine)

    @app.post('/api/cloud/migrate')
    def cloud_migrate():
        return cloud_sync.migrate(engine)

    @app.post('/api/cloud/sync')
    def cloud_sync_now():
        with engine.connect() as conn:
            server_url = cloud_sync.get_meta(conn, 'server_url', '')
        token = gitee.token(service='cloud-session')
        if not server_url or not token:
            raise HTTPException(status_code=400, detail="未绑定云端账号")
        client = CloudClient(base_url=server_url, token=token)
        return cloud_sync.sync_once(engine, client)

    @app.put('/api/cloud/mode')
    def cloud_set_mode(payload: CloudModeInput):
        with engine.begin() as conn:
            cloud_sync.set_meta(conn, 'mode', payload.model_dump(mode='json'))
        return payload.model_dump(mode='json')

    @app.get('/api/cloud/conflicts')
    def cloud_list_conflicts():
        with engine.connect() as conn:
            return {'conflicts': cloud_sync.list_conflicts(conn)}

    @app.post('/api/cloud/conflicts/{id}/resolve')
    def cloud_resolve_conflict(id: str, payload: CloudResolveInput):
        return cloud_sync.resolve_conflict(engine, id, payload.choices)

    @app.get('/api/cloud/devices')
    def cloud_list_devices():
        with engine.connect() as conn:
            server_url = cloud_sync.get_meta(conn, 'server_url', '')
        token = gitee.token(service='cloud-session')
        if not server_url or not token:
            raise HTTPException(status_code=400, detail="未绑定云端账号")
        client = CloudClient(base_url=server_url, token=token)
        return {'devices': client.list_devices()}

    @app.delete('/api/cloud/devices/{device_id}')
    def cloud_revoke_device(device_id: str):
        with engine.connect() as conn:
            server_url = cloud_sync.get_meta(conn, 'server_url', '')
        token = gitee.token(service='cloud-session')
        if not server_url or not token:
            raise HTTPException(status_code=400, detail="未绑定云端账号")
        client = CloudClient(base_url=server_url, token=token)
        client.revoke_device(device_id)
        return Response(status_code=204)

    def _bound_cloud_client(optional: bool = False) -> CloudClient | None:
        with engine.connect() as conn:
            server_url = cloud_sync.get_meta(conn, 'server_url', '')
        token = gitee.token(service='cloud-session')
        if not server_url or not token:
            if optional:
                return None
            raise HTTPException(status_code=400, detail="未绑定云端账号")
        return CloudClient(base_url=server_url, token=token)

    @app.get('/api/history/cloud/preview')
    def history_cloud_preview():
        client = _bound_cloud_client(optional=True)
        return history_upload.history_preview(engine, client)

    @app.post('/api/history/cloud/upload')
    def history_cloud_upload(payload: HistoryCloudUploadInput = None):
        if payload is None:
            payload = HistoryCloudUploadInput()
        client = _bound_cloud_client()
        try:
            if payload.all_batches:
                return history_upload.upload_all_history(
                    engine,
                    client,
                    batch_size=payload.batch_size,
                    force_restart=payload.force_restart,
                )
            return history_upload.upload_history_batch(
                engine,
                client,
                batch_size=payload.batch_size,
                force_restart=payload.force_restart,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"云端上传失败: {exc}")

    @app.get('/api/history/cloud')
    def history_cloud_list(
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=100),
        date_from: str = Query(''),
        date_to: str = Query(''),
        has_date: bool | None = Query(None),
        project: str = Query(''),
        source: str = Query(''),
        q: str = Query(''),
    ):
        client = _bound_cloud_client()
        try:
            return client.list_cloud_history(
                page=page,
                limit=limit,
                date_from=date_from,
                date_to=date_to,
                has_date=has_date,
                project=project,
                source=source,
                q=q,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"云端查询失败: {exc}")

    @app.get('/api/history/cloud/summary')
    def history_cloud_summary():
        client = _bound_cloud_client()
        try:
            return client.cloud_history_summary()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"云端查询统计失败: {exc}")

    dist = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
    if dist.is_dir():
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app
