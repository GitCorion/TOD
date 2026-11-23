import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.strm import _available_release_ids, export_strm, safe_name

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
        "VALUES(?,?,?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test: Movie", 2020, None, None),
    )
    movie_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders,quality,audio) "
        "VALUES(?,?,?,?,?,?,?)",
        (movie_id, "TrackerEspanol2", "Test.Movie.1080p.mkv", "alive", 10,
         "1080p", "castellano"),
    )
    release_1080 = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders,quality,audio) "
        "VALUES(?,?,?,?,?,?,?)",
        (movie_id, "TrackerEspanol", "Test.Movie.2160p.mkv", "alive", 5,
         "4K", "spanish_unverified"),
    )
    release_4k = conn.execute(
        "SELECT id FROM releases ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year,season,episode) "
        "VALUES(?,?,?,?,?,?,?)",
        ("series:tmdb:2:1:3", "series", 2, "Serie", 2021, 1, 3),
    )
    series_id = conn.execute(
        "SELECT id FROM media_items WHERE key='series:tmdb:2:1:3'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (series_id, "TrackerEspanol2", "Serie.S01E03.mkv", "alive", 12),
    )
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:3", "movie", 3, "Nada", 2022),
    )
    conn.commit()
    conn.close()
    return db, release_1080, release_4k


def test_safe_name_removes_path_characters():
    assert safe_name('A/B:C*D? "E"') == "A B C D E"
    assert safe_name("") == "Sin titulo"


def test_local_release_with_zero_seeders_is_exported(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    media = tmp_path / "Local.mkv"
    media.write_bytes(b"x" * 1000)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:9", "movie", 9, "Local", 2024),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders,quality,audio) "
        "VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol (API)", "Local.mkv", "dead", 0, "1080p",
         "castellano"),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
        "size_bytes,state,storage_location,local_path) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, 0, "Local.mkv", 1000, "ready", "LOCAL_DISK",
         str(media)),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "strm"
    result = export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        key="movie:tmdb:9", require_health=True, min_seeders=5,
    )
    assert result["written"] == 1
    path = out / "movies" / "Local (2024)" / "Local (2024).strm"
    assert path.is_file()
    assert "download=0" in path.read_text()


def test_dead_release_without_local_file_is_not_published(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:10", "movie", 10, "Muerta", 2024),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (asset_id, "Tracker", "Muerta.mkv", "dead", 0),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "strm"
    result = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert result["written"] == 0
    assert not (out / "movies" / "Muerta (2024)").exists()


def test_local_file_without_release_is_exported(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    media = tmp_path / "Local.mkv"
    media.write_bytes(b"x" * 10)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:11", "movie", 11, "Sin release", 2024),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,file_index,rel_path,size_bytes,"
        "state,storage_location,local_path) VALUES(?,?,?,?,?,?,?)",
        (asset_id, 0, "Local.mkv", 10, "ready", "LOCAL_DISK", str(media)),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "strm"
    result = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert result["written"] == 1
    assert (
        out / "movies" / "Sin release (2024)" / "Sin release (2024).strm"
    ).is_file()


def test_export_multi_version_and_stable_default(tmp_path):
    db, release_1080, release_4k = seed(tmp_path)
    out = tmp_path / "strm"
    result = export_strm(
        db, out, base_url="http://127.0.0.1:8773", versions_filter="alive"
    )
    assert result["assets"] == 2
    assert result["written"] == 2
    assert result["versions_written"] == 2
    assert len(result["written_paths"]) == 4

    movie_dir = out / "movies" / "Test Movie (2020)"
    default = movie_dir / "Test Movie (2020).strm"
    assert default.read_text().strip() == (
        "http://127.0.0.1:8773/play?key=movie%3Atmdb%3A1&download=0"
    )
    v1080 = movie_dir / "Test Movie (2020) - 1080p Cast.strm"
    v4k = movie_dir / "Test Movie (2020) - 4K ESP.strm"
    assert v1080.read_text().strip().endswith(f"&release={release_1080}")
    assert v4k.read_text().strip().endswith(f"&release={release_4k}")

    assert (movie_dir / "Test Movie (2020).nfo").is_file()
    assert "tmdbid" in (movie_dir / "Test Movie (2020).nfo").read_text()
    episode = (
        out
        / "series"
        / "Serie (2021)"
        / "Season 01"
        / "Serie S01E03.strm"
    )
    assert episode.read_text().strip() == (
        "http://127.0.0.1:8773/play?key=series%3Atmdb%3A2%3A1%3A3&download=0"
    )
    tvshow = out / "series" / "Serie (2021)" / "tvshow.nfo"
    assert tvshow.is_file()
    assert "<tmdbid>2</tmdbid>" in tvshow.read_text()
    # Una sola versión: no se genera fichero de versión.
    assert len(list(episode.parent.glob("*.strm"))) == 1
    assert not (out / "movies" / "Nada (2022)").exists()

    again = export_strm(
        db, out, base_url="http://127.0.0.1:8773", versions_filter="alive"
    )
    assert again["written"] == 0
    assert again["versions_written"] == 0


def test_alive_releases_without_file_do_not_get_version_strm(tmp_path):
    db, _, _ = seed(tmp_path)
    out = tmp_path / "strm"
    result = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert result["written"] == 2
    assert result["versions_written"] == 0
    assert result["versions_filter"] == "available"

    movie_dir = out / "movies" / "Test Movie (2020)"
    files = sorted(path.name for path in movie_dir.glob("*.strm"))
    assert files == ["Test Movie (2020).strm"]


def test_prune_removes_stale_version_strm(tmp_path):
    db, release_1080, _ = seed(tmp_path)
    out = tmp_path / "strm"
    export_strm(
        db, out, base_url="http://127.0.0.1:8773", versions_filter="alive"
    )
    movie_dir = out / "movies" / "Test Movie (2020)"
    stale = movie_dir / "Test Movie (2020) - 4K ESP.strm"
    assert stale.is_file()

    media = tmp_path / "Test.Movie.1080p.mkv"
    media.write_bytes(b"x" * 10)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,rel_path,"
        "size_bytes,state,storage_location,local_path) "
        "VALUES((SELECT id FROM media_items WHERE key='movie:tmdb:1'),?,?,?,?,?,?,?)",
        (release_1080, 0, "Test.Movie.1080p.mkv", 10, "ready",
         "LOCAL_DISK", str(media)),
    )
    conn.commit()
    conn.close()

    result = export_strm(db, out, base_url="http://127.0.0.1:8773", prune=True)
    assert result["pruned"] >= 2
    assert not stale.exists()
    assert (movie_dir / "Test Movie (2020).strm").is_file()
    # Solo una versión disponible: no se genera fichero de versión.
    assert sorted(p.name for p in movie_dir.glob("*.strm")) == [
        "Test Movie (2020).strm"
    ]


def test_prune_rejects_partial_export(tmp_path):
    db, _, _ = seed(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        export_strm(
            db, tmp_path / "strm", base_url="http://127.0.0.1:8773",
            key="movie:tmdb:1", prune=True,
        )
    with pytest.raises(ValueError):
        export_strm(
            db, tmp_path / "strm", base_url="http://127.0.0.1:8773",
            limit=1, prune=True,
        )


def test_existing_only_ignores_new_assets_and_prunes(tmp_path):
    db, _, _ = seed(tmp_path)
    out = tmp_path / "strm"
    export_strm(
        db, out, base_url="http://127.0.0.1:8773", versions_filter="alive"
    )
    assert (out / "movies" / "Test Movie (2020)").is_dir()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:4", "movie", 4, "Nueva", 2025),
    )
    new_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:4'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (new_id, "TrackerEspanol2", "Nueva.mkv", "alive", 10),
    )
    conn.commit()
    conn.close()

    result = export_strm(
        db, out, base_url="http://127.0.0.1:8773",
        existing_only=True, prune=True,
    )
    assert result["assets"] == 2
    assert result["pruned"] >= 2
    assert not (out / "movies" / "Nueva (2025)").exists()


def test_export_filters_by_year(tmp_path):
    db, _, _ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:5", "movie", 5, "Otra", 2021),
    )
    new_id = conn.execute(
        "SELECT id FROM media_items WHERE key='movie:tmdb:5'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health,seeders) "
        "VALUES(?,?,?,?,?)",
        (new_id, "TrackerEspanol2", "Otra.mkv", "alive", 10),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "strm"
    result = export_strm(db, out, base_url="http://127.0.0.1:8773", year=2020)
    assert result["year"] == 2020
    assert (out / "movies" / "Test Movie (2020)").is_dir()
    assert not (out / "movies" / "Otra (2021)").exists()


def test_export_filters_by_kind(tmp_path):
    db, _, _ = seed(tmp_path)
    out = tmp_path / "strm"
    result = export_strm(
        db, out, base_url="http://127.0.0.1:8773", kind="series"
    )
    assert result["kind"] == "series"
    assert (out / "series" / "Serie (2021)").is_dir()
    assert not (out / "movies" / "Test Movie (2020)").exists()


def test_available_release_ids_trusts_qbit_state(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:7", "movie", 7, "Archivada", 2024),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    release_id = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,health) "
        "VALUES(?,?,?,?)",
        (asset_id, "Tracker", "Película.mkv", "alive"),
    ).lastrowid
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,library_path,"
        "size_bytes,state) VALUES(?,?,?,?,?,?)",
        (
            asset_id,
            release_id,
            0,
            "/mnt/fichier/Peliculas/Archivada (2024)/Archivada.mkv",
            1000,
            "archived",
        ),
    )
    conn.commit()
    assert _available_release_ids(conn) == {release_id}
    conn.close()


def test_export_writes_strm_for_library_only(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:42", "movie", 42, "En Radarr", 2024),
    )
    conn.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,source) "
        "VALUES('movie',42,0,0,?,?, 'radarr')",
        (str(tmp_path / "En Radarr.mkv"), 123),
    )
    conn.commit()
    conn.close()
    out = tmp_path / "strm"
    report = export_strm(db, out, base_url="http://127.0.0.1:8773")
    assert report["written"] == 1
    assert list(out.rglob("*.strm"))


def test_blocked_tracker_releases_are_not_published(tmp_path):
    from catalogo.policy import TrackerPolicy, upsert_policy

    db, *_ = seed(tmp_path)
    upsert_policy(
        str(db), TrackerPolicy("TrackerEspanol2", privacy="public", enabled=False)
    )
    upsert_policy(
        str(db), TrackerPolicy("TrackerEspanol", privacy="public", enabled=False)
    )
    out = tmp_path / "library"
    report = export_strm(str(db), out, base_url="http://tod:8773")
    assert report["written"] == 0
    assert not list(out.rglob("*.strm")) if out.exists() else True
