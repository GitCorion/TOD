from catalogo.tmdb import TmdbClient, TmdbError, movie_item, year_of_date


def test_year_of_date():
    assert year_of_date("2026-09-19") == 2026
    assert year_of_date("") is None
    assert year_of_date("0000-01-01") is None


def test_movie_item_normalizes_spanish_title_and_original():
    item = movie_item(
        {
            "id": 1263337,
            "title": "En el corazón de la bestia",
            "original_title": "Heart of the Beast",
            "release_date": "2026-09-19",
            "original_language": "en",
            "popularity": 123.4,
        }
    )
    assert item == {
        "kind": "movie",
        "tmdb": 1263337,
        "title": "En el corazón de la bestia",
        "original_title": "Heart of the Beast",
        "year": 2026,
        "original_language": "en",
        "popularity": 123.4,
        "release_date": "2026-09-19",
        "overview": None,
        "poster_path": None,
        "backdrop_path": None,
        "vote_average": None,
    }


def test_movie_item_requires_tmdb_and_title():
    assert movie_item({"id": 0, "title": "Nada"}) is None
    assert movie_item({"id": 1, "title": ""}) is None


def test_client_requires_key():
    try:
        TmdbClient("")
    except TmdbError:
        pass
    else:
        raise AssertionError("debía fallar sin key")


def test_discover_movies_builds_year_params():
    class Fake(TmdbClient):
        def __init__(self):
            self.called = None

        def _get(self, path, **params):
            self.called = (path, params)
            return {"results": []}

    client = Fake()
    client.discover_movies(year=2026, min_votes=50, page=2)
    path, params = client.called
    assert path == "discover/movie"
    assert params["primary_release_date.gte"] == "2026-01-01"
    assert params["primary_release_date.lte"] == "2026-12-31"
    assert params["vote_count.gte"] == 50
    assert params["page"] == 2


def test_tv_item_and_episode_items():
    from catalogo.tmdb import episode_items, tv_item

    series = tv_item(
        {
            "id": 111110,
            "name": "ONE PIECE",
            "original_name": "ONE PIECE",
            "first_air_date": "2023-08-31",
            "original_language": "en",
            "popularity": 24.0,
        }
    )
    assert series["kind"] == "series"
    assert series["tmdb"] == 111110
    assert series["year"] == 2023

    episodes = episode_items(
        1,
        {
            "episodes": [
                {"episode_number": 1, "name": "Romance Dawn", "air_date": "2023-08-31"},
                {"episode_number": 5, "name": "Eat at Baratie!", "air_date": "2023-09-01"},
                {"episode_number": 0, "name": "Especial"},
            ]
        },
    )
    assert [ep["episode"] for ep in episodes] == [1, 5]
    assert all(ep["season"] == 1 for ep in episodes)
