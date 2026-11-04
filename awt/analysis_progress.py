"""Bounded, in-memory analysis status for the live thinking/output window.

Streamed text is kept only in this process for display and is never written to
the database or included in saved analysis snapshots. Prompts and credentials
are still never exposed.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from threading import Lock
from time import monotonic

current = ContextVar('analysis_progress', default=None)
MAX_STREAM_CHARS = 20000


def _tail(text):
    if len(text) <= MAX_STREAM_CHARS:
        return text
    return '…（更早内容已省略）\n' + text[-MAX_STREAM_CHARS:]


def _estimate_tokens(cjk, chars):
    # CJK is about one token per character; other text about four characters.
    return max(0, cjk + (chars - cjk) // 4)


class Progress:
    def __init__(self):
        self.lock = Lock()
        self.started = monotonic()
        self.finished = None
        self.state = 'running'
        self.logs = []
        self.thinking = ''
        self.output = ''
        self.model = ''
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.call_started = self.started
        self.thinking_started = None
        self.thinking_finished = None
        self._awaiting_thinking = False
        self._thinking_current = False
        # The placeholder before the first request is not a real call; treating
        # it as finalized keeps it out of the estimated-usage calculation.
        self.call_finalized = True
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.any_estimated = False
        self._prompt_exact = False
        self._completion_exact = False
        self._thinking_chars = 0
        self._thinking_cjk = 0
        self._output_chars = 0
        self._output_cjk = 0

    def add(self, message, *, replace=False):
        with self.lock:
            row = {'seconds': round(monotonic() - self.started, 1), 'message': message}
            if replace and self.logs and self.logs[-1].get('live'):
                self.logs[-1] = {**row, 'live': True}
            else:
                self.logs.append({**row, 'live': replace})
                self.logs = self.logs[-100:]

    def _finalize_call(self):
        if self.call_finalized:
            return
        if self._thinking_current and self.thinking_finished is None:
            self.thinking_finished = monotonic()
        self.total_prompt_tokens += self.prompt_tokens
        self.total_completion_tokens += self.completion_tokens
        self.any_estimated = self.any_estimated or not (self._prompt_exact and self._completion_exact)
        self.call_finalized = True

    def begin_stream(self, model, prompt_tokens):
        # Each model request gets its own thinking/output block. The previously
        # displayed thinking stays until the new request starts reasoning, so a
        # final call without reasoning never blanks the window.
        with self.lock:
            self._finalize_call()
            self.call_started = monotonic()
            self.output = ''
            self._awaiting_thinking = True
            self._thinking_current = False
            self.model = model or ''
            self.prompt_tokens = max(1, int(prompt_tokens or 0))
            self.completion_tokens = 0
            self._prompt_exact = False
            self._completion_exact = False
            self._thinking_chars = 0
            self._thinking_cjk = 0
            self._output_chars = 0
            self._output_cjk = 0
            self.call_finalized = False

    def push_stream(self, thinking='', output=''):
        with self.lock:
            if thinking:
                if self._awaiting_thinking:
                    self.thinking = ''
                    # Count the visible thinking phase from the request start,
                    # so short reasoning bursts do not display as 0 seconds.
                    self.thinking_started = self.call_started
                    self.thinking_finished = None
                    self._awaiting_thinking = False
                self._thinking_current = True
                self.thinking = _tail(self.thinking + thinking)
                self._thinking_chars += len(thinking)
                self._thinking_cjk += sum(1 for char in thinking if '\u4e00' <= char <= '\u9fff')
            if output:
                self.output = _tail(self.output + output)
                if self._thinking_current and self.thinking_finished is None and output.strip():
                    self.thinking_finished = monotonic()
                self._output_chars += len(output)
                self._output_cjk += sum(1 for char in output if '\u4e00' <= char <= '\u9fff')
            if thinking or output:
                self.completion_tokens = _estimate_tokens(
                    self._thinking_cjk + self._output_cjk,
                    self._thinking_chars + self._output_chars)

    def set_usage(self, prompt=None, completion=None):
        with self.lock:
            if prompt is not None:
                self.prompt_tokens = max(0, int(prompt))
                self._prompt_exact = True
            if completion is not None:
                self.completion_tokens = max(0, int(completion))
                self._completion_exact = True

    def finish(self, state):
        with self.lock:
            self._finalize_call()
            self.state = state
            self.finished = monotonic()

    def snapshot(self):
        with self.lock:
            now = self.finished or monotonic()
            thinking_seconds = 0 if self.thinking_started is None else (
                (self.thinking_finished or now) - self.thinking_started)
            prompt = self.total_prompt_tokens + (0 if self.call_finalized else self.prompt_tokens)
            completion = self.total_completion_tokens + (0 if self.call_finalized else self.completion_tokens)
            estimated = self.any_estimated or (
                False if self.call_finalized else not (self._prompt_exact and self._completion_exact))
            return {
                'state': self.state,
                'elapsed_seconds': round(now - self.started, 1),
                'logs': [dict(row) for row in self.logs],
                'thinking': self.thinking,
                'thinking_live': bool(self._thinking_current and self.thinking_finished is None and self.finished is None),
                'model': self.model,
                'thinking_seconds': round(max(0, thinking_seconds), 1),
                'tokens': {
                    'prompt_tokens': prompt,
                    'completion_tokens': completion,
                    'total_tokens': prompt + completion,
                    'estimated': estimated,
                },
            }


@contextmanager
def track(progress):
    token = current.set(progress)
    try:
        yield
    finally:
        current.reset(token)


def emit(message, *, replace=False):
    progress = current.get()
    if progress is not None:
        progress.add(message, replace=replace)


def begin_stream(model, prompt_tokens):
    progress = current.get()
    if progress is not None:
        progress.begin_stream(model, prompt_tokens)


def stream_delta(thinking='', output=''):
    progress = current.get()
    if progress is not None and (thinking or output):
        progress.push_stream(thinking, output)


def stream_usage(prompt=None, completion=None):
    progress = current.get()
    if progress is not None:
        progress.set_usage(prompt=prompt, completion=completion)
