import sqlite3
from pathlib import Path

from catalogo.backfill import pick_year
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def seed(tmp_path, *, with_release_2019=True, library_2018=False):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    for year, tmdb in ((2019, 101), (2018, 102)):
        conn.execute(
            "INSERT INTO media_items(key,kind,tmdb,title,year,status) "
            "VALUES(?,?,?,?,?,?)",
            (f"movie:tmdb:{tmdb}", "movie", tmdb, f"Peli {year}", year,
             "COLD"),
        )
        asset_id = conn.execute(
            "SELECT id FROM media_items WHERE tmdb=?", (tmdb,)
        ).fetchone()[0]
        if year == 2019 and with_release_2019:
            conn.execute(
                "INSERT INTO releases(asset_id,tracker,title) VALUES(?,?,?)",
                (asset_id, "TrackerEspanol (API)", f"Peli {year}"),
            )
        if year == 2018 and library_2018:
            conn.execute(
                "INSERT INTO library_files(kind,tmdb,season,episode,path,"
                "size_bytes,source) VALUES(?,?,?,?,?,?,?)",
                ("movie", tmdb, 0, 0, "/mnt/fichier/Peliculas/x.mkv",
                 1, "radarr"),
            )
    conn.commit()
    conn.close()
    return db


def test_pick_year_skips_years_with_sources(tmp_path):
    db = seed(tmp_path)
    assert pick_year(db, [2019, 2018]) == 2018


def test_pick_year_skips_library_titles(tmp_path):
    db = seed(tmp_path, with_release_2019=True, library_2018=True)
    assert pick_year(db, [2019, 2018]) is None


def test_pick_year_returns_first_pending(tmp_path):
    db = seed(tmp_path, with_release_2019=False, library_2018=False)
    assert pick_year(db, [2019, 2018]) == 2019
