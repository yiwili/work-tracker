"""Relay Gitee Enterprise calls through the official mcp-gitee-ent server.

Gitee rejects MCP enterprise tokens sent by third-party clients with 401
"Only For MCP Gitee Enterprise Application", so requests run through the
official server over stdio. The token only travels in the child environment,
never on a command line.
"""
import atexit
import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from collections import deque

API = 'https://api.gitee.com/enterprises'
PACKAGE = '@gitee/mcp-gitee-ent@latest'
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


class MCPError(ValueError):
    pass


class _Server:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.token = ''
        self.messages = None
        self.errors = None
        self.next_id = 1

    def command(self):
        custom = os.environ.get('AWT_GITEE_MCP_COMMAND', '').strip()
        if custom:
            parts = shlex.split(custom, posix=False)
            return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in '"\'' else p for p in parts]
        npx = shutil.which('npx')
        if not npx:
            raise MCPError('未找到 Node.js（npx）：Gitee 企业版只允许官方 mcp-gitee-ent 客户端访问，请先安装 Node.js 18+ 后重试')
        return [npx, '-y', PACKAGE]

    def start(self, token):
        self.stop()
        env = {**os.environ, 'GITEE_ENT_MCP_ACCESS_TOKEN': token, 'GITEE_ENT_API_BASE': API}
        try:
            self.process = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, encoding='utf-8', errors='replace', bufsize=1, env=env,
                                            creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            raise MCPError('无法启动官方 Gitee MCP 客户端（需要 Node.js/npx）：'+str(exc)) from None
        self.token = token
        self.next_id = 2
        self.messages = queue.Queue()
        self.errors = deque(maxlen=20)
        threading.Thread(target=self._read_stdout, args=(self.process, self.messages), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.process, self.errors, token), daemon=True).start()
        self.send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-03-26', 'capabilities': {},
            'clientInfo': {'name': 'ai-work-tracker', 'version': '0.1.0'}}})
        try:
            self.wait(1, 180)
        except MCPError:
            self.stop()
            raise
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def call(self, tool, arguments, token, timeout=90):
        with self.lock:
            if self.process is None or self.process.poll() is not None or self.token != token:
                self.start(token)
            request_id = self.next_id
            self.next_id += 1
            self.send({'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                       'params': {'name': tool, 'arguments': arguments}})
            try:
                result = self.wait(request_id, timeout)
            except MCPError:
                self.stop()
                raise
            return self.result(result)

    def send(self, message):
        process = self.process
        if process is None or process.stdin is None:
            raise MCPError('Gitee MCP 客户端未启动')
        try:
            process.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
            process.stdin.flush()
        except (OSError, ValueError):
            raise MCPError('与官方 Gitee MCP 客户端的连接已断开'+self.detail()) from None

    def wait(self, request_id, timeout):
        messages = self.messages
        if messages is None:
            raise MCPError('Gitee MCP 客户端未启动')
        deadline = time.monotonic()+timeout
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise MCPError('官方 Gitee MCP 客户端响应超时')
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty:
                continue
            if message is None:
                raise MCPError('官方 Gitee MCP 客户端已退出'+self.detail())
            if not isinstance(message, dict) or message.get('id') != request_id:
                continue
            if isinstance(message.get('error'), dict):
                raise MCPError(str(message['error'].get('message') or 'MCP 请求失败'))
            result = message.get('result')
            return result if isinstance(result, dict) else {}

    def result(self, result):
        content = result.get('content')
        text = '\n'.join(str(part.get('text', '')) for part in content or []
                         if isinstance(part, dict) and part.get('type') == 'text')
        if result.get('isError'):
            raise MCPError(text or 'Gitee 企业版返回错误')
        if not text:
            return result
        try:
            return json.loads(text)
        except ValueError:
            return text

    def detail(self):
        lines = [line for line in (self.errors or []) if line]
        return ('：'+lines[-1][:200]) if lines else ''

    def _read_stdout(self, process, messages):
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    messages.put(line)
        except (OSError, ValueError):
            pass
        finally:
            messages.put(None)

    def _read_stderr(self, process, errors, token):
        try:
            for line in process.stderr:
                line = line.strip()
                if line:
                    errors.append(line.replace(token, '[redacted]') if token else line)
        except (OSError, ValueError):
            pass

    def stop(self):
        process, self.process = self.process, None
        self.token = ''
        self.messages = None
        if process is None:
            return
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=10)
            else:
                process.terminate()
                process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass


_server = _Server()
atexit.register(_server.stop)


def call(tool, arguments, token, timeout=90):
    return _server.call(tool, arguments, token, timeout)


def reset():
    _server.stop()
