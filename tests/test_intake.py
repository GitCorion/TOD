import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from catalogo import intake
from catalogo.db import init_db
from catalogo.title_sync import upsert_movies

MIGRATIONS = Path(__file__).resolve().parents[1] / "catalogo" / "migrations"


class FakeTmdb:
    def list_details(self, list_id, *, page=1):
        return {
            "id": int(list_id),
            "name": "Christmas",
            "total_pages": 1,
            "items": [
                {"id": 550, "title": "Pelicula", "media_type": "movie"},
                {"id": 1396, "name": "Serie", "media_type": "tv"},
            ],
        }

    def movie_details(self, tmdb):
        if int(tmdb) == 404:
            raise RuntimeError("TMDB 404")
        return {
            "id": int(tmdb),
            "title": f"Película {tmdb}",
            "original_title": f"Movie {tmdb}",
            "release_date": "2024-03-01",
            "poster_path": f"/m{tmdb}.jpg",
            "popularity": 10,
        }

    def tv_details(self, tmdb):
        return {
            "id": int(tmdb),
            "name": f"Serie {tmdb}",
            "original_name": f"Show {tmdb}",
            "first_air_date": "2020-01-01",
            "poster_path": f"/s{tmdb}.jpg",
            "popularity": 8,
            "seasons": [
                {"season_number": 1, "episode_count": 2},
                {"season_number": 2, "episode_count": 1},
            ],
        }

    def season_details(self, tmdb, season):
        if int(season) == 2:
            return {
                "episodes": [
                    {"episode_number": 1, "name": "Dos uno", "air_date": "2021-01-01"}
                ]
            }
        return {
            "episodes": [
                {"episode_number": 1, "name": "Uno", "air_date": "2020-01-01"},
                {"episode_number": 2, "name": "Dos", "air_date": "2020-01-02"},
            ]
        }


def seed_db(tmp_path):
    db = tmp_path / "catalogo.db"
    init_db(db, MIGRATIONS)
    return db


def config_for(db):
    return SimpleNamespace(db_path=db)


def test_parse_entries_accepts_urls_keys_csv_and_dedupe():
    text = """
    # comentario
    https://www.themoviedb.org/movie/603
    https://www.themoviedb.org/es/tv/1396/season/1/episode/2
    movie:tmdb:603
    series:tmdb:1396:1
    550
    basura
    """
    entries = intake.parse_entries(text, kind="movie")
    assert [entry.kind for entry in entries] == [
        "movie",
        "series",
        "movie",
        "series",
        "movie",
        "unknown",
    ]
    assert entries[0].tmdb == 603
    assert entries[1].season == 1 and entries[1].episode == 2
    assert entries[3].season == 1 and entries[3].episode is None
    assert entries[4].tmdb == 550
    assert entries[5].reason

    deduped = intake._dedupe(entries)
    assert deduped[2].repeated is True
    assert deduped[2].reason == "Repetido en la lista"


def test_parse_tmdb_list_url():
    entries = intake.parse_entries(
        "https://www.themoviedb.org/list/42936-christmas-list"
    )
    assert len(entries) == 1
    assert entries[0].kind == "list"
    assert entries[0].list_id == 42936


def test_generic_url_reason_is_clear():
    entries = intake.parse_entries("https://example.com/algo")
    assert entries[0].kind == "unknown"
    assert "puerta" not in (entries[0].reason or "").lower()
    assert "TMDB" in (entries[0].reason or "")


def test_parse_entries_csv():
    text = "tmdb,kind,season,episode\n603,movie,,\n1396,series,1,2\n"
    entries = intake.parse_entries(text)
    assert len(entries) == 2
    assert entries[0].kind == "movie" and entries[0].tmdb == 603
    assert entries[1].kind == "series"
    assert entries[1].season == 1 and entries[1].episode == 2


def test_preflight_marks_ready_duplicates_range_and_unknown(tmp_path):
    db = seed_db(tmp_path)
    upsert_movies(
        db,
        [{"tmdb": 603, "title": "Ya está", "year": 1999}],
        origin="radarr",
    )
    text = """
    https://www.themoviedb.org/movie/603
    https://www.themoviedb.org/movie/550
    https://www.themoviedb.org/tv/1396
    https://www.themoviedb.org/tv/1396/season/1/episode/2
    https://www.themoviedb.org/movie/404
    basura
    """
    data = intake.preflight(config_for(db), text, client=FakeTmdb())
    summary = data["summary"]
    assert summary["total"] == 6
    assert summary["duplicate"] == 1
    assert summary["ready"] == 2
    assert summary["needs_range"] == 1
    assert summary["unknown"] == 1
    assert summary["error"] == 1

    states = [row["state"] for row in data["items"]]
    assert states == [
        "duplicate",
        "ready",
        "needs_range",
        "ready",
        "error",
        "unknown",
    ]
    assert data["items"][2]["href"] == "#/series/1396"
    assert data["items"][3]["episode"] == 2


def test_preflight_detects_complete_season(tmp_path):
    db = seed_db(tmp_path)
    from catalogo.title_sync import upsert_episodes

    upsert_episodes(
        db,
        {"tmdb": 1396, "title": "Serie 1396", "year": 2020},
        [
            {"season": 1, "episode": 1, "name": "Uno"},
            {"season": 1, "episode": 2, "name": "Dos"},
        ],
        origin="import",
    )
    data = intake.preflight(
        config_for(db),
        "https://www.themoviedb.org/tv/1396/season/1",
        client=FakeTmdb(),
    )
    assert data["items"][0]["state"] == "duplicate"
    assert "temporada" in data["items"][0]["reason"].lower()


def test_confirm_persists_batch_items_and_origin(tmp_path, monkeypatch):
    db = seed_db(tmp_path)

    def fake_add_movie(config, tmdb, *, origin=None):
        report = upsert_movies(
            config.db_path,
            [{"tmdb": int(tmdb), "title": f"Película {tmdb}", "year": 2024}],
            origin=origin or "tmdb",
        )
        conn = sqlite3.connect(config.db_path)
        row = conn.execute(
            "SELECT id FROM media_items WHERE key = ?", (f"movie:tmdb:{tmdb}",)
        ).fetchone()
        conn.close()
        return {
            "key": f"movie:tmdb:{tmdb}",
            "asset_id": row[0] if row else None,
            **report,
        }

    monkeypatch.setattr(intake.admin, "add_movie", fake_add_movie)
    text = """
    https://www.themoviedb.org/movie/550
    https://www.themoviedb.org/tv/1396/season/1/episode/2
    basura
    """
    result = intake.confirm(
        config_for(db), text, client=FakeTmdb(), requested_by="127.0.0.1"
    )
    assert result["added"] == 2
    assert result["unknown"] == 1
    assert result["state"] == "done"
    assert len(result["items"]) == 3
    assert result["items"][0]["detail"] == "En el catálogo"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    origin = conn.execute(
        "SELECT origin, origin_detail FROM media_items WHERE key='movie:tmdb:550'"
    ).fetchone()
    assert origin["origin"] == "import"
    assert origin["origin_detail"] == f"Lote #{result['id']}"
    episode = conn.execute(
        "SELECT title, season, episode FROM media_items "
        "WHERE key='series:tmdb:1396:1:2'"
    ).fetchone()
    assert dict(episode) == {"title": "Serie 1396", "season": 1, "episode": 2}
    conn.close()


def test_reception_includes_automatic_flows(tmp_path):
    db = seed_db(tmp_path)
    result = {
        "mirror": {
            "radarr": {"items": 2},
            "sonarr": {"episodes": 3},
        },
        "written": 4,
    }
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs(kind, state, result) VALUES('arr-mirror', 'ok', ?)",
        (json.dumps(result),),
    )
    conn.commit()
    conn.close()

    data = intake.reception(db)
    assert data["today"]["batches"] == 0
    flows = {flow["kind"]: flow for flow in data["flows"]}
    assert flows["arr-mirror"]["summary"] == "5 títulos espejados · 4 .strm"
    assert flows["rss-sync"]["summary"] == "Sin ejecutar"


def test_confirm_expands_tmdb_list(tmp_path, monkeypatch):
    db = seed_db(tmp_path)

    def fake_add_movie(config, tmdb, *, origin=None):
        report = upsert_movies(
            config.db_path,
            [{"tmdb": int(tmdb), "title": f"Película {tmdb}", "year": 2024}],
            origin=origin or "tmdb",
        )
        conn = sqlite3.connect(config.db_path)
        row = conn.execute(
            "SELECT id FROM media_items WHERE key = ?", (f"movie:tmdb:{tmdb}",)
        ).fetchone()
        conn.close()
        return {
            "key": f"movie:tmdb:{tmdb}",
            "asset_id": row[0] if row else None,
            **report,
        }

    monkeypatch.setattr(intake.admin, "add_movie", fake_add_movie)
    result = intake.confirm(
        config_for(db),
        "https://www.themoviedb.org/list/42936-christmas-list",
        client=FakeTmdb(),
    )
    assert result["added"] == 3
    assert "Lista de TMDB · Christmas" == result["label"]
    assert result["total"] == 3
    keys = {item["key"] for item in result["items"] if item["key"]}
    assert "movie:tmdb:550" in keys
    assert "series:tmdb:1396:1" in keys
    assert "series:tmdb:1396:2" in keys
    conn = sqlite3.connect(db)
    episodes = conn.execute(
        "SELECT COUNT(*) FROM media_items WHERE key LIKE 'series:tmdb:1396:%'"
    ).fetchone()[0]
    conn.close()
    assert episodes == 3


def test_set_follow_toggles_monitored(tmp_path):
    db = seed_db(tmp_path)
    upsert_movies(
        db,
        [{"tmdb": 7, "title": "Siete", "year": 2024}],
        origin="import",
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT monitored FROM media_items WHERE key='movie:tmdb:7'"
        ).fetchone()[0] == 1
    intake.set_follow(db, ["movie:tmdb:7"], False)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT monitored FROM media_items WHERE key='movie:tmdb:7'"
        ).fetchone()[0] == 0
    intake.set_follow(db, ["movie:tmdb:7"], True)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT monitored FROM media_items WHERE key='movie:tmdb:7'"
        ).fetchone()[0] == 1


def test_add_torrent_records_batch(tmp_path, monkeypatch):
    db = seed_db(tmp_path)
    raw = b"d4:infod4:name4:Test6:lengthi10eee"
    called = {}

    class FakeQbit:
        @classmethod
        def from_config(cls, config):
            return cls()

        def add_torrent(self, torrent_bytes, **kwargs):
            called["bytes"] = torrent_bytes
            called.update(kwargs)

    monkeypatch.setattr(
        "catalogo.qbit_write.QbittorrentWriteClient", FakeQbit
    )
    config = SimpleNamespace(
        db_path=db,
        ondemand_save_path=tmp_path / "ondemand",
    )
    result = intake.add_torrent(
        config, filename="test.torrent", raw=raw
    )
    assert result["name"] == "Test"
    assert called["category"] == "ondemand"
    assert called["bytes"] == raw
    detail = intake.batch_detail(db, result["batch_id"])
    assert detail["added"] == 1
    assert detail["items"][0]["kind"] == "torrent"
    assert detail["items"][0]["title"] == "Test"


def test_origins_counts_catalog(tmp_path):
    db = seed_db(tmp_path)
    upsert_movies(
        db,
        [{"tmdb": 1, "title": "Uno", "year": 2024}],
        origin="radarr",
    )
    upsert_movies(
        db,
        [{"tmdb": 2, "title": "Dos", "year": 2024}],
        origin="import",
    )
    totals = {row["origin"]: row["total"] for row in intake.origins(db)}
    assert totals["radarr"] == 1
    assert totals["import"] == 1
