from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ptgen_search import api as api_module
from ptgen_search.api import build_filter, build_search_fields, is_missing_index_error, lookup_document_id
from ptgen_search.config import Settings
from ptgen_search.ids import source_document_id
from ptgen_search.ingest import build_staging, load_imdb_kind_map, normalize_work, upsert_staged
from ptgen_search.meili_client import MeiliError
from ptgen_search.posters import PosterCache


class NormalizeWorkTests(unittest.TestCase):
    def test_douban_work_uses_source_id_and_people(self) -> None:
        data = {
            "sid": "10000794",
            "site": "douban",
            "chinese_title": "花蕾",
            "foreign_title": "Poupata",
            "trans_title": ["Flower Buds"],
            "year": "2011",
            "genre": ["剧情"],
            "director": [{"name": "齐德内克·吉拉斯基 Zdenek Jirasky", "url": "/celebrity/1332611/"}],
            "cast": [{"name": "Actor One"}],
            "imdb_id": "tt1741246",
            "poster": "https://example.test/poster.jpg",
            "introduction": "A Czech social drama.",
        }

        doc = normalize_work("douban", "10000794", data, "douban/10000794.json", {}, {})

        self.assertEqual(doc["id"], "douban-10000794")
        self.assertEqual(doc["source_ids"]["imdb"], "tt1741246")
        self.assertIn("花蕾", doc["titles"])
        self.assertIn("Flower Buds", doc["titles"])
        self.assertIn("齐德内克·吉拉斯基 Zdenek Jirasky", doc["directors"])
        self.assertIn("Actor One", doc["people"])
        self.assertEqual(doc["poster"], "https://example.test/poster.jpg")
        self.assertEqual(doc["kind"], "movie")

    def test_imdb_work_uses_source_id_and_map_link(self) -> None:
        data = {
            "sid": "tt0199626",
            "site": "imdb",
            "name": "In the Cut",
            "actors": [{"name": "Meg Ryan"}],
            "genre": ["Mystery"],
        }

        doc = normalize_work(
            "imdb",
            "tt0199626",
            data,
            "imdb/199626.json",
            {},
            {"tt0199626": "1309163"},
        )

        self.assertEqual(doc["id"], "imdb-tt0199626")
        self.assertEqual(doc["source_ids"]["douban"], "1309163")
        self.assertIn("Meg Ryan", doc["people"])
        self.assertEqual(doc["kind"], "movie")

    def test_imdb_movie_kind_is_normalized(self) -> None:
        doc = normalize_work(
            "imdb",
            "tt1",
            {"sid": "tt1", "site": "imdb", "name": "A Film", "@type": "Movie"},
            "imdb/1.json",
            {},
            {},
        )

        self.assertEqual(doc["kind"], "movie")

    def test_imdb_tv_kind_is_normalized(self) -> None:
        series = normalize_work(
            "imdb",
            "tt2",
            {"sid": "tt2", "site": "imdb", "name": "A Series", "@type": "TVSeries"},
            "imdb/2.json",
            {},
            {},
        )
        episode = normalize_work(
            "imdb",
            "tt3",
            {"sid": "tt3", "site": "imdb", "name": "An Episode", "@type": "TVEpisode"},
            "imdb/3.json",
            {},
            {},
        )

        self.assertEqual(series["kind"], "tv")
        self.assertEqual(episode["kind"], "tv")

    def test_douban_kind_uses_linked_imdb_type_when_available(self) -> None:
        movie = normalize_work(
            "douban",
            "10000794",
            {"sid": "10000794", "site": "douban", "name": "A Film", "imdb_id": "tt1741246"},
            "douban/10000794.json",
            {},
            {},
            {"tt1741246": "movie"},
        )
        tv = normalize_work(
            "douban",
            "10000801",
            {"sid": "10000801", "site": "douban", "name": "A Series"},
            "douban/10000801.json",
            {"10000801": "tt2091334"},
            {},
            {"tt2091334": "tv"},
        )

        self.assertEqual(movie["kind"], "movie")
        self.assertEqual(tv["kind"], "tv")

    def test_douban_kind_is_guessed_when_linked_imdb_type_is_missing(self) -> None:
        movie = normalize_work(
            "douban",
            "10000794",
            {"sid": "10000794", "site": "douban", "name": "Ambiguous", "imdb_id": "tt1741246"},
            "douban/10000794.json",
            {},
            {},
            {},
        )
        tv = normalize_work(
            "douban",
            "10001417",
            {"sid": "10001417", "site": "douban", "name": "A Series", "episodes": "20"},
            "douban/10001417.json",
            {},
            {},
            {},
        )

        self.assertEqual(movie["kind"], "movie")
        self.assertEqual(tv["kind"], "tv")

    def test_douban_kind_guess_uses_season_titles(self) -> None:
        doc = normalize_work(
            "douban",
            "10481125",
            {"sid": "10481125", "site": "douban", "name": "The Ellen DeGeneres Show Season 9"},
            "douban/10481125.json",
            {},
            {},
            {},
        )

        self.assertEqual(doc["kind"], "tv")

    def test_load_imdb_kind_map_reads_imdb_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            imdb_dir = Path(tmp) / "imdb"
            imdb_dir.mkdir()
            (imdb_dir / "tt1.json").write_text(
                json.dumps({"sid": "tt1", "@type": "Movie"}),
                encoding="utf-8",
            )
            (imdb_dir / "tt2.json").write_text(
                json.dumps({"sid": "tt2", "@type": "TVSeries"}),
                encoding="utf-8",
            )
            (imdb_dir / "tt3.json").write_text(
                json.dumps({"sid": "tt3"}),
                encoding="utf-8",
            )

            kinds = load_imdb_kind_map(Path(tmp))

        self.assertEqual(kinds, {"tt0000001": "movie", "tt0000002": "tv"})

    def test_build_staging_applies_imdb_kind_to_douban_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ptgen"
            state_dir = Path(tmp) / "state"
            (root / "douban").mkdir(parents=True)
            (root / "imdb").mkdir()
            (root / "internal_map").mkdir()
            (root / "douban" / "1.json").write_text(
                json.dumps({"sid": "1", "site": "douban", "chinese_title": "Mapped Movie"}),
                encoding="utf-8",
            )
            (root / "douban" / "2.json").write_text(
                json.dumps({"sid": "2", "site": "douban", "chinese_title": "Direct TV", "imdb_id": "tt2"}),
                encoding="utf-8",
            )
            (root / "imdb" / "tt1.json").write_text(
                json.dumps({"sid": "tt1", "site": "imdb", "name": "Mapped Movie", "@type": "Movie"}),
                encoding="utf-8",
            )
            (root / "imdb" / "tt2.json").write_text(
                json.dumps({"sid": "tt2", "site": "imdb", "name": "Direct TV", "@type": "TVSeries"}),
                encoding="utf-8",
            )
            (root / "internal_map" / "douban_imdb_map.json").write_text(
                json.dumps([{"dbid": "1", "imdbid": "tt1"}]),
                encoding="utf-8",
            )
            settings = Settings(
                ptgen_path=root,
                state_dir=state_dir,
                include_files="douban/1.json,douban/2.json",
            )

            staging_path, doc_count, files_seen, errors = build_staging(settings, "kindtest")
            conn = sqlite3.connect(staging_path)
            try:
                rows = {
                    row_id: json.loads(raw_doc)
                    for row_id, raw_doc in conn.execute("select id, doc from works order by id")
                }
            finally:
                conn.close()

        self.assertEqual(doc_count, 2)
        self.assertEqual(files_seen, 2)
        self.assertEqual(errors, 0)
        self.assertEqual(rows["douban-1"]["kind"], "movie")
        self.assertEqual(rows["douban-1"]["source_ids"], {"douban": "1", "imdb": "tt0000001"})
        self.assertEqual(rows["douban-2"]["kind"], "tv")
        self.assertEqual(rows["douban-2"]["source_ids"], {"douban": "2", "imdb": "tt0000002"})

    def test_bangumi_cast_uses_actor_names_not_character_names(self) -> None:
        doc = normalize_work(
            "bangumi",
            "13",
            {
                "sid": "13",
                "site": "bangumi",
                "name": "CLANNAD",
                "platform": "游戏",
                "cast": [{"name": "古河渚", "actors": [{"name": "中原麻衣"}]}],
                "staff": [{"key": "开发", "value": "Key"}, {"key": "音乐", "value": "麻枝准、折戸伸治"}],
            },
            "bangumi/13.json",
            {},
            {},
        )

        self.assertEqual(doc["kind"], "game")
        self.assertIn("中原麻衣", doc["people"])
        self.assertNotIn("古河渚", doc["people"])
        self.assertNotIn("开发", doc["people"])
        self.assertIn("麻枝准", doc["people"])

    def test_mapped_douban_and_imdb_records_stay_separate(self) -> None:
        douban = normalize_work(
            "douban",
            "1",
            {"sid": "1", "site": "douban", "name": "中文名", "imdb_id": "tt1"},
            "douban/1.json",
            {},
            {},
        )
        imdb = normalize_work(
            "imdb",
            "tt1",
            {"sid": "tt1", "site": "imdb", "name": "English Title"},
            "imdb/tt1.json",
            {},
            {"tt0000001": "1"},
        )

        self.assertEqual(douban["id"], "douban-1")
        self.assertEqual(imdb["id"], "imdb-tt0000001")
        self.assertEqual(douban["sources"], ["douban"])
        self.assertEqual(imdb["sources"], ["imdb"])
        self.assertEqual(douban["source_ids"], {"douban": "1", "imdb": "tt0000001"})
        self.assertEqual(imdb["source_ids"], {"imdb": "tt0000001", "douban": "1"})

    def test_duplicate_source_ids_choose_better_record(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("create table works(id text primary key, doc text not null)")
        older = {
            "id": "imdb-tt0851578",
            "sources": ["imdb"],
            "source_ids": {"imdb": "tt0851578"},
            "source_paths": {"imdb": "imdb/tt851578.json"},
            "titles": ["Older"],
            "aliases": [],
            "people": [],
            "directors": [],
            "writers": [],
            "cast": [],
            "staff": [],
            "developers": [],
            "publishers": [],
            "genres": [],
            "tags": [],
            "regions": [],
            "languages": [],
            "updated_at": "2025-01-01T00:00:00",
            "rating_votes": 1,
        }
        newer = {
            **older,
            "titles": ["Newer"],
            "source_paths": {"imdb": "imdb/tt0851578.json"},
            "updated_at": "2026-01-01T00:00:00",
            "rating_votes": 20,
        }

        upsert_staged(conn, older)
        upsert_staged(conn, newer)
        upsert_staged(conn, older)

        raw = conn.execute("select doc from works where id = ?", ("imdb-tt0851578",)).fetchone()[0]
        try:
            self.assertEqual(json.loads(raw)["titles"], ["Newer"])
        finally:
            conn.close()

    def test_filter_rejects_injection(self) -> None:
        with self.assertRaises(HTTPException):
            build_filter('nosuch" OR sources = "douban', None, None)
        with self.assertRaises(HTTPException):
            build_filter(None, "work", None)

        self.assertEqual(build_filter("douban", "movie", 2011), ['sources = "douban"', 'kind = "movie"', "year = 2011"])

    def test_search_fields_are_allow_listed(self) -> None:
        self.assertIsNone(build_search_fields(None))
        self.assertIsNone(build_search_fields("all"))
        self.assertEqual(build_search_fields("titles,aliases"), ["titles", "aliases"])
        self.assertEqual(build_search_fields("title_aliases"), ["titles", "aliases"])
        self.assertEqual(
            build_search_fields("people"),
            ["people", "directors", "writers", "cast", "staff", "developers", "publishers"],
        )
        self.assertEqual(build_search_fields("source_ids,metadata"), ["source_ids", "genres", "tags", "description"])

        with self.assertRaises(HTTPException):
            build_search_fields('titles,description" OR kind = "movie')

    def test_source_document_id_uses_source_prefix(self) -> None:
        self.assertEqual(source_document_id("douban", "1291843"), "douban-1291843")
        self.assertEqual(source_document_id("imdb", "0133093"), "imdb-tt0133093")
        self.assertEqual(source_document_id("imdb", "tt0133093"), "imdb-tt0133093")
        self.assertEqual(source_document_id("imdb", "851578"), "imdb-tt0851578")

        with self.assertRaises(HTTPException):
            lookup_document_id("tmdb", "603")

    def test_lookup_route_uses_exact_source_document_id(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.document_id = ""

            def document(self, index_name: str, document_id: str) -> dict:
                self.document_id = document_id
                return {"id": document_id, "index": index_name}

        fake = FakeClient()
        previous = api_module.client
        api_module.client = fake
        try:
            response = TestClient(api_module.app).get("/api/lookup", params={"source": "imdb", "id": "851578"})
        finally:
            api_module.client = previous

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "imdb-tt0851578")
        self.assertEqual(fake.document_id, "imdb-tt0851578")

    def test_search_route_scopes_fields(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.payload: dict = {}

            def search(self, index_name: str, payload: dict) -> dict:
                self.payload = payload
                return {
                    "hits": [],
                    "estimatedTotalHits": 0,
                    "limit": payload["limit"],
                    "offset": payload["offset"],
                    "processingTimeMs": 0,
                    "query": payload["q"],
                }

        fake = FakeClient()
        previous = api_module.client
        api_module.client = fake
        try:
            response = TestClient(api_module.app).get(
                "/api/search",
                params={"q": "matrix", "fields": "title_aliases", "kind": "movie"},
            )
        finally:
            api_module.client = previous

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.payload["attributesToSearchOn"], ["titles", "aliases"])
        self.assertEqual(fake.payload["filter"], ['kind = "movie"'])

    def test_document_fetch_encodes_document_id(self) -> None:
        paths = []
        client = object.__new__(api_module.MeiliClient)

        def request(method: str, path: str, **kwargs: object) -> dict:
            paths.append((method, path))
            return {}

        client.request = request
        client.document("works", "source/id with space")

        self.assertEqual(paths, [("GET", "/indexes/works/documents/source%2Fid%20with%20space")])

    def test_poster_cache_rewrites_allowed_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PosterCache(Path(tmp), max_bytes=1000, timeout_seconds=1, failure_ttl_seconds=60)
            doc = {
                "id": "douban-1",
                "poster": "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p1.webp",
                "_formatted": {"poster": "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p1.webp"},
            }

            proxied = cache.proxy_document(doc, "https://ptgen.test")

            self.assertEqual(doc["poster"], "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p1.webp")
            self.assertEqual(proxied["poster"], doc["poster"])
            self.assertTrue(proxied["poster_ptgen"].startswith("https://ptgen.test/api/posters/"))
            self.assertNotIn("poster_original", proxied)
            self.assertTrue(proxied["_formatted"]["poster_ptgen"].startswith("https://ptgen.test/api/posters/"))

    def test_poster_cache_does_not_proxy_disallowed_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PosterCache(Path(tmp), max_bytes=1000, timeout_seconds=1, failure_ttl_seconds=60)
            doc = {"id": "other-1", "poster": "https://example.test/poster.jpg"}

            self.assertIs(cache.register_url(doc["poster"]), None)
            self.assertEqual(cache.proxy_document(doc), doc)

    def test_poster_route_serves_cached_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PosterCache(Path(tmp), max_bytes=1000, timeout_seconds=1, failure_ttl_seconds=60)
            url = "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p1.webp"
            key = cache.register_url(url)
            assert key is not None
            file_name = f"{key}.webp"
            path = cache.files_dir / file_name
            path.write_bytes(b"RIFFxxxxWEBP")
            with cache.db() as conn:
                conn.execute(
                    """
                    update posters
                    set status = 'cached',
                        content_type = 'image/webp',
                        file_name = ?,
                        size = 12,
                        fetched_at = 1
                    where key = ?
                    """,
                    (file_name, key),
                )

            previous = api_module.poster_cache
            api_module.poster_cache = cache
            try:
                response = TestClient(api_module.app).get(f"/api/posters/{key}")
            finally:
                api_module.poster_cache = previous

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "image/webp")
            self.assertEqual(response.content, b"RIFFxxxxWEBP")

    def test_missing_index_error_is_detected(self) -> None:
        self.assertTrue(is_missing_index_error(MeiliError('GET failed: {"code":"index_not_found"}')))


if __name__ == "__main__":
    unittest.main()
