"""Local API transport and optional terminal-owned backend; no browser required."""
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse
import httpx
from . import db


class ApiError(ValueError):
    pass


class LocalApi:
    def __init__(self,url='http://127.0.0.1:8765',client=None):
        parsed=urlparse(url)
        if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1') or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValueError('终端版只连接本机 HTTP 服务，例如 http://127.0.0.1:8765')
        self.url=url.rstrip('/');self.port=parsed.port or 80
        if parsed.port is not None and parsed.port<1:
            raise ValueError('本机端口需为1至65535')
        self.client=client or httpx.Client(base_url=self.url,timeout=httpx.Timeout(360,connect=5),trust_env=False)
        self.owns_client=client is None

    def request(self,path,method='GET',body=None):
        try:
            options={'json':body} if body is not None else {}
            if path=='/health':options['timeout']=2
            response=self.client.request(method,'/api'+path,**options)
        except httpx.HTTPError:
            raise ApiError('本地服务连接失败或等待超时；可重试读取结果，不要重复添加人工事项') from None
        try:
            value=response.json()
        except ValueError:
            raise ApiError(f'本地服务返回了无法读取的响应（HTTP {response.status_code}）') from None
        if not response.is_success:
            detail=value.get('detail','请求失败') if isinstance(value,dict) else '请求失败'
            if isinstance(detail,list):
                # Validation errors may include input values: never echo pasted Tokens.
                detail='；'.join('.'.join(str(v) for v in e.get('loc',[]))+': '+str(e.get('msg','参数无效')) for e in detail if isinstance(e,dict))
            raise ApiError(str(detail))
        return value

    def close(self):
        if self.owns_client:self.client.close()

    def markdown(self,path):
        try:
            response=self.client.get('/api'+path)
        except httpx.HTTPError:
            raise ApiError('Markdown导出连接失败，请稍后重试') from None
        if not response.is_success:
            raise ApiError(f'Markdown导出失败（HTTP {response.status_code}）')
        return response.text


class Backend:
    def __init__(self,api,auto_start=True):
        self.api=api;self.auto_start=auto_start;self.process=None;self.output=None

    def __enter__(self):
        try:
            health=self.api.request('/health')
        except ApiError:
            health=None
        if health:
            if not isinstance(health,dict) or health.get('application')!='ai-work-tracker':
                raise ApiError('此端口不是Work Tracker服务，请指定其他本机端口')
            return self.api
        if not self.auto_start:
            raise ApiError('本地服务未启动；请先运行 awt serve，或去掉 --no-start')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',self.api.port))==0:
                raise ApiError('端口已被其他程序占用，请通过 --url 指定其他本机端口')
        folder=db.data_dir();folder.mkdir(parents=True,exist_ok=True)
        self.output=(folder/'terminal-server.log').open('ab')
        try:
            executable=sys.executable;environment=os.environ.copy()
            # The Windows venv redirector otherwise spawns a separate child that
            # survives terminating its parent. Launch the same runtime directly,
            # retaining CPython's venv launcher context so dependencies stay local.
            if os.name=='nt' and getattr(sys,'_base_executable',sys.executable)!=sys.executable:
                executable=sys._base_executable
                environment['__PYVENV_LAUNCHER__']=sys.executable
            self.process=subprocess.Popen([executable,'-X','utf8','-m','awt.cli','serve','--port',str(self.api.port)],
                stdout=self.output,stderr=self.output,stdin=subprocess.DEVNULL,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if self.process.poll() is not None:
                    raise ApiError('终端后台启动失败，请查看数据目录中的 terminal-server.log')
                try:
                    health=self.api.request('/health')
                    if isinstance(health,dict) and health.get('application')=='ai-work-tracker':return self.api
                except ApiError:
                    pass
                time.sleep(.25)
            raise ApiError('等待终端后台启动超时，请查看 terminal-server.log')
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def __exit__(self,*exc):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=5)
        if self.output:self.output.close()
        self.api.close()
