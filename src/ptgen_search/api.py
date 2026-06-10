from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .ids import source_document_id
from .meili_client import MeiliClient, MeiliError

settings = get_settings()
client = MeiliClient(settings.meili_url, settings.meili_key)
app = FastAPI(title="PTGen search")

ALLOWED_SOURCES = {"douban", "imdb", "bangumi", "steam", "epic", "indienova"}
ALLOWED_KINDS = {"movie", "tv", "anime", "game", "work"}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = PROJECT_ROOT / "web"
STATE_FILE = settings.state_dir / "ingest-state.json"


def read_state() -> dict:
    if not STATE_FILE.exists():
        return {"status": "not_run"}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "invalid_state_file"}


def is_missing_index_error(exc: MeiliError) -> bool:
    text = str(exc)
    return "index_not_found" in text or f"Index `{settings.index_name}` not found" in text


def build_filter(source: str | None, kind: str | None, year: int | None) -> list[str]:
    filters: list[str] = []
    if source:
        if source not in ALLOWED_SOURCES:
            raise HTTPException(status_code=400, detail="invalid source filter")
        filters.append(f"sources = {json.dumps(source)}")
    if kind:
        if kind not in ALLOWED_KINDS:
            raise HTTPException(status_code=400, detail="invalid kind filter")
        filters.append(f"kind = {json.dumps(kind)}")
    if year is not None:
        filters.append(f"year = {year}")
    return filters


def lookup_document_id(source: str, source_id: str) -> str:
    if source not in ALLOWED_SOURCES:
        raise HTTPException(status_code=400, detail="invalid source")
    source_id = source_id.strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="missing id")
    return source_document_id(source, source_id)


@app.get("/api/health")
def health() -> dict:
    try:
        meili = client.health()
    except MeiliError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "meilisearch": meili}


@app.get("/api/status")
def status() -> dict:
    state = read_state()
    try:
        stats = client.index_stats(settings.index_name)
    except MeiliError:
        stats = None
    return {"state": state, "index": stats}


@app.get("/api/search")
def search(
    q: Annotated[str, Query()] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    source: str | None = None,
    kind: str | None = None,
    year: int | None = None,
) -> dict:
    payload = {
        "q": q,
        "limit": limit,
        "offset": offset,
        "attributesToHighlight": ["titles", "aliases", "people"],
        "highlightPreTag": "<mark>",
        "highlightPostTag": "</mark>",
    }
    filters = build_filter(source, kind, year)
    if filters:
        payload["filter"] = filters
    try:
        return client.search(settings.index_name, payload)
    except MeiliError as exc:
        if is_missing_index_error(exc):
            return {
                "hits": [],
                "estimatedTotalHits": 0,
                "limit": limit,
                "offset": offset,
                "processingTimeMs": 0,
                "query": q,
                "message": "index is still building",
            }
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/lookup")
def lookup(source: str, id: Annotated[str, Query(min_length=1)]) -> dict:
    try:
        return client.document(settings.index_name, lookup_document_id(source, id))
    except MeiliError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/works/{document_id:path}")
def work(document_id: str) -> dict:
    try:
        return client.document(settings.index_name, document_id)
    except MeiliError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.head("/")
def index_head() -> Response:
    return Response()


@app.get("/{path:path}")
def spa_fallback(path: str) -> FileResponse:
    asset = WEB_DIR / path
    if asset.exists() and asset.is_file():
        return FileResponse(asset)
    return FileResponse(WEB_DIR / "index.html")
