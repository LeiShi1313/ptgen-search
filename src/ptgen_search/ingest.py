from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Settings, get_settings
from .ids import normalize_imdb_id, source_document_id
from .meili_client import MeiliClient

WORK_SOURCES = ("douban", "imdb", "bangumi", "steam", "epic", "indienova")
INDEX_SCHEMA_VERSION = "source-specific-v2"
GAME_SOURCES = {"steam", "epic", "indienova"}
SOURCE_KINDS = {"Movie": "movie", "TVSeries": "tv", "TVEpisode": "tv", "VideoGame": "game"}
ANIME_PLATFORMS = {"TV", "OVA", "OAD", "WEB", "剧场版", "Movie", "电影", "日剧", "欧美剧", "华语剧", "电视剧", "动态漫画"}
BANGUMI_STAFF_KEYS = {
    "导演",
    "監督",
    "脚本",
    "分镜",
    "演出",
    "音乐",
    "人物设定",
    "系列构成",
    "原作",
    "作画监督",
}
TEXT_LIMIT = 1800
LIST_LIMIT = 120


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(args: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def ensure_git_safe_directory(path: Path) -> None:
    completed = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    safe_paths = set(completed.stdout.splitlines())
    if str(path) not in safe_paths:
        run_git(["config", "--global", "--add", "safe.directory", str(path)])


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\u00a0", " ")
    text = " ".join(text.split())
    if not text or text.lower() in {"none", "null", "n/a"}:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        return None
    return text


def clean_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return None


def as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def string_values(value: Any) -> list[str]:
    values: list[str] = []
    if value is None:
        return values
    if isinstance(value, str):
        text = clean_text(value)
        if text:
            values.append(text)
        return values
    if isinstance(value, (int, float)):
        values.append(str(value))
        return values
    if isinstance(value, list):
        for item in value:
            values.extend(string_values(item))
        return values
    if isinstance(value, dict):
        for item in value.values():
            values.extend(string_values(item))
    return values


def named_values(value: Any) -> list[str]:
    names: list[str] = []
    if value is None:
        return names
    if isinstance(value, str):
        text = clean_text(value)
        if text and len(text) <= 160:
            names.append(text)
        return names
    if isinstance(value, list):
        for item in value:
            names.extend(named_values(item))
        return names
    if isinstance(value, dict):
        for key in ("name", "name_cn", "name_en", "name_full", "cn_name", "jp_name"):
            text = clean_text(value.get(key))
            if text:
                names.append(text)
        if not names:
            for item in value.values():
                names.extend(named_values(item))
    return names


def split_credit_text(value: Any) -> list[str]:
    names: list[str] = []
    for text in string_values(value):
        for part in text.replace("/", "、").replace("，", "、").replace(",", "、").split("、"):
            cleaned = clean_text(part)
            if cleaned:
                names.append(cleaned)
    return names


def bangumi_cast_people(value: Any) -> list[str]:
    names: list[str] = []
    for item in as_items(value):
        if isinstance(item, dict):
            names.extend(named_values(item.get("actors")))
    return names


def bangumi_staff_people(value: Any) -> list[str]:
    names: list[str] = []
    for item in as_items(value):
        if not isinstance(item, dict):
            continue
        key = clean_text(item.get("key"))
        if key in BANGUMI_STAFF_KEYS:
            names.extend(split_credit_text(item.get("value")))
    return names


def text_matches(values: Iterable[Any], needles: tuple[str, ...]) -> bool:
    text = " ".join(string_values(list(values))).casefold()
    return any(needle.casefold() in text for needle in needles)


def guessed_douban_kind(data: dict[str, Any]) -> str:
    title_values = (
        string_values(data.get("name"))
        + string_values(data.get("chinese_title"))
        + string_values(data.get("foreign_title"))
        + string_values(data.get("this_title"))
        + string_values(data.get("trans_title"))
        + string_values(data.get("aka"))
    )
    if text_matches(
        title_values,
        (
            "season",
            "tv series",
            "tv-series",
            "mini-series",
            "miniseries",
            "第1季",
            "第一季",
            "第二季",
            "第三季",
            "第四季",
            "第五季",
            "第六季",
            "第七季",
            "第八季",
            "第九季",
            "第十季",
        ),
    ):
        return "tv"

    episodes = int_value(data.get("episodes"))
    if episodes and episodes > 1:
        return "tv"
    return "movie"


def imdb_kind_from_type(data: dict[str, Any]) -> str | None:
    raw_type = clean_text(data.get("@type"))
    return SOURCE_KINDS.get(raw_type)


def normalized_kind(site: str, data: dict[str, Any], linked_imdb_kind: str | None = None) -> str:
    if site == "douban" and linked_imdb_kind:
        return linked_imdb_kind
    if site == "douban":
        return guessed_douban_kind(data)
    if site in GAME_SOURCES:
        return "game"
    if site == "bangumi":
        platform = clean_text(data.get("platform"))
        if platform in {"游戏", "Game"}:
            return "game"
        if platform in ANIME_PLATFORMS:
            return "anime"
        return "anime"
    imdb_kind = imdb_kind_from_type(data)
    if imdb_kind:
        return imdb_kind
    return "movie"


def unique(values: Iterable[Any], limit: int = LIST_LIMIT) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            normalized = clean_text(value)
            if normalized is None:
                continue
            value = normalized
            marker = normalized.casefold()
        else:
            marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def first_text(data: dict[str, Any], fields: Iterable[str], limit: int = TEXT_LIMIT) -> str | None:
    candidates: list[str] = []
    for field in fields:
        value = data.get(field)
        if isinstance(value, str):
            text = clean_text(value)
            if text:
                candidates.append(text)
    if not candidates:
        return None
    text = max(candidates, key=len)
    return text[:limit]


def first_string(*values: Any) -> str | None:
    for value in values:
        strings = string_values(value)
        if strings:
            return strings[0]
    return None


def int_year(value: Any) -> int | None:
    for text in string_values(value):
        digits = "".join(ch for ch in text[:4] if ch.isdigit())
        if len(digits) == 4:
            year = int(digits)
            if 1800 <= year <= 2200:
                return year
    return None


def float_value(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def int_value(value: Any) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def load_title_maps(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    path = root / "internal_map" / "douban_imdb_map.json"
    if not path.exists():
        return {}, {}
    with path.open("r", encoding="utf-8") as handle:
        rows = json.load(handle)
    douban_to_imdb: dict[str, str] = {}
    imdb_to_douban: dict[str, str] = {}
    for row in rows:
        dbid = clean_text(row.get("dbid"))
        imdbid = clean_text(row.get("imdbid"))
        if not dbid or not imdbid:
            continue
        imdbid = normalize_imdb_id(imdbid)
        douban_to_imdb[dbid] = imdbid
        imdb_to_douban[imdbid] = dbid
    return douban_to_imdb, imdb_to_douban


def load_imdb_kind_map(root: Path) -> dict[str, str]:
    directory = root / "imdb"
    if not directory.exists():
        return {}
    kinds: dict[str, str] = {}
    for path in directory.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            imdb_id = normalize_imdb_id(clean_text(data.get("sid")) or path.stem)
            kind = imdb_kind_from_type(data)
            if kind:
                kinds[imdb_id] = kind
        except Exception as exc:  # noqa: BLE001 - bad source rows should not stop kind inference
            print(f"failed to read IMDb kind {path}: {exc}", file=sys.stderr)
    return kinds


def document_id(site: str, sid: str) -> str:
    return source_document_id(site, sid)


def normalize_work(
    site: str,
    sid: str,
    data: dict[str, Any],
    rel_path: str,
    douban_to_imdb: dict[str, str],
    imdb_to_douban: dict[str, str],
    imdb_kind_by_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    doc_id = document_id(site, sid)
    titles = unique(
        string_values(data.get("name"))
        + string_values(data.get("name_cn"))
        + string_values(data.get("name_chs"))
        + string_values(data.get("chinese_title"))
        + string_values(data.get("foreign_title"))
        + string_values(data.get("english_title"))
        + string_values(data.get("this_title"))
        + string_values(data.get("trans_title"))
    )
    aliases = unique(
        string_values(data.get("aka"))
        + string_values(data.get("alt"))
        + string_values(data.get("another_title"))
    )

    directors = unique(named_values(data.get("director")) + named_values(data.get("directors")))
    writers = unique(named_values(data.get("writer")) + named_values(data.get("writers")))
    if site == "bangumi":
        cast = unique(bangumi_cast_people(data.get("cast")))
        staff = unique(bangumi_staff_people(data.get("staff")))
    else:
        cast = unique(named_values(data.get("cast")) + named_values(data.get("actors")))
        staff = unique(named_values(data.get("staff")) + named_values(data.get("creators")))
    developers = unique(named_values(data.get("dev")))
    publishers = unique(named_values(data.get("pub")))
    people = unique(directors + writers + cast + staff + developers + publishers)

    source_ids: dict[str, str] = {site: sid}
    if site == "douban":
        imdb_id = clean_text(data.get("imdb_id")) or douban_to_imdb.get(sid)
        if imdb_id:
            source_ids["imdb"] = normalize_imdb_id(imdb_id)
    if site == "imdb":
        imdb_id = normalize_imdb_id(sid)
        source_ids["imdb"] = imdb_id
        douban_id = imdb_to_douban.get(imdb_id)
        if douban_id:
            source_ids["douban"] = douban_id

    rating_score = (
        float_value(data.get("douban_rating_average"))
        or float_value(data.get("imdb_rating_average"))
        or float_value((data.get("rating") or {}).get("score") if isinstance(data.get("rating"), dict) else None)
    )
    rating_votes = (
        int_value(data.get("douban_votes"))
        or int_value(data.get("imdb_votes"))
        or int_value((data.get("rating") or {}).get("total") if isinstance(data.get("rating"), dict) else None)
    )

    year = int_year(data.get("year")) or int_year(data.get("datePublished")) or int_year(data.get("date"))
    linked_imdb_kind = None
    if site == "douban":
        imdb_id = source_ids.get("imdb")
        if imdb_id:
            linked_imdb_kind = (imdb_kind_by_id or {}).get(imdb_id)
    kind = normalized_kind(site, data, linked_imdb_kind)

    return {
        "id": doc_id,
        "kind": kind,
        "sources": [site],
        "source_ids": source_ids,
        "source_paths": {site: rel_path},
        "titles": titles,
        "aliases": aliases,
        "year": year,
        "release_date": first_string(data.get("datePublished"), data.get("date"), data.get("release_date"), data.get("playdate")),
        "genres": unique(string_values(data.get("genre")) + string_values(data.get("cat"))),
        "tags": unique(string_values(data.get("tags")) + string_values(data.get("keywords"))),
        "regions": unique(string_values(data.get("region"))),
        "languages": unique(string_values(data.get("language"))),
        "people": people,
        "directors": directors,
        "writers": writers,
        "cast": cast,
        "staff": staff,
        "developers": developers,
        "publishers": publishers,
        "description": first_text(data, ("introduction", "description", "story", "descr", "desc", "intro")),
        "poster": clean_url(data.get("poster")) or clean_url(data.get("cover")) or clean_url(data.get("logo")),
        "rating_score": rating_score,
        "rating_votes": rating_votes,
        "updated_at": clean_text(data.get("update_at")),
    }


def iter_source_files(root: Path, settings: Settings) -> Iterable[tuple[str, Path, str]]:
    if settings.include_file_list:
        for rel in settings.include_file_list:
            path = root / rel
            if path.exists() and path.suffix == ".json":
                site = rel.split("/", 1)[0]
                if site in WORK_SOURCES:
                    yield site, path, rel
        return

    for site in WORK_SOURCES:
        directory = root / site
        if not directory.exists():
            continue
        count = 0
        for path in sorted(directory.glob("*.json")):
            yield site, path, f"{site}/{path.name}"
            count += 1
            if settings.max_files_per_source and count >= settings.max_files_per_source:
                break


def upsert_staged(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:
    row = conn.execute("select doc from works where id = ?", (doc["id"],)).fetchone()
    if row:
        doc = choose_duplicate_doc(json.loads(row[0]), doc)
    conn.execute(
        "insert or replace into works(id, doc) values(?, ?)",
        (doc["id"], json.dumps(doc, ensure_ascii=False, separators=(",", ":"))),
    )


def doc_quality_key(doc: dict[str, Any]) -> tuple[Any, ...]:
    list_fields = (
        "titles",
        "aliases",
        "genres",
        "tags",
        "regions",
        "languages",
        "people",
        "directors",
        "writers",
        "cast",
        "staff",
        "developers",
        "publishers",
    )
    scalar_fields = ("year", "release_date", "poster", "rating_score", "rating_votes", "updated_at", "description")
    list_count = sum(len(as_items(doc.get(field))) for field in list_fields)
    scalar_count = sum(1 for field in scalar_fields if doc.get(field) not in (None, "", []))
    description_len = len(doc.get("description") or "")
    return (
        doc.get("updated_at") or "",
        int_value(doc.get("rating_votes")) or 0,
        scalar_count,
        list_count,
        description_len,
        json.dumps(doc.get("source_paths", {}), ensure_ascii=False, sort_keys=True),
    )


def choose_duplicate_doc(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if doc_quality_key(candidate) >= doc_quality_key(existing):
        return candidate
    return existing


def prepare_source(settings: Settings) -> str | None:
    path = settings.ptgen_path
    if (path / ".git").exists():
        ensure_git_safe_directory(path)
    if settings.skip_git_update:
        return current_commit(path)
    if not (path / ".git").exists():
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", "--branch", settings.ptgen_branch, settings.ptgen_repo_url, str(path)])
    else:
        run_git(["fetch", "--prune", "origin"], cwd=path)
        run_git(["checkout", settings.ptgen_branch], cwd=path)
        run_git(["reset", "--hard", f"origin/{settings.ptgen_branch}"], cwd=path)
    return current_commit(path)


def current_commit(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    return run_git(["rev-parse", "HEAD"], cwd=path)


def load_state(settings: Settings) -> dict[str, Any]:
    path = settings.state_dir / "ingest-state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_state(settings: Settings, state: dict[str, Any]) -> None:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path = settings.state_dir / "ingest-state.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def build_staging(settings: Settings, run_id: str) -> tuple[Path, int, int, int]:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    staging_path = settings.state_dir / f"staging-{run_id}.sqlite"
    if staging_path.exists():
        staging_path.unlink()

    douban_to_imdb, imdb_to_douban = load_title_maps(settings.ptgen_path)
    imdb_kind_by_id = load_imdb_kind_map(settings.ptgen_path)
    conn = sqlite3.connect(staging_path)
    conn.execute("pragma journal_mode=wal")
    conn.execute("pragma synchronous=normal")
    conn.execute("create table works(id text primary key, doc text not null)")

    files_seen = 0
    errors = 0
    for site, path, rel_path in iter_source_files(settings.ptgen_path, settings):
        sid = path.stem
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            sid = clean_text(data.get("sid")) or path.stem
            doc = normalize_work(site, sid, data, rel_path, douban_to_imdb, imdb_to_douban, imdb_kind_by_id)
            if doc["titles"] or doc["aliases"]:
                upsert_staged(conn, doc)
        except Exception as exc:  # noqa: BLE001 - bad source rows should not stop the run
            errors += 1
            print(f"failed to normalize {rel_path}: {exc}", file=sys.stderr)
        files_seen += 1
        if files_seen % 1000 == 0:
            conn.commit()
            print(f"normalized {files_seen} files", flush=True)

    conn.commit()
    doc_count = conn.execute("select count(*) from works").fetchone()[0]
    conn.close()
    return staging_path, int(doc_count), files_seen, errors


def load_index_settings() -> dict[str, Any]:
    settings_path = Path(__file__).resolve().parents[2] / "config" / "works.settings.json"
    return json.loads(settings_path.read_text(encoding="utf-8"))


def stream_staged_docs(staging_path: Path, batch_size: int) -> Iterable[list[dict[str, Any]]]:
    conn = sqlite3.connect(staging_path)
    try:
        cursor = conn.execute("select doc from works order by id")
        batch: list[dict[str, Any]] = []
        for (raw_doc,) in cursor:
            batch.append(json.loads(raw_doc))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
    finally:
        conn.close()


def ensure_index(client: MeiliClient, index_name: str) -> None:
    try:
        client.index_stats(index_name)
    except Exception:
        client.wait_task(client.create_index(index_name))


def delete_index_if_exists(client: MeiliClient, index_name: str) -> None:
    try:
        client.index_stats(index_name)
    except Exception:
        return
    client.wait_task(client.delete_index(index_name))


def publish_to_meili(settings: Settings, staging_path: Path, run_id: str, doc_count: int) -> None:
    client = MeiliClient(settings.meili_url, settings.meili_key, timeout=180.0)
    shadow = f"{settings.index_name}_build_{run_id}"
    swapped = False
    try:
        delete_index_if_exists(client, shadow)
        client.wait_task(client.create_index(shadow))
        client.wait_task(client.update_settings(shadow, load_index_settings()))

        indexed = 0
        for batch in stream_staged_docs(staging_path, settings.ingest_batch_size):
            client.wait_task(client.add_documents(shadow, batch), timeout_seconds=1800)
            indexed += len(batch)
            print(f"indexed {indexed}/{doc_count} documents", flush=True)

        ensure_index(client, settings.index_name)
        client.wait_task(client.swap_indexes(settings.index_name, shadow), timeout_seconds=1800)
        swapped = True
        delete_index_if_exists(client, shadow)
    finally:
        if not swapped:
            try:
                delete_index_if_exists(client, shadow)
            except Exception as exc:  # noqa: BLE001
                print(f"failed to clean shadow index {shadow}: {exc}", file=sys.stderr)
        client.close()


def ingest_once(settings: Settings, force: bool = False) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    started_at = now_iso()
    last_state = load_state(settings)
    state = {
        "status": "running",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": None,
        "source_commit": None,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "documents": 0,
        "files_seen": 0,
        "errors": 0,
    }
    write_state(settings, state)

    staging_path: Path | None = None
    try:
        source_commit = prepare_source(settings)
        if (
            not force
            and not settings.include_file_list
            and source_commit
            and last_state.get("status") == "succeeded"
            and last_state.get("source_commit") == source_commit
            and last_state.get("index_schema_version") == INDEX_SCHEMA_VERSION
        ):
            state.update(
                {
                    "status": "skipped",
                    "source_commit": source_commit,
                    "index_schema_version": INDEX_SCHEMA_VERSION,
                    "finished_at": now_iso(),
                    "message": "source commit and index schema unchanged",
                }
            )
            write_state(settings, state)
            return state

        state["source_commit"] = source_commit
        write_state(settings, state)
        staging_path, doc_count, files_seen, errors = build_staging(settings, run_id)
        publish_to_meili(settings, staging_path, run_id, doc_count)
        state.update(
            {
                "status": "succeeded",
                "finished_at": now_iso(),
                "source_commit": source_commit,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "documents": doc_count,
                "files_seen": files_seen,
                "errors": errors,
            }
        )
        write_state(settings, state)
        return state
    except Exception as exc:
        state.update({"status": "failed", "finished_at": now_iso(), "error": str(exc)})
        write_state(settings, state)
        raise
    finally:
        if staging_path and staging_path.exists():
            staging_path.unlink()
            wal = staging_path.with_suffix(staging_path.suffix + "-wal")
            shm = staging_path.with_suffix(staging_path.suffix + "-shm")
            for extra in (wal, shm):
                if extra.exists():
                    extra.unlink()


def schedule(settings: Settings) -> None:
    should_run = settings.ingest_run_on_start
    while True:
        if should_run:
            try:
                ingest_once(settings)
            except Exception:
                traceback.print_exc()
        should_run = True
        print(f"sleeping {settings.ingest_interval_seconds} seconds before next ingest", flush=True)
        time.sleep(settings.ingest_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="PtGen works ingester")
    subparsers = parser.add_subparsers(dest="command")
    once = subparsers.add_parser("once")
    once.add_argument("--force", action="store_true")
    subparsers.add_parser("schedule")
    args = parser.parse_args()

    settings = get_settings()
    if args.command == "schedule":
        schedule(settings)
    else:
        ingest_once(settings, force=bool(getattr(args, "force", False)))


if __name__ == "__main__":
    main()
