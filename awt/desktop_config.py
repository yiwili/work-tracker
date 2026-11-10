import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DESKTOP_ALLOWED_ORIGINS = [
    'http://127.0.0.1:8765',
    'http://localhost:8765',
    'http://127.0.0.1:5173',
    'http://localhost:5173',
    'tauri://localhost',
    'https://tauri.localhost',
    'app://.',
]


def get_allowed_origins() -> list[str]:
    origins = list(DESKTOP_ALLOWED_ORIGINS)
    for raw in os.environ.get('AWT_CORS_ORIGINS', '').split(','):
        origin = raw.strip()
        if origin and origin not in origins:
            origins.append(origin)
    return origins


def configure_desktop_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_allowed_origins(),
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.get('/api/runtime-info')
    def runtime_info():
        return {
            'service': 'ai-work-tracker',
            'mode': os.environ.get('AWT_RUNTIME_MODE', 'local-web'),
            'api_version': '1.0',
            'desktop_ready': True,
        }
