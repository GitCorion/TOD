from catalogo.identity import (
    asset_key,
    normalize_key,
    parse_key,
    play_url,
)


def test_movie_tmdb_key():
    assert asset_key("movie", 9737) == ("movie:tmdb:9737", "ok")
    parsed = parse_key("movie:tmdb:9737")
    assert parsed.kind == "movie"
    assert parsed.scheme == "tmdb"
    assert parsed.value == "9737"


def test_series_key():
    key, status = asset_key("series", 2190, 29, 4)
    assert key == "series:tmdb:2190:29:4"
    assert status == "ok"
    parsed = parse_key(key)
    assert (parsed.season, parsed.episode) == (29, 4)


def test_legacy_keys_are_not_collapsed():
    assert asset_key("movie", 0, legacy_id=2) == ("movie:legacy:2", "needs_identity")
    assert asset_key("series", None, 1, 2, legacy_id=9) == (
        "series:legacy:9:1:2",
        "needs_identity",
    )


def test_normalize_old_keys():
    assert normalize_key("movie:9737") == "movie:tmdb:9737"
    assert normalize_key("series:2190:29:4") == "series:tmdb:2190:29:4"
    assert normalize_key("movie:0") is None
    assert normalize_key("basura") is None


def test_play_url_is_stable_and_encoded():
    assert play_url("movie:tmdb:9737", "http://127.0.0.1:8773") == (
        "http://127.0.0.1:8773/play?key=movie%3Atmdb%3A9737&download=0"
    )
