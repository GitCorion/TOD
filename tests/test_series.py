import sqlite3
from pathlib import Path

from catalogo.backend import select_target
from catalogo.bulk_sync import matches_asset, sync_sources
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeProwlarr:
    def __init__(self, results):
        self.results = results
        self.searches = []

    def indexers(self):
        return [
            {"id": 1, "name": "TrackerEspanol (API)", "language": "es-ES"},
            {"id": 5, "name": "TrackerEspanol2 (API)", "language": "es-ES"},
        ]

    def search(self, query, *, media_type="movie", limit=50, indexer_ids=None):
        assert media_type == "series"
        self.searches.append((query, tuple(indexer_ids or ())))
        return self.results


def seed_series(tmp_path, episodes=(4, 5)):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    for episode in episodes:
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,original_title,year,"
            "season,episode,popularity) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"series:tmdb:111110:1:{episode}",
                "series",
                111110,
                "ONE PIECE",
                "ONE PIECE",
                2023,
                1,
                episode,
                50.0,
            ),
        )
    conn.commit()
    conn.close()
    return db


def asset(db, episode=5):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM media_items WHERE key = ?",
        (f"series:tmdb:111110:1:{episode}",),
    ).fetchone()
    conn.close()
    return row


def pack_result(**overrides):
    base = {
        "title": (
            "ONE PIECE (2023) S01 [PACK][NF WEB-DL 1080p AVC ES DD+ "
            "Atmos 5.1][HDO] SPANiSH"
        ),
        "size": 12 * 1024**3,
        "seeders": 20,
        "peers": 1,
        "indexer": "TrackerEspanol (API)",
        "guid": "pack-one-piece-s01",
        "infoHash": "packhash",
        "tmdbId": 111110,
        "downloadUrl": "http://prowlarr/1",
    }
    base.update(overrides)
    return base


def test_pack_matches_midseason_episode():
    row = {
        "tmdb": 111110,
        "title": "ONE PIECE",
        "original_title": "ONE PIECE",
        "year": 2023,
        "season": 1,
        "episode": 5,
    }
    assert matches_asset(row, pack_result(), kind="series")
    assert not matches_asset(
        row, pack_result(title="ONE PIECE (2023) S02 [PACK]"), kind="series"
    )
    assert not matches_asset(
        row,
        pack_result(title="ONE PIECE (2023) [SEGUNDA TEMPORADA] [PACK]"),
        kind="series",
    )
    assert matches_asset(
        row,
        pack_result(title="ONE PIECE (2023) Complete Series 1080p"),
        kind="series",
    )


class IdSeasonProwlarr:
    """Búsqueda por TMDB que devuelve packs de varias temporadas."""

    def indexers(self):
        return [
            {
                "id": 1,
                "name": "TrackerEspanol (API)",
                "capabilities": {"tvSearchParams": ["tmdbid"]},
            }
        ]

    def torznab_search(self, indexer_id, **kwargs):
        assert kwargs["tmdb_id"] == 111110
        return [
            pack_result(guid="s01", infoHash="s01"),
            pack_result(
                title="ONE PIECE (2023) S02 [PACK]",
                guid="s02",
                infoHash="s02",
            ),
        ]

    def search(self, query, **kwargs):
        return []


def test_id_search_rejects_packs_from_other_seasons(tmp_path):
    db = seed_series(tmp_path, episodes=(1,))
    report = sync_sources(
        db,
        IdSeasonProwlarr(),
        kind="series",
        limit=1,
        delay=0,
        key="series:tmdb:111110:1:1",
        id_fallback=True,
    )
    assert report["accepted"] == 1
    assert report["inserted"] == 1
    conn = sqlite3.connect(db)
    titles = [row[0] for row in conn.execute("SELECT title FROM releases")]
    conn.close()
    assert len(titles) == 1
    assert "S01" in titles[0]


def test_anime_is_rejected_by_tmdb_id():
    row = {
        "tmdb": 111110,
        "title": "ONE PIECE",
        "original_title": "ONE PIECE",
        "year": 2023,
        "season": 1,
        "episode": 5,
    }
    anime = pack_result(
        title="One Piece (1999) S01 [PACK][NF WEB-DL 1080p]",
        tmdbId=37854,
        guid="anime",
    )
    assert not matches_asset(row, anime, kind="series")


def test_anime_is_rejected_by_year_without_tmdb():
    row = {
        "tmdb": 111110,
        "title": "ONE PIECE",
        "original_title": "ONE PIECE",
        "year": 2023,
        "season": 1,
        "episode": 5,
    }
    anime = pack_result(
        title="One Piece (1999) S01 [PACK][NF WEB-DL 1080p]",
        tmdbId=None,
        guid="anime2",
    )
    assert not matches_asset(row, anime, kind="series")


def test_exact_episode_must_match_season_and_episode():
    row = {
        "tmdb": 111110,
        "title": "ONE PIECE",
        "original_title": "ONE PIECE",
        "year": 2023,
        "season": 1,
        "episode": 5,
    }
    assert matches_asset(
        row, pack_result(title="ONE PIECE S01E05 1080p"), kind="series"
    )
    assert not matches_asset(
        row, pack_result(title="ONE PIECE S01E04 1080p"), kind="series"
    )


def test_select_target_picks_midseason_file_in_pack():
    files = [
        {"index": index, "name": f"ONE PIECE S01E{index + 1:02d}.mkv",
         "size": 1000}
        for index in range(8)
    ]
    target = select_target(
        files, {"kind": "series", "season": 1, "episode": 5}
    )
    assert target["index"] == 4
    assert target["name"].endswith("S01E05.mkv")


def test_series_pack_inserts_one_release_per_episode(tmp_path):
    db = seed_series(tmp_path, episodes=(4, 5))
    client = FakeProwlarr([pack_result()])
    report = sync_sources(
        db, client, kind="series", limit=5, delay=0
    )
    assert report["assets_checked"] == 2
    assert report["inserted"] == 2

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM releases ORDER BY asset_id"
    ).fetchall()
    assert [row["is_pack"] for row in rows] == [1, 1]
    assert len({row["download_url"] for row in rows}) == 1
    hashes = [row["info_hash"] for row in rows]
    assert hashes.count("packhash") == 1
    assert hashes.count(None) == 1
    assert rows[0]["guid"] != rows[1]["guid"]
    conn.close()
