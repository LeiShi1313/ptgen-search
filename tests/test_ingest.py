from __future__ import annotations

import unittest

from fastapi import HTTPException

from ptgen_search.api import build_filter, is_missing_index_error
from ptgen_search.meili_client import MeiliError
from ptgen_search.ingest import merge_docs, normalize_work


class NormalizeWorkTests(unittest.TestCase):
    def test_douban_work_uses_imdb_cluster_and_people(self) -> None:
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

        self.assertEqual(doc["id"], "work_imdb_tt1741246")
        self.assertIn("花蕾", doc["titles"])
        self.assertIn("Flower Buds", doc["titles"])
        self.assertIn("齐德内克·吉拉斯基 Zdenek Jirasky", doc["directors"])
        self.assertIn("Actor One", doc["people"])
        self.assertEqual(doc["poster"], "https://example.test/poster.jpg")
        self.assertEqual(doc["kind"], "work")

    def test_imdb_work_keeps_tt_sid_and_map_link(self) -> None:
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

        self.assertEqual(doc["id"], "work_imdb_tt0199626")
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

    def test_merge_combines_source_metadata(self) -> None:
        left = {
            "id": "work_imdb_tt1",
            "sources": ["douban"],
            "source_ids": {"douban": "1", "imdb": "tt1"},
            "source_paths": {"douban": "douban/1.json"},
            "titles": ["中文名"],
            "aliases": [],
            "people": ["Director"],
            "directors": ["Director"],
            "writers": [],
            "cast": [],
            "staff": [],
            "developers": [],
            "publishers": [],
            "genres": ["剧情"],
            "tags": [],
            "regions": [],
            "languages": [],
            "description": "short",
        }
        right = {
            "id": "work_imdb_tt1",
            "sources": ["imdb"],
            "source_ids": {"imdb": "tt1"},
            "source_paths": {"imdb": "imdb/1.json"},
            "titles": ["English Title"],
            "aliases": ["Alias"],
            "people": ["Actor"],
            "directors": [],
            "writers": [],
            "cast": ["Actor"],
            "staff": [],
            "developers": [],
            "publishers": [],
            "genres": ["Drama"],
            "tags": [],
            "regions": [],
            "languages": [],
            "description": "longer description",
        }

        merged = merge_docs(left, right)

        self.assertEqual(merged["sources"], ["douban", "imdb"])
        self.assertIn("中文名", merged["titles"])
        self.assertIn("English Title", merged["titles"])
        self.assertIn("Actor", merged["people"])
        self.assertEqual(merged["description"], "longer description")

    def test_filter_rejects_injection(self) -> None:
        with self.assertRaises(HTTPException):
            build_filter('nosuch" OR sources = "douban', None, None)

        self.assertEqual(build_filter("douban", "work", 2011), ['sources = "douban"', 'kind = "work"', "year = 2011"])

    def test_missing_index_error_is_detected(self) -> None:
        self.assertTrue(is_missing_index_error(MeiliError('GET failed: {"code":"index_not_found"}')))


if __name__ == "__main__":
    unittest.main()
