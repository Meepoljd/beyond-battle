import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import urlparse
from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .domain import ROOT, SCENARIO, player_view
from .storage import Store, DomainError
from .opencode import OpenCodeSettings
from .turns import TurnRunner


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CreateMatch(Strict):
    scenario_id: Literal['bridge_v1'] = 'bridge_v1'
    commander_ids: list[str] = Field(default_factory=lambda: ['lin', 'zhou', 'chen'], min_length=1, max_length=3)
    seed: int = Field(default=42, ge=0, le=2147483647)
    provider_mode: Literal['rules', 'opencode'] = 'rules'
    use_memories: bool = True

    @model_validator(mode='after')
    def valid_commanders(self):
        if len(set(self.commander_ids)) != len(self.commander_ids) or set(self.commander_ids) - {c['id'] for c in SCENARIO['commanders']}:
            raise ValueError('invalid commanders')
        return self


class Version(Strict):
    match_version: int = Field(ge=0)


class Order(Version):
    unit_id: str = Field(max_length=30)
    action: Literal['MOVE', 'HOLD', 'OBSERVE', 'ATTACK', 'SUPPORT', 'WITHDRAW', 'REST']
    target: str = Field(max_length=30)
    priority: int = Field(default=1, ge=0, le=3)
    expires_tick: int = Field(ge=1, le=1000)
    direct: bool = False
    autonomy: bool = True
    retreat_threshold: int = Field(default=7, ge=0, le=15)
    silent_until: int = Field(default=0, ge=0, le=1000)
    supersedes_id: str | None = Field(default=None, max_length=30)
    text: str = Field(default='', max_length=600)
    report_before_withdraw: bool = True


class Review(Strict):
    accepted_ids: list[str] = Field(max_length=3)


class MemoryState(Strict):
    active: bool


Key = Annotated[str, Header(alias='Idempotency-Key', min_length=8, max_length=100)]


def create_app(db_path=None, opencode_settings=None):
    settings = opencode_settings if opencode_settings is not None else OpenCodeSettings.from_env()
    store = Store(db_path or os.environ.get('BATTLE_DB', ROOT / 'data/battle.sqlite3'), settings)
    runner = TurnRunner(store)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        try:
            yield
        finally:
            runner.close()

    app = FastAPI(title='战报之外 · 演习 API', version='0.2.0', lifespan=lifespan)
    app.state.store = store

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse(status_code=403, content={'error': 'ORIGIN_REJECTED', 'request_id': request.state.request_id})
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(status_code=exc.status, content={'error': exc.code, 'request_id': request.state.request_id})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={'error': 'INVALID_INPUT', 'request_id': request.state.request_id})

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse(status_code=500, content={'error': 'INTERNAL_ERROR', 'request_id': request.state.request_id})

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': 'rules'}

    @app.get('/readyz')
    def ready():
        with store.connection() as db:
            version = db.execute('SELECT version FROM schema_version').fetchone()[0]
        return {'status': 'ready', 'schema_version': version}

    @app.get('/api/commanders')
    def commanders():
        return store.commanders()

    @app.get('/api/providers')
    def providers():
        return {'rules': {'available': True}, 'opencode': {
            'available': settings.enabled,
            'model': settings.model if settings.enabled else None,
            'max_calls': settings.max_calls if settings.enabled else 0}}

    @app.post('/api/matches', status_code=201)
    def create(body: CreateMatch, key: Key):
        return store.create(body.model_dump(), key)

    @app.get('/api/matches/{mid}')
    def view(mid: str):
        s, v = store.get(mid)
        result = player_view(s, mid, v)
        if result['provider_mode'] == 'opencode':
            result['provider_calls_reserved'] = store.provider_usage(mid)
            result['provider_call_limit'] = s['provider_config']['max_calls']
        return result

    @app.post('/api/matches/{mid}/orders', status_code=201)
    def order(mid: str, body: Order, key: Key):
        return store.order(mid, body.model_dump(), key)

    @app.post('/api/matches/{mid}/advance', status_code=202)
    def advance(mid: str, body: Version, key: Key):
        state, _ = store.get(mid)
        if state.get('provider_config', {}).get('mode') == 'opencode':
            return runner.submit(mid, body.model_dump(), key)
        return store.advance(mid, body.model_dump(), key)

    @app.get('/api/jobs/{jid}')
    def job(jid: str):
        return store.job(jid)

    @app.get('/api/matches/{mid}/reports')
    def reports(mid: str, after_seq: int = Query(default=0, ge=0)):
        s, _ = store.get(mid)
        return [r for r in s['reports'] if r['seq'] > after_seq]

    @app.post('/api/matches/{mid}/end')
    def end(mid: str, body: Version, key: Key):
        return store.end(mid, body.model_dump(), key)

    @app.get('/api/matches/{mid}/replay')
    def replay(mid: str):
        return store.get_replay(mid)

    @app.get('/api/matches/{mid}/memory-candidates')
    def candidates(mid: str):
        return store.memory_candidates(mid)

    @app.post('/api/matches/{mid}/memory-review')
    def review(mid: str, body: Review, key: Key):
        return store.review(mid, body.model_dump(), key)

    @app.patch('/api/memories/{memory_id}')
    def memory(memory_id: str, body: MemoryState):
        return store.set_memory(memory_id, body.active)

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web/index.html')

    app.mount('/assets', StaticFiles(directory=ROOT / 'web'), name='assets')
    return app


app = create_app()
