import sqlite3
import urllib.parse
from pathlib import Path

from catalogo import ondemand
from catalogo.config import Config
from catalogo.db import init_db

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def config_for(tmp_path, db):
    return Config(
        db_path=db,
        legacy_db=db,
        library_root=tmp_path / "fichier",
        seed_root=tmp_path / "descargas",
        migrations_dir=MIGRATIONS,
        prowlarr_url="http://127.0.0.1:1/api/v1",
        prowlarr_api_key=None,
        qbittorrent_url="http://127.0.0.1:1",
        qbittorrent_user="x",
        qbittorrent_pass=None,
        jackett_url="http://127.0.0.1:1/api/v2.0",
        jackett_api_key=None,
        rd_api_key=None,
        max_stream_bytes=0,
        tmdb_api_key=None,
        catalog_library=tmp_path / "catalogo",
        max_active_ondemand=4,
    )


class FakeQbit:
    def __init__(self, torrents):
        self._torrents = torrents

    def torrents(self, category=None):
        if category and category != "ondemand":
            return []
        return list(self._torrents)


def _strm(root: Path, item: dict, key: str) -> None:
    title = item["title"]
    year = item["year"]
    if item["kind"] == "movie":
        path = root / "movies" / f"{title} ({year})" / f"{title} ({year}).strm"
    else:
        path = (
            root
            / "series"
            / f"{title} ({year})"
            / f"Season {item['season']:02d}"
            / f"{title} S{item['season']:02d}E{item['episode']:02d}.strm"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "http://127.0.0.1:8773/play?key="
        + urllib.parse.quote(key, safe="")
        + "&download=0",
        encoding="utf-8",
    )


def seed(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    root = tmp_path / "catalogo"
    conn = sqlite3.connect(db)

    def media(key, kind, tmdb, title, year, season=None, episode=None):
        cur = conn.execute(
            "INSERT INTO media_items(key,media_id,kind,tmdb,title,year,season,episode,"
            "popularity) VALUES(?,?,?,?,?,?,?,?,?)",
            (key, key, kind, tmdb, title, year, season, episode, 10),
        )
        return int(cur.lastrowid)

    def release(asset_id, tracker="Tracker"):
        cur = conn.execute(
            "INSERT INTO releases(asset_id,tracker,title,size_bytes,health,source,privacy) "
            "VALUES(?,?,?,?,?,?,?)",
            (asset_id, tracker, "Release", 1000, "alive", "prowlarr", "private"),
        )
        return int(cur.lastrowid)

    movie_pub = media("movie:tmdb:1", "movie", 1, "Publicada", 2024)
    movie_pending = media("movie:tmdb:2", "movie", 2, "Pendiente", 2024)
    movie_none = media("movie:tmdb:3", "movie", 3, "Sin fuente", 2024)
    series_pub = media("series:tmdb:10:1:1", "series", 10, "Serie A", 2020, 1, 1)
    series_a2 = media("series:tmdb:10:1:2", "series", 10, "Serie A", 2020, 1, 2)
    series_pending = media("series:tmdb:20:1:1", "series", 20, "Serie B", 2021, 1, 1)
    series_none = media("series:tmdb:30:1:1", "series", 30, "Serie C", 2022, 1, 1)

    _strm(root, {"kind": "movie", "title": "Publicada", "year": 2024}, "movie:tmdb:1")
    _strm(
        root,
        {
            "kind": "series", "title": "Serie A", "year": 2020,
            "season": 1, "episode": 1,
        },
        "series:tmdb:10:1:1",
    )
    release(movie_pub)
    release(movie_pending)
    release(series_a2)
    release(series_pending)

    conn.execute(
        "INSERT INTO torrents(info_hash,release_id,tracker,state,category,progress,"
        "qbit_state) VALUES(?,?,?,?,?,?,?)",
        ("a" * 40, 1, "Tracker", "downloading", "ondemand", 0.4, "downloading"),
    )
    torrent_id = conn.execute("SELECT id FROM torrents").fetchone()[0]
    conn.execute(
        "INSERT INTO torrent_files(asset_id,release_id,torrent_id,file_index,rel_path,"
        "size_bytes,state,storage_location) VALUES(?,?,?,?,?,?,?,?)",
        (movie_pending, 1, torrent_id, 0, "file.mkv", 1000, "downloading", "LOCAL_DISK"),
    )
    conn.commit()
    conn.close()

    qbit = FakeQbit([
        {"hash": "a" * 40, "state": "downloading", "progress": 0.4},
        {"hash": "b" * 40, "state": "forcedUP", "progress": 1.0},
        {"hash": "c" * 40, "state": "stoppedDL", "progress": 0.0},
    ])
    return db, root, movie_pub, movie_pending, qbit


def test_ondemand_overview_and_states(tmp_path):
    db, root, movie_pub, movie_pending, qbit = seed(tmp_path)
    config = config_for(tmp_path, db)
    data = ondemand.overview(db, config, qbit=qbit, arr=None)
    assert data["catalog"]["movies"]["published"] == 1
    assert data["catalog"]["movies"]["pending"] == 1
    assert data["catalog"]["movies"]["no_source"] == 1
    assert data["catalog"]["series"]["published"] == 1
    assert data["catalog"]["series"]["pending"] == 1
    assert data["catalog"]["series"]["no_source"] == 1
    assert data["catalog"]["episodes"] == 1
    assert data["downloads"] == {
        "total": 3,
        "downloading": 1,
        "seeding": 1,
        "paused": 1,
        "error": 0,
        "other": 0,
    }
    assert data["limit"] == 4


def test_ondemand_catalog_filters(tmp_path):
    db, root, movie_pub, movie_pending, qbit = seed(tmp_path)
    config = config_for(tmp_path, db)
    published = ondemand.catalog(
        db, config, qbit=qbit, arr=None, publication="publicado"
    )
    assert {item["title"] for item in published["items"]} == {
        "Publicada", "Serie A"
    }
    downloading = ondemand.catalog(
        db, config, qbit=qbit, arr=None, download="descargando"
    )
    assert [item["title"] for item in downloading["items"]] == ["Pendiente"]
    item = ondemand.catalog(
        db, config, qbit=qbit, arr=None, q="Publicada"
    )["items"][0]
    assert item["publication"] == "publicado"
    assert item["download"] == "sin_descarga"


def test_item_state_reports_publication(tmp_path):
    db, root, movie_pub, _, qbit = seed(tmp_path)
    config = config_for(tmp_path, db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    item = dict(conn.execute(
        "SELECT * FROM media_items WHERE id = ?", (movie_pub,)
    ).fetchone())
    conn.close()
    state = ondemand.item_state(db, config, item, qbit=qbit, arr=None)
    assert state["publication"] == "publicado"
    assert state["published"] is True
