# PtGen Search

Local/internal search over the PtGen static archive.

The stack has one searchable entity: works. People are indexed as metadata on works,
so searching a director, writer, cast member, staff member, developer, or publisher
returns work results.

This can also be used as a media library search API. The API is intentionally small:
clients search works, inspect ingest/index status, and optionally fetch a work by
its internal document id.

## Services

- `meilisearch`: local persistent search backend
- `api`: FastAPI server and static web UI
- `ingester`: scheduled PtGen pull + works index rebuild

## Run

Start the full local stack, including the automatic ingester:

```bash
cp .env.example .env
docker compose up -d --build
```

Open <http://127.0.0.1:8080>.

If port `8080` is already used, change `API_PORT` in `.env`, for example:

```env
API_PORT=8088
```

The ingester owns its PtGen clone at `./data/ptgen` and Meilisearch stores its index
at `./data/meili`.

## Full Index

The ingester is its own scheduler. It currently does a full rebuild on every
scheduled run, not an incremental update. On startup it will:

1. clone or update the PtGen repo in `./data/ptgen`
2. normalize work records from `douban`, `imdb`, `bangumi`, `steam`, `epic`, and `indienova`
3. index people names as searchable metadata on works
4. publish through a shadow Meilisearch index and swap it into `works`
5. sleep for `INGEST_INTERVAL_SECONDS` before checking again

The shadow-index flow means the live `works` index remains available while a new
daily rebuild is running. If a rebuild fails, the previous successful index stays
active. The only exception is first bootstrap: before the first successful build,
there is no live `works` index yet, so search returns an empty "index is still
building" response.

For a full index, keep these values in `.env`:

```env
PTGEN_MAX_FILES_PER_SOURCE=0
PTGEN_INCLUDE_FILES=
INGEST_RUN_ON_START=1
```

Watch progress:

```bash
docker compose logs -f ingester
```

The per-batch progress appears in the ingester logs as `normalized N files` and
`indexed N/TOTAL documents`. `/api/status` reports the run state, but it does not
currently expose per-batch ingest progress.

Check status:

```bash
curl http://127.0.0.1:8080/api/status
```

Before the first full ingest completes, `/api/search` returns an empty bootstrap
response. After the first successful swap, search serves the completed `works`
index while future scheduled rebuilds happen in the background.

## API And UI Only

If you want to start Meilisearch and the UI without kicking off a full ingest:

```bash
docker compose up -d --build meilisearch api
```

## Media Library API

The public API is served by the `api` service. If exposed through a tunnel or
reverse proxy, replace `http://127.0.0.1:8080` with your public base URL.
Responses are JSON. The built-in web UI and API are served from the same origin;
browser clients hosted on another origin need either a same-origin proxy or CORS
middleware added to the API.

### `GET /api/search`

Search works. Results are always works; people are searchable fields on work
documents and are never returned as standalone entities.

Query parameters:

| Parameter | Required | Description |
| --- | --- | --- |
| `q` | no | Search text. Matches titles, aliases, source ids, people, genres/tags, and descriptions. |
| `limit` | no | Number of results, `1` to `100`. Default: `20`. |
| `offset` | no | Result offset for pagination. Default: `0`. |
| `source` | no | One of `douban`, `imdb`, `bangumi`, `steam`, `epic`, `indienova`. |
| `kind` | no | One of `movie`, `tv`, `anime`, `game`, `work`. |
| `year` | no | Exact normalized release year. |

Example:

```bash
curl 'http://127.0.0.1:8080/api/search?q=Zdenek%20Jirasky&limit=5'
```

Response shape:

```json
{
  "hits": [
    {
      "id": "work_imdb_tt1741246",
      "kind": "work",
      "sources": ["douban"],
      "source_ids": {
        "douban": "10000794",
        "imdb": "tt1741246"
      },
      "source_paths": {
        "douban": "douban/10000794.json"
      },
      "titles": ["花蕾", "Poupata", "Flower Buds"],
      "aliases": ["Flower Buds"],
      "year": 2011,
      "release_date": "2011-12-01(捷克)",
      "genres": ["剧情"],
      "tags": [],
      "regions": ["捷克"],
      "languages": ["捷克语"],
      "people": ["齐德内克·吉拉斯基 Zdenek Jirasky"],
      "directors": ["齐德内克·吉拉斯基 Zdenek Jirasky"],
      "writers": ["齐德内克·吉拉斯基 Zdenek Jirasky"],
      "cast": [],
      "staff": [],
      "developers": [],
      "publishers": [],
      "description": "Work summary text",
      "poster": "https://example.test/poster.jpg",
      "rating_score": 6.5,
      "rating_votes": 1234,
      "updated_at": "2025-06-29T15:36:16",
      "_formatted": {
        "titles": ["花蕾"],
        "people": ["齐德内克·吉拉斯基 <mark>Zdenek Jirasky</mark>"]
      }
    }
  ],
  "estimatedTotalHits": 1,
  "limit": 5,
  "offset": 0,
  "processingTimeMs": 2,
  "query": "Zdenek Jirasky"
}
```

During first bootstrap, before the first `works` index exists, search returns:

```json
{
  "hits": [],
  "estimatedTotalHits": 0,
  "limit": 20,
  "offset": 0,
  "processingTimeMs": 0,
  "query": "anything",
  "message": "index is still building"
}
```

### `GET /api/status`

Return ingest and index health.

Example:

```bash
curl http://127.0.0.1:8080/api/status
```

Response shape while building:

```json
{
  "state": {
    "status": "running",
    "run_id": "20260609203514",
    "started_at": "2026-06-09T20:35:14.746796+00:00",
    "finished_at": null,
    "source_commit": "6c464d7ec74a697b1825a62a9b4dc469f4b6d9e8",
    "documents": 0,
    "files_seen": 0,
    "errors": 0
  },
  "index": null
}
```

Response shape after a successful build:

```json
{
  "state": {
    "status": "succeeded",
    "run_id": "20260609203514",
    "started_at": "2026-06-09T20:35:14.746796+00:00",
    "finished_at": "2026-06-09T21:20:00.000000+00:00",
    "source_commit": "6c464d7ec74a697b1825a62a9b4dc469f4b6d9e8",
    "documents": 506822,
    "files_seen": 886037,
    "errors": 0
  },
  "index": {
    "numberOfDocuments": 506822,
    "isIndexing": false,
    "fieldDistribution": {}
  }
}
```

### `GET /api/works/{id}`

Fetch one indexed work by document id.

Example:

```bash
curl http://127.0.0.1:8080/api/works/work_imdb_tt1741246
```

The response is the same work document shape returned inside `hits`.

### `GET /api/health`

Basic API and Meilisearch health check.

```bash
curl http://127.0.0.1:8080/api/health
```

Expected healthy response:

```json
{
  "ok": true,
  "meilisearch": {
    "status": "available"
  }
}
```


## Manual Ingest

Run a one-off rebuild from the host while the Meilisearch container is running:

```bash
PYTHONPATH=src \
MEILI_URL=http://127.0.0.1:7700 \
MEILI_MASTER_KEY=ptgen-local-master-key \
PTGEN_PATH=/home/lei/workspace/cloned/PtGen \
PTGEN_SKIP_GIT_UPDATE=1 \
python3 -m ptgen_search.ingest once --force
```

For quick tests, limit input:

```bash
PTGEN_MAX_FILES_PER_SOURCE=25
PYTHONPATH=src \
MEILI_URL=http://127.0.0.1:7700 \
MEILI_MASTER_KEY=ptgen-local-master-key \
PTGEN_PATH=/home/lei/workspace/cloned/PtGen \
PTGEN_SKIP_GIT_UPDATE=1 \
python3 -m ptgen_search.ingest once --force
```

Or only ingest specific files:

```bash
PTGEN_INCLUDE_FILES=douban/10000794.json,bangumi/100040.json
PYTHONPATH=src \
MEILI_URL=http://127.0.0.1:7700 \
MEILI_MASTER_KEY=ptgen-local-master-key \
PTGEN_PATH=/home/lei/workspace/cloned/PtGen \
PTGEN_SKIP_GIT_UPDATE=1 \
python3 -m ptgen_search.ingest once --force
```

## Stop

```bash
docker compose down
```
