import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.library_index import build_library_index, in_library
from catalogo.prune import prune_library
from catalogo.strm import export_strm

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def seed_catalog(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test Movie", 2020),
    )
    movie_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (movie_id, "TrackerEspanol2", "Test.Movie.mkv", "alive", 10),
    )
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
        "VALUES(?,?,?,?,?,?,?)",
        ("series:tmdb:2:1:3", "series", 2, "Serie", 2021, 1, 3),
    )
    series_id = conn.execute(
        "SELECT id FROM media_items WHERE kind='series'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (series_id, "TrackerEspanol2", "Serie.S01E03.mkv", "alive", 12),
    )
    conn.commit()
    conn.close()
    return db


def make_radarr(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE MovieMetadata (Id INTEGER PRIMARY KEY, TmdbId INTEGER);
        CREATE TABLE Movies (Id INTEGER PRIMARY KEY, MovieMetadataId INTEGER,
                             MovieFileId INTEGER);
        INSERT INTO MovieMetadata VALUES (1, 1);
        INSERT INTO Movies VALUES (1, 1, 1);
        """
    )
    conn.commit()
    conn.close()


def make_sonarr(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE Series (Id INTEGER PRIMARY KEY, TmdbId INTEGER);
        CREATE TABLE Episodes (Id INTEGER PRIMARY KEY, SeriesId INTEGER,
                               SeasonNumber INTEGER, EpisodeNumber INTEGER,
                               EpisodeFileId INTEGER);
        INSERT INTO Series VALUES (1, 2);
        INSERT INTO Episodes VALUES (1, 1, 1, 3, 1);
        """
    )
    conn.commit()
    conn.close()


def test_library_index_detects_movie_and_episode(tmp_path):
    radarr = tmp_path / "radarr.db"
    sonarr = tmp_path / "sonarr.db"
    make_radarr(radarr)
    make_sonarr(sonarr)
    index = build_library_index(radarr, sonarr)
    assert index.movies == {1}
    assert index.episodes == {(2, 1, 3)}
    assert in_library(
        index,
        {"kind": "movie", "tmdb": 1, "season": None, "episode": None},
    )
    assert in_library(
        index,
        {"kind": "series", "tmdb": 2, "season": 1, "episode": 3},
    )
    assert not in_library(
        index,
        {"kind": "series", "tmdb": 2, "season": 1, "episode": 4},
    )


def test_export_skips_library_and_prune_removes_strm(tmp_path):
    db = seed_catalog(tmp_path)
    radarr = tmp_path / "radarr.db"
    sonarr = tmp_path / "sonarr.db"
    make_radarr(radarr)
    make_sonarr(sonarr)
    out = tmp_path / "strm"

    first = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert first["written"] == 2
    assert (out / "movies" / "Test Movie (2020)").is_dir()
    assert (out / "series" / "Serie (2021)").is_dir()

    skipped = export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        skip_library=True, radarr_db=radarr, sonarr_db=sonarr,
    )
    assert skipped["skipped"] == 2
    assert skipped["skipped_reasons"]["in_library"] == 2

    report = prune_library(
        db, out, radarr_db=radarr, sonarr_db=sonarr, dry_run=False
    )
    assert report["assets_in_library"] == 2
    assert report["removed_count"] == 2
    assert not list(out.rglob("*.strm"))


def test_restore_readds_strm_if_library_copy_disappears(tmp_path):
    db = seed_catalog(tmp_path)
    radarr = tmp_path / "radarr.db"
    sonarr = tmp_path / "sonarr.db"
    make_radarr(radarr)
    make_sonarr(sonarr)
    out = tmp_path / "strm"
    export_strm(db, out, base_url="http://127.0.0.1:8773")
    prune_library(db, out, radarr_db=radarr, sonarr_db=sonarr, dry_run=False)
    assert not list(out.rglob("*.strm"))

    conn = sqlite3.connect(radarr)
    conn.execute("UPDATE Movies SET MovieFileId=0")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(sonarr)
    conn.execute("UPDATE Episodes SET EpisodeFileId=0")
    conn.commit()
    conn.close()

    restored = export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        skip_library=True, radarr_db=radarr, sonarr_db=sonarr,
    )
    assert restored["written"] == 2
    assert (out / "movies" / "Test Movie (2020)").is_dir()
    assert (out / "series" / "Serie (2021)").is_dir()
