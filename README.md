# PtGen Search

Local/internal search over the PtGen static archive.

The stack has one searchable entity: works. People are indexed as metadata on works,
so searching a director, writer, cast member, staff member, developer, or publisher
returns work results.

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

The ingester is its own scheduler. On startup it will:

1. clone or update the PtGen repo in `./data/ptgen`
2. normalize work records from `douban`, `imdb`, `bangumi`, `steam`, `epic`, and `indienova`
3. index people names as searchable metadata on works
4. publish through a shadow Meilisearch index and swap it into `works`
5. sleep for `INGEST_INTERVAL_SECONDS` before checking again

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

Check status:

```bash
curl http://127.0.0.1:8080/api/status
```

Until the full ingest completes, search results only reflect whatever has already
been indexed. The initial review instance was seeded with a few real records only,
so its search coverage is intentionally very limited.

## API And UI Only

If you want to start Meilisearch and the UI without kicking off a full ingest:

```bash
docker compose up -d --build meilisearch api
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
