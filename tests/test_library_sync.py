import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.library_sync import sync_library

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def make_radarr(path, root):
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE MovieMetadata (Id INTEGER PRIMARY KEY, TmdbId INTEGER);
        CREATE TABLE Movies (Id INTEGER PRIMARY KEY, MovieMetadataId INTEGER,
                             MovieFileId INTEGER, Path TEXT);
        CREATE TABLE MovieFiles (Id INTEGER PRIMARY KEY, MovieId INTEGER,
                                 RelativePath TEXT, Size INTEGER);
        INSERT INTO MovieMetadata VALUES (1, 100);
        INSERT INTO Movies VALUES (1, 1, 1, '{root}/Peli (2024)');
        INSERT INTO MovieFiles VALUES (1, 1, 'Peli (2024) WEBDL-1080p.mkv', 123);
        """
    )
    conn.commit()
    conn.close()


def make_sonarr(path, root):
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE Series (Id INTEGER PRIMARY KEY, TmdbId INTEGER, Path TEXT);
        CREATE TABLE Episodes (Id INTEGER PRIMARY KEY, SeriesId INTEGER,
                               SeasonNumber INTEGER, EpisodeNumber INTEGER,
                               EpisodeFileId INTEGER);
        CREATE TABLE EpisodeFiles (Id INTEGER PRIMARY KEY, SeriesId INTEGER,
                                   RelativePath TEXT, Size INTEGER);
        INSERT INTO Series VALUES (1, 200, '{root}/Serie (2024)');
        INSERT INTO Episodes VALUES (1, 1, 1, 2, 1);
        INSERT INTO EpisodeFiles VALUES
            (1, 1, 'Season 1/Serie - S01E02 - Piloto.mkv', 456);
        """
    )
    conn.commit()
    conn.close()


def test_sync_library_rebuilds_paths(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    radarr = tmp_path / "radarr.db"
    sonarr = tmp_path / "sonarr.db"
    make_radarr(radarr, "/mnt/fichier/Peliculas")
    make_sonarr(sonarr, "/mnt/fichier/Series")

    report = sync_library(db, radarr_db=radarr, sonarr_db=sonarr)
    assert report["ok"] is True
    assert report["movies"] == 1
    assert report["episodes"] == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {
        (row["kind"], row["tmdb"], row["season"], row["episode"]): row
        for row in conn.execute("SELECT * FROM library_files")
    }
    assert rows[("movie", 100, 0, 0)]["path"] == (
        "/mnt/fichier/Peliculas/Peli (2024)/Peli (2024) WEBDL-1080p.mkv"
    )
    assert rows[("series", 200, 1, 2)]["path"] == (
        "/mnt/fichier/Series/Serie (2024)/Season 1/"
        "Serie - S01E02 - Piloto.mkv"
    )
    conn.close()
