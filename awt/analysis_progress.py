"""Bounded, in-memory analysis status. Never stores prompts or reasoning text."""
from contextvars import ContextVar
from contextlib import contextmanager
from threading import Lock
from time import monotonic

current = ContextVar('analysis_progress', default=None)


class Progress:
    def __init__(self):
        self.lock = Lock()
        self.started = monotonic()
        self.finished = None
        self.state = 'running'
        self.logs = []

    def add(self, message, *, replace=False):
        with self.lock:
            row = {'seconds': round(monotonic() - self.started, 1), 'message': message}
            if replace and self.logs and self.logs[-1].get('live'):
                self.logs[-1] = {**row, 'live': True}
            else:
                self.logs.append({**row, 'live': replace})
                self.logs = self.logs[-100:]

    def finish(self, state):
        with self.lock:
            self.state = state
            self.finished = monotonic()

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'elapsed_seconds': round((self.finished or monotonic()) - self.started, 1),
                    'logs': [dict(row) for row in self.logs]}


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
