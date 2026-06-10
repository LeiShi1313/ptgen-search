from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx

ALLOWED_POSTER_HOSTS = {
    "img1.doubanio.com",
    "img2.doubanio.com",
    "img3.doubanio.com",
    "img9.doubanio.com",
    "m.media-amazon.com",
}
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
POSTER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://movie.douban.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class PosterError(RuntimeError):
    pass


class PosterNotFound(PosterError):
    pass


class PosterCache:
    def __init__(
        self,
        cache_dir: Path,
        max_bytes: int,
        timeout_seconds: int,
        failure_ttl_seconds: int,
    ) -> None:
        self.cache_dir = cache_dir
        self.files_dir = cache_dir / "files"
        self.db_path = cache_dir / "posters.sqlite"
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.failure_ttl_seconds = failure_ttl_seconds

    def setup(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.execute(
                """
                create table if not exists posters(
                    key text primary key,
                    original_url text not null,
                    status text not null default 'pending',
                    content_type text,
                    file_name text,
                    size integer,
                    fetched_at integer,
                    failed_at integer,
                    error text
                )
                """
            )

    def connect(self) -> sqlite3.Connection:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def key_for_url(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in ALLOWED_POSTER_HOSTS

    def register_url(self, url: str) -> str | None:
        if not self.is_allowed_url(url):
            return None
        key = self.key_for_url(url)
        self.setup()
        with self.db() as conn:
            conn.execute(
                """
                insert into posters(key, original_url)
                values(?, ?)
                on conflict(key) do nothing
                """,
                (key, url),
            )
        return key

    def poster_url(self, url: str, public_base_url: str = "") -> str | None:
        key = self.register_url(url)
        if not key:
            return None
        path = f"/api/posters/{key}"
        if public_base_url:
            return f"{public_base_url}{path}"
        return path

    def proxy_document(self, doc: dict[str, Any], public_base_url: str = "") -> dict[str, Any]:
        poster = doc.get("poster")
        if not isinstance(poster, str):
            return doc
        proxy_url = self.poster_url(poster, public_base_url)
        if not proxy_url:
            return doc
        copied = dict(doc)
        copied["poster_ptgen"] = proxy_url
        formatted = copied.get("_formatted")
        if isinstance(formatted, dict) and isinstance(formatted.get("poster"), str):
            copied["_formatted"] = {**formatted, "poster_ptgen": proxy_url}
        return copied

    def proxy_search_result(self, result: dict[str, Any], public_base_url: str = "") -> dict[str, Any]:
        hits = result.get("hits")
        if not isinstance(hits, list):
            return result
        copied = dict(result)
        copied["hits"] = [
            self.proxy_document(hit, public_base_url) if isinstance(hit, dict) else hit
            for hit in hits
        ]
        return copied

    def get_cached_file(self, key: str) -> tuple[Path, str] | None:
        self.setup()
        with self.db() as conn:
            row = conn.execute(
                "select status, content_type, file_name from posters where key = ?",
                (key,),
            ).fetchone()
        if not row:
            raise PosterNotFound("unknown poster")
        status, content_type, file_name = row
        if status != "cached" or not content_type or not file_name:
            return None
        path = self.files_dir / file_name
        if not path.exists():
            return None
        return path, content_type

    def get_or_fetch(self, key: str) -> tuple[Path, str]:
        cached = self.get_cached_file(key)
        if cached:
            return cached

        self.setup()
        with self.db() as conn:
            row = conn.execute(
                "select original_url, status, failed_at from posters where key = ?",
                (key,),
            ).fetchone()
        if not row:
            raise PosterNotFound("unknown poster")

        original_url, status, failed_at = row
        if status == "failed" and failed_at and int(time.time()) - int(failed_at) < self.failure_ttl_seconds:
            raise PosterNotFound("poster fetch failed recently")

        return self.fetch_and_store(key, original_url)

    def fetch_and_store(self, key: str, original_url: str) -> tuple[Path, str]:
        if not self.is_allowed_url(original_url):
            self.mark_failed(key, "poster host not allowed")
            raise PosterNotFound("poster host not allowed")

        temp_path = self.files_dir / f"{key}.tmp"
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                with client.stream("GET", original_url, headers=POSTER_HEADERS) as response:
                    if response.status_code >= 400:
                        raise PosterError(f"poster fetch returned {response.status_code}")
                    if not self.is_allowed_url(str(response.url)):
                        raise PosterError("poster redirect host not allowed")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    extension = IMAGE_EXTENSIONS.get(content_type)
                    if not extension:
                        raise PosterError(f"unsupported poster content type {content_type!r}")
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise PosterError("poster too large")

                    total = 0
                    with temp_path.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > self.max_bytes:
                                raise PosterError("poster too large")
                            handle.write(chunk)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink()
            self.mark_failed(key, str(exc))
            raise PosterNotFound(str(exc)) from exc

        file_name = f"{key}{extension}"
        final_path = self.files_dir / file_name
        os.replace(temp_path, final_path)
        with self.db() as conn:
            conn.execute(
                """
                update posters
                set status = 'cached',
                    content_type = ?,
                    file_name = ?,
                    size = ?,
                    fetched_at = ?,
                    failed_at = null,
                    error = null
                where key = ?
                """,
                (content_type, file_name, final_path.stat().st_size, int(time.time()), key),
            )
        return final_path, content_type

    def mark_failed(self, key: str, error: str) -> None:
        with self.db() as conn:
            conn.execute(
                """
                update posters
                set status = 'failed',
                    failed_at = ?,
                    error = ?
                where key = ?
                """,
                (int(time.time()), error[:500], key),
            )
