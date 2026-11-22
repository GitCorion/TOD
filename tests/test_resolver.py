import sqlite3
from pathlib import Path

from catalogo.db import init_db
from catalogo.resolver import Resolver

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    library = tmp_path / "library.mkv"
    library.write_bytes(b"L" * 1000)
    seed_file = tmp_path / "seed.mkv"
    seed_file.write_bytes(b"S" * 2000)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:1", "movie", 1, "Test", 2020),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,quality,audio,size_bytes,"
        "health,seeders) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "Test.1080p.mkv", "1080p", "castellano",
         2000, "alive", 10),
    )
    release_id = conn.execute("SELECT id FROM releases").fetchone()[0]
    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,state) VALUES(?,?,?)",
        ("hash1", release_id, "seeding"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,local_path,"
        "library_path,size_bytes,state) VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, release_id, torrent_id, 0, str(seed_file), None, 2000, "ready"),
    )
    seed_file_id = conn.execute("SELECT id FROM torrent_files").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,file_index,library_path,size_bytes,"
        "state) VALUES(?,?,?,?,?,?)",
        (asset_id, release_id, None, str(library), 1000, "archived"),
    )
    library_file_id = conn.execute(
        "SELECT id FROM torrent_files WHERE library_path IS NOT NULL"
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return db, asset_id, release_id, seed_file, library, seed_file_id, library_file_id


def test_auto_prefers_local_seed_then_library(tmp_path):
    db, *_ , seed_file, library, seed_file_id, library_file_id = seed(tmp_path)
    resolver = Resolver(db)
    result = resolver.resolve("movie:tmdb:1")
    assert result.source == "local_seed"
    assert result.path == str(seed_file)
    assert result.file_id == seed_file_id

    # Si el seed local desaparece, cae a la biblioteca sin cambiar la clave.
    seed_file.unlink()
    result = resolver.resolve("movie:tmdb:1")
    assert result.source == "library"
    assert result.path == str(library)
    assert result.file_id == library_file_id


def test_pointer_is_mutable_and_url_key_is_stable(tmp_path):
    db, _asset_id, release_id, seed_file, library, seed_file_id, library_file_id = seed(tmp_path)
    resolver = Resolver(db)
    key = "movie:tmdb:1"

    resolver.set_target(key, "library", file_id=library_file_id)
    result = resolver.resolve(key)
    assert result.source == "library"
    assert result.path == str(library)
    assert result.key == key

    resolver.set_target(key, "local_seed", file_id=seed_file_id)
    result = resolver.resolve(key)
    assert result.source == "local_seed"
    assert result.path == str(seed_file)
    assert result.key == key

    resolver.set_target(key, "unavailable", note="mantenimiento")
    result = resolver.resolve(key)
    assert result.source == "unavailable"
    assert result.reason == "mantenimiento"

    resolver.set_target(key, "torrent", release_id=release_id)
    result = resolver.resolve(key)
    assert result.source == "torrent"
    assert result.release_id == release_id


def test_unknown_key_is_unavailable(tmp_path):
    db, *_ = seed(tmp_path)
    resolver = Resolver(db)
    result = resolver.resolve("movie:tmdb:999")
    assert result.source == "unavailable"
    assert result.reason == "asset_not_found"


def test_active_file_wins_over_legacy_pointer(tmp_path):
    db, _asset_id, _release_id, _seed_file, library, _seed_id, library_id = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE media_items SET active_file_id=?", (library_id,)
    )
    conn.execute(
        "UPDATE play_targets SET source_kind='torrent', file_id=NULL"
    )
    conn.commit()
    conn.close()
    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "library"
    assert result.reason == "active_file"
    assert result.path == str(library)


def test_debrid_release_is_preferred(tmp_path):
    db, _asset_id, _release_id, *_ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE releases SET debrid_cached=1, debrid_link='https://rd/direct.mkv'"
    )
    conn.execute("DELETE FROM torrent_files")
    conn.execute("UPDATE media_items SET active_file_id=NULL")
    conn.commit()
    conn.close()
    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "debrid"
    assert result.debrid_url == "https://rd/direct.mkv"
    assert result.public()["has_debrid"] is True
    assert "debrid_url" not in result.public()


def test_sources_and_release_selection(tmp_path):
    db, _asset_id, release_id, seed_file, _library, _seed_id, _lib_id = seed(tmp_path)
    resolver = Resolver(db)
    sources = resolver.sources("movie:tmdb:1")
    assert len(sources) == 1
    assert sources[0]["release_id"] == release_id
    assert "download_url" not in sources[0]

    chosen = resolver.resolve_release("movie:tmdb:1", release_id)
    assert chosen.source in ("library", "local_seed")
    assert chosen.reason == "requested_release"
    assert chosen.release_id == release_id
    assert chosen.path is not None


def test_sources_marks_too_large(tmp_path):
    db, _asset_id, _release_id, *_ = seed(tmp_path)
    resolver = Resolver(db, max_size_bytes=500)
    sources = resolver.sources("movie:tmdb:1")
    assert sources[0]["too_large"] is True


def test_release_order_prefers_seeders_over_size(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO media_items(key,kind,tmdb,title,year) VALUES(?,?,?,?,?)",
        ("movie:tmdb:2", "movie", 2, "Tamaño", 2026),
    )
    asset_id = conn.execute("SELECT id FROM media_items").fetchone()[0]
    conn.executemany(
        "INSERT INTO releases(asset_id,tracker,title,quality,audio,size_bytes,"
        "health,seeders) VALUES(?,?,?,?,?,?,?,?)",
        [
            (asset_id, "TrackerEspanol (API)", "Peli.2160p.mkv", "4K", "castellano",
             100 * 1024**2, "alive", 5),
            (asset_id, "TrackerEspanol2 (API)", "Peli.1080p.mkv", "1080p",
             "castellano", 2 * 1024**3, "alive", 50),
        ],
    )
    conn.commit()
    conn.close()

    resolver = Resolver(db)
    sources = resolver.sources("movie:tmdb:2")
    assert [row["quality"] for row in sources] == ["1080p", "4K"]
    assert [row["size_bytes"] for row in sources] == [
        2 * 1024**3,
        100 * 1024**2,
    ]
    chosen = resolver.resolve("movie:tmdb:2")
    assert chosen.source == "torrent"
    assert chosen.size == 2 * 1024**3


def test_library_file_is_preferred(tmp_path):
    db, asset_id, *_ = seed(tmp_path)
    library = tmp_path / "biblioteca.mkv"
    library.write_bytes(b"B" * 10)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,"
        "source) VALUES(?,?,?,?,?,?,?)",
        ("movie", 1, 0, 0, str(library), 10, "radarr"),
    )
    conn.commit()
    conn.close()

    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "library"
    assert result.reason == "library_file"
    assert result.path == str(library)
    assert result.size == 10


def test_library_file_is_trusted_from_arr_without_stat(tmp_path, monkeypatch):
    db, _asset_id, *_ = seed(tmp_path)
    missing = "/mnt/fichier/Peliculas/No existe (2020)/No existe.mkv"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO library_files(kind,tmdb,season,episode,path,size_bytes,"
        "source) VALUES(?,?,?,?,?,?,?)",
        ("movie", 1, 0, 0, missing, 4242, "radarr"),
    )
    conn.commit()
    conn.close()

    def fail(*_args, **_kwargs):
        raise AssertionError("no debe tocar el disco al resolver biblioteca")

    monkeypatch.setattr(Path, "is_file", fail)
    monkeypatch.setattr(Path, "stat", fail)
    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "library"
    assert result.path == missing
    assert result.size == 4242


def test_version_resolution_follows_moved_library_path(tmp_path):
    db, _asset_id, release_id, seed_file, _library, *_ = seed(tmp_path)
    seed_file.unlink()
    resolver = Resolver(db)
    first = resolver.resolve_release("movie:tmdb:1", release_id)
    assert first.source == "library"

    moved = "/mnt/fichier/Peliculas/Test (2020)/Test movida.mkv"
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE torrent_files SET library_path=? WHERE release_id=?",
        (moved, release_id),
    )
    conn.commit()
    conn.close()

    second = resolver.resolve_release("movie:tmdb:1", release_id)
    assert second.source == "library"
    assert second.path == moved


def test_partial_local_file_is_torrent_not_local_seed(tmp_path):
    db, _asset_id, _release_id, *_ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE torrents SET state='downloading', progress=0.01")
    conn.execute(
        "UPDATE torrent_files SET state='downloading' "
        "WHERE local_path IS NOT NULL"
    )
    conn.execute(
        "UPDATE torrent_files SET state='missing' "
        "WHERE library_path IS NOT NULL"
    )
    conn.commit()
    conn.close()
    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "torrent"


def test_imported_target_does_not_override_smallest_alive(tmp_path):
    db, asset_id, _release_id, _seed_file, _library, *_ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO play_targets(asset_id, source_kind, note) VALUES(?,?,?)",
        (asset_id, "library", "imported"),
    )
    conn.commit()
    conn.close()
    result = Resolver(db).resolve("movie:tmdb:1")
    assert result.source == "local_seed"
    assert result.reason == "auto_local_seed"


def test_sources_order_by_size_without_hardcoded_tracker_priority(tmp_path):
    db, asset_id, tracker2_id, *_ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM torrent_files")
    conn.execute("DELETE FROM play_targets")
    conn.execute("UPDATE media_items SET active_file_id=NULL")
    small_id = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,quality,size_bytes,"
        "health,seeders) VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerA", "Small.mkv", "1080p", 500, "alive", 99),
    ).lastrowid
    smaller_id = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,quality,size_bytes,"
        "health,seeders) VALUES(?,?,?,?,?,?,?)",
        (asset_id, "TrackerB", "Smaller.mkv", "1080p", 400, "alive", 99),
    ).lastrowid
    conn.commit()
    conn.close()
    from catalogo import language_settings

    language_settings.save(str(db), "any")
    resolver = Resolver(db)
    sources = resolver.sources("movie:tmdb:1")
    # Sin preferencia de idioma ni listas locales, manda el tamaño.
    assert sources[0]["release_id"] == smaller_id
    assert small_id in {item["release_id"] for item in sources}
    assert tracker2_id in {item["release_id"] for item in sources}
    assert resolver.resolve("movie:tmdb:1").release_id == smaller_id


def test_swarm_failures_deprioritize_release(tmp_path):
    db, asset_id, clean_id, *_ = seed(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM torrent_files")
    conn.execute("DELETE FROM play_targets")
    conn.execute("UPDATE media_items SET active_file_id=NULL")
    conn.execute(
        "UPDATE releases SET size_bytes=2000, health='unknown' WHERE id=?",
        (clean_id,),
    )
    failed_id = conn.execute(
        "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,"
        "seeders) VALUES(?,?,?,?,?,?)",
        (asset_id, "TrackerEspanol2", "SmallFailed.mkv", 500, "alive", 99),
    ).lastrowid
    conn.execute(
        "INSERT INTO swarm_stats(release_id,tracker,started_ok,failures) "
        "VALUES(?,?,?,?)",
        (failed_id, "TrackerEspanol2", 0, 2),
    )
    conn.commit()
    conn.close()
    resolver = Resolver(db)
    sources = resolver.sources("movie:tmdb:1")
    assert sources[0]["release_id"] == clean_id
    assert resolver.resolve("movie:tmdb:1").release_id == clean_id
