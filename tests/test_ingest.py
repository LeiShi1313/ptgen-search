from __future__ import annotations

import json
import sqlite3
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ptgen_search import api as api_module
from ptgen_search.api import build_filter, is_missing_index_error, lookup_document_id
from ptgen_search.ids import source_document_id
from ptgen_search.ingest import normalize_work, upsert_staged
from ptgen_search.meili_client import MeiliError


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
        self.assertEqual(doc["kind"], "work")

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
        self.assertEqual(doc["kind"], "work")

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

        self.assertEqual(build_filter("douban", "work", 2011), ['sources = "douban"', 'kind = "work"', "year = 2011"])

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

    def test_document_fetch_encodes_document_id(self) -> None:
        paths = []
        client = object.__new__(api_module.MeiliClient)

        def request(method: str, path: str, **kwargs: object) -> dict:
            paths.append((method, path))
            return {}

        client.request = request
        client.document("works", "source/id with space")

        self.assertEqual(paths, [("GET", "/indexes/works/documents/source%2Fid%20with%20space")])

    def test_missing_index_error_is_detected(self) -> None:
        self.assertTrue(is_missing_index_error(MeiliError('GET failed: {"code":"index_not_found"}')))


if __name__ == "__main__":
    unittest.main()
