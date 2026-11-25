"""Tests de NFO enriquecido — Fase 3."""

from catalogo.strm import _nfo


def test_nfo_basic_movie():
    asset = {"kind": "movie", "title": "Dune", "year": 2021, "tmdb": 438631,
             "key": "movie:tmdb:438631"}
    result = _nfo(asset)
    assert result is not None
    path, content = result
    assert "<movie>" in content
    assert "<title>Dune</title>" in content
    assert "<tmdbid>438631</tmdbid>" in content
    assert "<genre>" not in content  # No rich by default


def test_nfo_rich_movie():
    asset = {"kind": "movie", "title": "Dune", "year": 2021, "tmdb": 438631,
             "key": "movie:tmdb:438631",
             "imdb_id": "tt1160419", "genres": "Sci-Fi,Action",
             "overview": "Paul Atreides viaja a Arrakis.",
             "runtime_seconds": 9360, "vote_average": 7.8,
             "certification": "PG-13"}
    result = _nfo(asset, rich=True)
    assert result is not None
    _, content = result
    assert '<uniqueid type="imdb">tt1160419</uniqueid>' in content
    assert "<genre>Sci-Fi</genre>" in content
    assert "<genre>Action</genre>" in content
    assert "<plot>Paul Atreides" in content
    assert "<runtime>156</runtime>" in content
    assert "<rating>7.8</rating>" in content
    assert "<mpaa>PG-13</mpaa>" in content


def test_nfo_rich_series():
    asset = {"kind": "series", "title": "Breaking Bad", "year": 2008,
             "tmdb": 1396, "season": 1, "episode": 1,
             "key": "series:tmdb:1396:1:1",
             "genres": "Drama,Crime", "imdb_id": "tt0903747",
             "overview": "Un profesor de quimica...",
             "tvdb_id": 81189}
    result = _nfo(asset, rich=True)
    assert result is not None
    _, content = result
    assert "<tvshow>" in content
    assert "<genre>Drama</genre>" in content
    assert "<genre>Crime</genre>" in content
    assert '<uniqueid type="imdb">tt0903747</uniqueid>' in content


def test_nfo_rich_no_data():
    """Con rich=True pero sin datos extra, no falla."""
    asset = {"kind": "movie", "title": "Test", "year": 2024, "tmdb": 1,
             "key": "movie:tmdb:1"}
    result = _nfo(asset, rich=True)
    assert result is not None
    _, content = result
    assert "<movie>" in content
    assert "<genre>" not in content
